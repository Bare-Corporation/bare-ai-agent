#!/usr/bin/env python3
"""taskbus-poller.py - Task-Bus Poller / Watchdog for bare-ai-brain@100.64.0.4

Implements docs-taskbus-poller-scope-2026-09-12.md.

DESIGN INVARIANTS (from the scope, non-negotiable):
  * DRY-RUN IS THE DEFAULT. Starting work requires --live.
  * Reconciliation runs BEFORE intake, so a wedged task never blocks its dependants.
  * Cancelled is terminal and always wins, at every stage.
  * Completed is FAIL-CLOSED, and is decided by DISPATCHER-SIDE INDEPENDENT
    VERIFICATION (Option C). The dispatcher evaluates a machine-readable
    success_criterion on the target node; only if every check passes does it write
    verification_result=Passed and verified_by=<dispatcher>. An executor never
    self-reports success. A criterion that cannot be machine-checked goes to
    HumanReviewNeeded for a manual override (Option B), which is then honoured.
  * The poller holds NO in-memory truth across cycles. The table is the truth.
  * Every row is read IN FULL before it is acted on.

CREDENTIAL: this poller's OWN Baserow identity, poller@bare-ai.net, whose password is read
from Vault at the moment of use (see POLLER_SECRET_PATH). The password and the resulting JWT
are never logged and never written to disk. The shared admin@bare-erp.com login is NOT used,
so every row this poller touches is attributable to the poller and not to a human.
"""

import argparse
import fcntl
import json
import os
import re
import shlex
import ssl
import subprocess
import sys
import time
import uuid as uuidlib
import urllib.error
import urllib.request
from datetime import datetime, timezone

# ---------------------------------------------------------------- configuration

HOME = os.path.expanduser("~")
ENVF = os.path.join(HOME, ".bare-ai", "taskbus.env")
JOB_DIR = os.path.join(HOME, ".bare-ai", "jobs")
DISPATCHER = os.path.join(HOME, ".bare-ai", "lib", "dispatch-job.sh")
EXEC_CONF = os.path.join(HOME, ".bare-ai", "taskbus-exec.conf")
KILL_SWITCH = os.path.join(HOME, ".bare-ai", "taskbus.PAUSED")
STATE_FILE = os.path.join(HOME, ".bare-ai", "taskbus-poller.state.json")
LOCK_FILE = os.path.join(HOME, ".bare-ai", "taskbus-poller.lock")
LOG_FILE = os.path.join(HOME, ".bare-ai", "logs", "taskbus-poller.log")

TABLE = 215
# Own identity, overridable per node (2026-10-26). A leaf poller must know WHICH host
# it is, or it treats itself as remote, attempts an ssh it cannot make, and stamps
# verified_by with another machine's name. Default preserves the original value.
ME = os.environ.get("POLLER_SELF_NODE", "").strip() or "bare-ai-brain@100.64.0.4"

# ------------------------------------------------- CROSS-MACHINE DISPATCH (.4 is the brain)
# Liege design 2026-09-13: .4 READS the table and DISPATCHES; it does not do the work itself.
# assigned_to_machine names the TARGET, not the consumer. So .4 claims rows for any machine
# it can reach and sends the job over SSH, then polls that machine's job state next cycle.
SSH_USER = "bare-ai"
REMOTE_JOBS = "/home/bare-ai/.bare-ai/jobs"
# .4's own target-side agent runner, used for SELF-TARGET rows (see dispatch_self).
# It reads HOME at run time, so launched by the poller user it resolves to that
# user's identity and writes job state into JOB_DIR - the directory the local
# readers already consult. Executable but not writable by the poller user.
_SHARED_RUNNER = "/home/bare-ai/.bare-ai/lib/taskbus-agent-job.py"
_OWN_RUNNER = os.path.join(HOME, ".bare-ai", "lib", "taskbus-agent-job.py")
# Prefer the poller user's OWN copy when present. The shared copy lives in another
# user's home and is NOT writable by this user, so a runner fix could not be deployed
# without a password prompt (proven 2026-09-14: sudo cp asks for a password on .4).
# The detached-launch fd fix spans BOTH dispatch-job.sh (fd 0 pinned) and this runner
# (stdin=DEVNULL), so the runner must be deployable by the user that runs it.
# The BARE_TASKBUS_LOCAL_RUNNER env override still wins.
LOCAL_RUNNER = os.environ.get(
    "BARE_TASKBUS_LOCAL_RUNNER",
    _OWN_RUNNER if os.path.exists(_OWN_RUNNER) else _SHARED_RUNNER)
REMOTE_DISPATCHER = "/home/bare-ai/.bare-ai/lib/dispatch-job.sh"
REMOTE_RUNNER = "/home/bare-ai/.bare-ai/lib/taskbus-agent-job.py"

# Only machines .4 can actually dispatch to. .1 (council-ingress) is deliberately ABSENT:
# the role file records that .4 has no key for it, so .1-assigned rows are left alone
# rather than claimed and then failed. Proxmox-manager also lives on .4, so the map is by
# machine name -> node, not one row per node.
DISPATCHABLE = {
    "bare-ai-secrets-manager@100.64.0.2": "100.64.0.2",
    "bare-ai-brain@100.64.0.4": "100.64.0.4",
    "bare-ai-proxmox-manager@100.64.0.4": "100.64.0.4",
    "bare-ai-headscale-manager@100.64.0.5": "100.64.0.5",
    "bare-ai-developer@100.64.0.11": "100.64.0.11",
    "bare-ai-council-manager@100.64.0.13": "100.64.0.13",
    "bare-ai-bare-connectfi-manager@100.64.0.18": "100.64.0.18",
    "bare-ai-bare-ai.net-and-baserow-manager@100.64.0.19": "100.64.0.19",
}
SSH_OPTS = ["-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=8"]

# ---- SELF-TARGET DETECTION (added 2026-09-13) --------------------------------
# A row assigned to a machine that lives on THIS host keeps its job record in the
# LOCAL job dir. Reading it over SSH to ourselves is not merely wasteful, it is
# WRONG: ssh_node treats "command exited nonzero with empty stdout" as
# UNREACHABLE, and a cat of a missing job file exits 1 - so a definitively-absent
# record was misreported as a dead node. reconcile() then refused to remediate a
# stuck row and enforce_cancellations() refused to stop a Cancelled row's live
# job, logging "is UNREACHABLE" every cycle (observed on row 76). Local reads
# cannot be ambiguous, so for our own host we always read the local files.
SELF_HOST = ME.split("@")[-1] if "@" in ME else ME
LOCAL_NODES = {SELF_HOST, "localhost", "127.0.0.1", "::1", os.uname().nodename}


def is_local_node(node):
    """True when node addresses the host this poller runs on."""
    return bool(node) and node in LOCAL_NODES

LEASE_MINUTES = 90          # scope section 4 proposal: 1.5x longest observed job
DEFAULT_MAX_ATTEMPTS = 2    # scope section 4 proposal; row max_attempts wins if set
MAX_PER_CYCLE = 1           # cost cap: a status typo must not cause unbounded spend
# Daily cap, raised 6 -> 24 on liege approval 2026-09-13. Rationale: at ~9k prompt
# tokens and off-peak rates this is roughly $0.04/DAY, so cost is not the real limit -
# the real limit is human review, since every finished task lands in HumanReviewNeeded.
# 24/day is about 1/hour averaged: reviewable in one sitting, and 4x the old ceiling.
# Override per-run with --max-per-day; the systemd unit inherits this constant.
MAX_PER_DAY = 24            # cost cap (default; --max-per-day overrides)

# A task whose short_description carries this marker is TEST HARNESS data, not real
# work, so it must NOT consume the production daily budget. Observed: a day of fixture
# claims exhausted the cap and silently stopped all real work. Note the conservative
# choice - fixtures do not COUNT toward the cap, but they are still BLOCKED by it, so
# this cannot be used as a cap bypass.
FIXTURE_MARKERS = ("[FIXTURE",)
POLL_INTERVAL = 3600        # 60 minutes, per scope section 2

SATISFIED_STATES = ("Completed", "Closed")
ONLY_ROWS = set()   # empty = no filter
# Liege directive 2026-09-12 and row 5 ai_prompt ("Act ONLY on a New task, or on
# an InProgress task that looks stuck. Nothing else."): these are the only statuses
# the poller may act on. Enforced at ONE place, in cycle().
ACTABLE_STATUSES = ("New", "InProgress", "Retry", "Reopened")
# Liege 2026-09-13: Retry and Reopened behave like New - both are claimable.
# CASING: the liege first created these LOWERCASE, then re-cased them to Title case
# to match the rest. I re-read the field metadata each time rather than assuming,
# because a wrong case does not error - it silently matches nothing, and the poller
# would look like it was ignoring those rows.
CLAIMABLE_STATUSES = ("New", "Retry", "Reopened")

# ------------------------------------------------------------------ scheduling
# Liege directive 2026-09-13 (CONFIRMED, after an earlier contradictory wording):
# PEAK bills at roughly double, so tasks must run in the OFF-PEAK window.
# Policy is named explicitly rather than implied, so the choice is visible.
POLICIES = ("avoid_peak", "none")
ACTIVITY_POLICY = "avoid_peak"

# ------------------------------------------------------------------ GLOBAL WINDOW
# Liege 2026-09-13, SIMPLIFIED: avoid the hours 00:00-14:00 UTC EVERY DAY, including
# weekends, and run jobs at any other time. This replaces per-row rate-rule gating.
# Why this is better than the per-row rule: one window, no dependency on a lookup field
# whose value can be "n/a" (rows 47/48), a different shape ("daily" vs weekday lists), or
# absent until a model is picked. Fewer moving parts, and NO silent failure mode where a
# rule we cannot parse decides when real money is spent.
# A global window that spans midnight is supported by in_peak already.
GLOBAL_AVOID_WINDOWS = [([0, 1, 2, 3, 4, 5, 6], 0, 14 * 60)]   # 00:00-14:00 UTC daily

_DAYS = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
# The day part may be a single day, a range, or the keyword "daily" (which real rows
# in table 207 use, e.g. "peak(daily 00:00-14:00 utc)"). "daily" was NOT handled at
# first, which made those rows parse as MALFORMED and therefore fail closed - they
# would never have been scheduled at all. Found by reading the actual rules.
_DAYPART = __import__("re").compile(
    r"^(daily|mon|tue|wed|thu|fri|sat|sun)(?:-(mon|tue|wed|thu|fri|sat|sun))?\s+(.+)$",
    __import__("re").I)
_TIMEPART = __import__("re").compile(r"(\d{1,2}):(\d{2})-(\d{1,2}):(\d{2})")


def parse_peak_windows(rule):
    """Parse the lookup rule into peak windows.

    Returns (windows, status) where status is one of:
      none      - no rule, or a rule with no time clause: no peak to avoid. RUN NOW.
      ok        - windows parsed
      malformed - a time clause exists but cannot be parsed: FAIL CLOSED upstream.

    The empty-vs-malformed distinction is deliberate. Empty means the model has no peak
    pricing (or no model is chosen yet) and starting immediately is correct. Malformed
    means we cannot tell, and running now would silently pay peak rates on every such
    row - so it must be refused, loudly, rather than guessed.
    """
    import re as _re
    # NORMALISE FIRST. special_ai_agent_calculation_rule is a LOOKUP field, so the API
    # returns a LIST OF DICTS - [{"id": 9, "value": "base:time=peak(...)"}] - not a
    # string. Calling .strip() on it raised AttributeError and killed the whole cycle,
    # every cycle. My unit tests passed plain strings and therefore could not catch it:
    # the test fixture was shape-wrong. Accept str | list | dict | None.
    if isinstance(rule, list):
        rule = rule[0] if rule else ""
    if isinstance(rule, dict):
        rule = rule.get("value")
    txt = (rule or "").strip() if isinstance(rule, str) else str(rule or "").strip()
    if not txt:
        return [], "none"
    if "time=" not in txt.lower():
        return [], "none"
    m = _re.search(r"peak\(([^)]*)\)", txt, _re.I)
    if not m:
        return [], "malformed"
    wins, days = [], None
    for token in m.group(1).split(","):
        token = token.strip()
        if not token:
            continue
        # The rule ends with a bare timezone word on the LAST time only, e.g.
        # "...01:00-04:00,06:00-10:00 utc". Strip it, or the strict time pattern fails
        # and a perfectly valid rule is misread as malformed (which refuses to run).
        token = _re.sub(r"\s+(utc|gmt|z)$", "", token, flags=_re.I).strip()
        dp = _DAYPART.match(token)
        rest = token
        if dp:
            d1, d2, rest = dp.group(1).lower(), (dp.group(2) or "").lower(), dp.group(3)
            if d1 == "daily":
                days = list(range(7))
            else:
                if d1 not in _DAYS:
                    return [], "malformed"
                a = _DAYS[d1]
                b = _DAYS[d2] if d2 else a
                if d2 and d2 not in _DAYS:
                    return [], "malformed"
                days = list(range(a, b + 1)) if a <= b else list(range(a, 7)) + list(range(0, b + 1))
        tm = _TIMEPART.search(rest)
        if not tm or days is None:
            return [], "malformed"
        start = int(tm.group(1)) * 60 + int(tm.group(2))
        end = int(tm.group(3)) * 60 + int(tm.group(4))
        if not (0 <= start <= 1440 and 0 <= end <= 1440) or start == end:
            return [], "malformed"
        wins.append((days, start, end))
    if not wins:
        return [], "malformed"
    return wins, "ok"


def in_peak(when, windows):
    mins = when.hour * 60 + when.minute
    for days, start, end in windows:
        if when.weekday() not in days:
            continue
        if start < end:
            if start <= mins < end:
                return True
        else:                      # window spans midnight
            if mins >= start or mins < end:
                return True
    return False


def next_offpeak(when, windows, limit_min=8 * 24 * 60):
    """First minute at/after `when` that is NOT inside a peak window.

    Stepping minute by minute is deliberately dumb: it is trivially correct for windows
    that span midnight and for multi-day ranges, where hand-rolled arithmetic is exactly
    where this feature would go wrong invisibly. Bounded to 8 days, then it gives up and
    reports rather than looping.
    """
    from datetime import timedelta
    cur = when.replace(second=0, microsecond=0)
    for _ in range(limit_min):
        if not in_peak(cur, windows):
            return cur
        cur = cur + timedelta(minutes=1)
    return None

# The executor: what actually RUNS a claimed task. Set EXECUTOR=<path> in
# ~/.bare-ai/taskbus-exec.conf (one line). It is invoked as <executor> <row_id>
# through dispatch-job.sh, so it runs detached and its output is secret-scrubbed.
# UNSET BY DEFAULT, and live mode FAILS CLOSED without it: a poller that claims a
# task it cannot execute would mark a real row InProgress, do nothing, go stale,
# burn an attempt and finally Block that task. Refusing is strictly better.
EXECUTOR = None


def load_executor():
    """Return the configured executor path, or None. Fails closed on any doubt."""
    if not os.path.exists(EXEC_CONF):
        return None
    try:
        for line in open(EXEC_CONF):
            line = line.strip()
            if line.startswith("EXECUTOR="):
                p = line.split("=", 1)[1].strip().strip(chr(34)).strip(chr(39))
                if p and os.path.isfile(p) and os.access(p, os.X_OK):
                    return p
                return None
    except OSError:
        return None
    return None
BLOCKING_STATES = ("Cancelled", "Blocked", "On-hold")


def now_utc():
    return datetime.now(timezone.utc)


def log(msg, level="INFO"):
    line = "%s [%s] %s" % (now_utc().strftime("%Y-%m-%dT%H:%M:%SZ"), level, msg)
    # Logs go to STDERR so that stdout carries ONLY the --json payload. Mixing them
    # made --json output unparseable (found while testing row 11).
    print(line, file=sys.stderr, flush=True)
    try:
        os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
        with open(LOG_FILE, "a") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


# ------------------------------------------------------------------ table access

def load_agent_context():
    """Read the agent context files and return (ok, one_line_summary).

    Required: technical constitution, bare archetype, node environment file.
    Optional: ~/.bare-ai/roles/current.md (not every node carries a roles dir).
    Cheap enough for every cycle (~250 KB) and deliberately NOT cached, so an edited
    file takes effect on the next cycle without a restart.
    """
    import hashlib
    base = os.path.join(HOME, ".bare-ai")
    required = [("constitution", os.path.join(base, "technical-constitution.md")),
                ("archetype", os.path.join(base, "bare-archetype.md")),
                ("environment", os.path.join(base, "bare-environment.md"))]
    optional = [("roles", os.path.join(base, "roles", "current.md"))]
    parts, ok = [], True
    for name, path in required:
        try:
            with open(path, "rb") as fh:
                blob = fh.read()
        except OSError as exc:
            parts.append("%s=MISSING(%s)" % (name, exc.__class__.__name__))
            ok = False
            continue
        if not blob.strip():
            parts.append("%s=EMPTY" % name)
            ok = False
            continue
        parts.append("%s=%dB/%s" % (name, len(blob),
                                    hashlib.sha256(blob).hexdigest()[:8]))
    for name, path in optional:
        try:
            with open(path, "rb") as fh:
                blob = fh.read()
            parts.append("%s=%dB/%s" % (name, len(blob),
                                        hashlib.sha256(blob).hexdigest()[:8]))
        except OSError:
            parts.append("%s=absent" % name)
    return ok, " ".join(parts)


def load_env():
    env = {}
    with open(ENVF) as fh:
        for line in fh:
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    return env



# ------------------------------------------------- PER-MACHINE BASEROW IDENTITY (2026-09-20)
# WAS: one shared admin@bare-erp.com table token in taskbus.env, so no row edit could be
# attributed to a machine. NOW: the poller authenticates as its own Baserow user.
# THERE IS DELIBERATELY NO FALLBACK to the old shared token. Per the standing credential rule, a
# failed Vault lookup means the credential is UNTRUSTED, not optional - so this fails closed and
# lets the cycle abort loudly rather than silently reverting to a shared identity.
VAULT_ENVF = os.path.join(HOME, ".bare-ai", "config", "vault.env")
POLLER_SECRET_PATH = os.environ.get(
    "BASEROW_SECRET_PATH", "secret/data/baserow/machines/bare-ai-poller-04")


def _vault_exports():
    env = {}
    with open(VAULT_ENVF) as fh:
        for line in fh:
            line = line.strip()
            if line.startswith("export ") and "=" in line:
                k, v = line[len("export "):].split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def poller_baserow_jwt(base):
    """Return (email, jwt) for the poller identity.

    The password is read from Vault here and used once to exchange for a JWT. Neither the
    password nor the JWT is ever logged. Endpoint measured 2026-09-20: POST /api/user/token-auth/
    returns {"token": ...} and the header is  Authorization: JWT <token>.
    """
    env = _vault_exports()
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    addr = env["VAULT_ADDR"].rstrip("/")

    body = json.dumps({"role_id": env["VAULT_ROLE_ID"],
                       "secret_id": env["VAULT_SECRET_ID"]}).encode()
    req = urllib.request.Request(addr + "/v1/auth/approle/login", data=body)
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, context=ctx, timeout=20) as resp:
        vt = json.loads(resp.read())["auth"]["client_token"]

    req = urllib.request.Request(addr + "/v1/" + POLLER_SECRET_PATH)
    req.add_header("X-Vault-Token", vt)
    with urllib.request.urlopen(req, context=ctx, timeout=20) as resp:
        d = json.loads(resp.read())["data"]["data"]
    email, pw = d["BASEROW_LOGIN_EMAIL"], d["BASEROW_PASSWORD"]

    req = urllib.request.Request(
        base + "/api/user/token-auth/",
        data=json.dumps({"email": email, "password": pw}).encode())
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=20) as resp:
        tok = json.loads(resp.read())["token"]
    return email, tok



class TaskBus:
    def __init__(self, dry_run=True):
        env = load_env()
        self.base = (env.get("BASEROW_URL") or "http://100.64.0.19").rstrip("/")
        self._jwt = None
        self.identity_email = None
        self.refresh_auth()
        self.dry_run = dry_run
        self.writes = 0

    def refresh_auth(self):
        """Acquire a fresh JWT for THIS machine's poller identity. FAILS CLOSED."""
        try:
            email, tok = poller_baserow_jwt(self.base)
        except Exception as exc:
            raise RuntimeError(
                "poller Baserow identity unavailable (%s via %s): %s. Refusing to fall back to a "
                "shared token - fix the Vault path or the poller user."
                % (POLLER_SECRET_PATH, VAULT_ENVF, exc))
        self.identity_email = email
        self._jwt = tok
        log("identity: authenticated to Baserow as %s" % email)

    @property
    def auth(self):
        """Never stored as a plain attribute, so it cannot be accidentally logged."""
        return "JWT " + (self._jwt or "")

    def _req(self, path, method="GET", payload=None):
        url = self.base + path
        data = json.dumps(payload).encode() if payload is not None else None
        last = None
        for attempt in (1, 2):
            req = urllib.request.Request(url, data=data, method=method)
            req.add_header("Authorization", self.auth)
            if data:
                req.add_header("Content-Type", "application/json")
            try:
                with urllib.request.urlopen(req, timeout=60) as resp:
                    body = resp.read()
                    return json.loads(body) if body else {}
            except urllib.error.HTTPError as e:
                detail = e.read().decode("utf-8", "replace")[:300]
                last = "HTTP %s on %s %s -> %s" % (e.code, method, path, detail)
                if e.code == 401 and attempt == 1:
                    log("identity: 401 - re-acquiring the JWT and retrying once", "WARN")
                    self.refresh_auth()
                    continue
                raise RuntimeError(last)
            except Exception as exc:
                last = "%s on %s %s -> %s" % (type(exc).__name__, method, path, exc)
                raise RuntimeError(last)
        raise RuntimeError(last or "request failed")

    def all_rows(self):
        rows, page = [], 1
        while True:
            d = self._req("/api/database/rows/table/%d/?user_field_names=true&size=200&page=%d"
                          % (TABLE, page))
            rows.extend(d["results"])
            if not d.get("next"):
                break
            page += 1
        return rows

    def get_row(self, rid):
        return self._req("/api/database/rows/table/%d/%d/?user_field_names=true" % (TABLE, rid))

    def patch(self, rid, payload, why):
        if self.dry_run:
            log("DRY-RUN  would PATCH row %s: %s   (%s)" % (rid, json.dumps(payload), why))
            return None
        out = self._req("/api/database/rows/table/%d/%d/?user_field_names=true" % (TABLE, rid),
                        method="PATCH", payload=payload)
        self.writes += 1
        log("WROTE    row %s: %s   (%s)" % (rid, json.dumps(payload), why))
        return out


# --------------------------------------------------------------- row accessors

def st(row):
    """status value as a plain string."""
    s = row.get("status")
    return s.get("value") if isinstance(s, dict) else (s or "")


def fval(row, name):
    v = row.get(name)
    if isinstance(v, dict):
        return v.get("value")
    return v


def num(row, name, default=0):
    """Coerce a Baserow number field to int.

    Baserow returns number fields as STRINGS (verified: max_attempts -> '3').
    Arithmetic on the raw value raises TypeError, so every read is coerced here.
    """
    v = row.get(name)
    if v is None or v == "":
        return default
    try:
        return int(v)
    except (TypeError, ValueError):
        try:
            return int(float(v))
        except (TypeError, ValueError):
            return default


def link_ids(row, name):
    v = row.get(name)
    if not v:
        return []
    if isinstance(v, list):
        return [x.get("id") if isinstance(x, dict) else x for x in v]
    return []


def parse_baserow_ts(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


# ------------------------------------------------------------------ job liveness

def job_uuid_of(row):
    """corresponding_machine_task_id looks like 'job:<uuid>' or a bare uuid."""
    raw = (row.get("corresponding_machine_task_id") or "").strip()
    if not raw:
        return None
    return raw[4:] if raw.startswith("job:") else raw


def ssh_node(node, cmd, timeout=40):
    """Run a command on a node. Returns (reachable, output_or_error).

    reachable=False means UNKNOWN, and that distinction is load-bearing: an unreachable
    node must NEVER be interpreted as "no job running", because that would declare a live
    task stuck and re-dispatch it - executing the same task twice.
    """
    try:
        p = subprocess.run(["ssh"] + SSH_OPTS + ["%s@%s" % (SSH_USER, node), cmd],
                           capture_output=True, text=True, timeout=timeout)
    except (subprocess.TimeoutExpired, OSError) as e:
        return False, "%s: %s" % (type(e).__name__, e)
    # ssh(1) exits with the REMOTE command's exit status, or 255 if ssh ITSELF
    # failed (connect, auth, transport). ONLY the latter means "node unreachable".
    # The previous test - nonzero rc with empty stdout - also matched a remote
    # command that merely FAILED, e.g. a cat of a job file that does not exist: that
    # exits 1 with empty stderr, because the callers redirect stderr at the far end.
    # Such a node is REACHABLE and simply holds NO job record, so it must not be
    # reported UNREACHABLE - doing so left stuck rows un-remediated and cancellation
    # requests un-honoured indefinitely (row 76, 17 consecutive cycles).
    # Erring the other way stays safe: rc 255 still fails closed as unreachable, and
    # an unreachable node is never read as "no job running".
    if p.returncode == 255:
        return False, (p.stderr or "ssh rc=255").strip()[:160]
    return True, p.stdout


def remote_job_state(node, uuid_str):
    """(state_dict_or_None, reachable). reachable=False => UNKNOWN, do not remediate."""
    if not uuid_str:
        return None, True
    if is_local_node(node):
        return job_state(uuid_str), True      # self: local read, never ambiguous
    ok, out = ssh_node(node, "cat %s/%s.json 2>/dev/null" % (REMOTE_JOBS, uuid_str))
    if not ok:
        return None, False
    if not (out or "").strip():
        return None, True          # reachable, definitively no job record
    try:
        return json.loads(out), True
    except ValueError:
        return None, True          # reachable but corrupt/unreadable record


def remote_pid_alive(node, pid):
    """True/False, or None when unknown (unreachable)."""
    if is_local_node(node):
        return pid_alive(pid)                 # self: local pid check
    ok, out = ssh_node(node, "kill -0 %s 2>/dev/null && echo yes || echo no" % pid)
    if not ok:
        return None
    return out.strip() == "yes"


def node_for(row):
    """The node a row should run on, or None if .4 cannot dispatch to that machine."""
    m = row.get("assigned_to_machine")
    m = m.get("value") if isinstance(m, dict) else m
    return DISPATCHABLE.get(m)


# ---------------------------------------------------------------------------
# PER-MACHINE OWNERSHIP FILTER (v2.1, 2026-10-25)
#
# Table 215 now carries stakeholder_representatives_master -> table 43 (field 6378).
# That array is the AUTHORITY for which machine owns a row. assigned_to_machine
# (5525) is legacy and is being retired.
#
# Modes (POLLER_FILTER_MODE):
#   rep    : 6378 only. A row with no representative is UNOWNED and is not claimed by
#            anyone. This is the target end-state.
#   legacy : assigned_to_machine only. Pre-migration behaviour.
#   dual   : 6378 first; fall back to 5525 ONLY when 6378 is empty, and LOG a warning
#            every time the fallback is used, so the cutover is observable rather than
#            silent. DEFAULT, because no machine is registered in 43 yet - turning on
#            'rep' today would stop every dispatch and starve the bus.
#
# A node-local poller additionally sets POLLER_SELF_REP=<its row id in 43>; it then
# only claims rows whose 6378 contains that id. Unset means "central dispatcher".
# ---------------------------------------------------------------------------

def _dispatch_lookups():
    """Index DISPATCHABLE ('name@ip' -> node) by bare IP and by bare name.

    A representative stores the IP in legal_last_name and the functional name in
    legal_first_name, NOT the legacy 'name@ip' string, so a direct dict lookup can never
    match. Without this the rep index silently stays empty and the filter degrades to the
    legacy path forever - which is exactly what happened on the first cut (0 mapped).
    """
    by_ip, by_name = {}, {}
    for key, node in DISPATCHABLE.items():
        nm, _, ip = str(key).partition("@")
        if ip:
            by_ip[ip] = node
        if nm:
            by_name[nm] = node
    return by_ip, by_name


_BY_IP, _BY_NAME = _dispatch_lookups()


def rep_ids(row):
    """Int ids from stakeholder_representatives_master, defensively parsed."""
    v = row.get("stakeholder_representatives_master")
    out = []
    if isinstance(v, list):
        for it in v:
            if isinstance(it, dict):
                if it.get("id") is not None:
                    try: out.append(int(it["id"]))
                    except (TypeError, ValueError): pass
            else:
                try: out.append(int(it))
                except (TypeError, ValueError): pass
    return out


def resolve_target(row, rep_index, mode, self_rep, legacy_node):
    """PURE decision: which node should run this row, and why.

    Returns (node, path, detail). node None means "do not claim".
    Kept pure so it can be tested without a bus or a network.
    """
    reps = rep_ids(row)
    if self_rep is not None and reps and self_rep not in reps:
        return None, "not-mine", "6378=%s excludes self_rep=%s" % (reps, self_rep)
    if mode in ("rep", "dual") and reps:
        mapped = [rep_index[r] for r in reps if r in rep_index]
        if mapped:
            return mapped[0], "rep", "6378=%s -> %s" % (reps, mapped[0])
        # An owner IS declared but cannot be resolved to a node. NEVER fall back to the
        # legacy field here: dispatching to whatever assigned_to_machine happens to say is
        # precisely how a row gets executed by the wrong machine. Fail closed, log, move on.
        return None, "rep-unmapped", "6378=%s not resolvable to a node" % (reps,)
    if mode in ("legacy", "dual"):
        if legacy_node:
            return legacy_node, "legacy", "assigned_to_machine (no 6378 mapping)"
        if not reps:
            return None, "unowned", "no 6378 and no dispatchable assigned_to_machine"
    return None, "not-mine" if reps else "unowned", "6378=%s mode=%s" % (reps, mode)



def job_state(uuid_str):
    path = os.path.join(JOB_DIR, uuid_str + ".json")
    if not os.path.exists(path):
        return None
    try:
        with open(path) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def pid_alive(pid):
    try:
        os.kill(int(pid), 0)
    except (OSError, ValueError, TypeError):
        return False
    return True


def job_alive(uuid_str):
    """True only if a job is genuinely running: status running AND pid alive."""
    if not uuid_str:
        return False
    state = job_state(uuid_str)
    if not state:
        return False
    if state.get("status") != "running":
        return False
    return pid_alive(state.get("pid"))


# -------------------------------------------------------------- completion guard

def job_log_tail(uuid_str, limit=1500):
    """Tail of a job log, for attaching evidence to the row."""
    if not uuid_str:
        return "(no job id on the row)"
    path = os.path.join(JOB_DIR, uuid_str + ".log")
    try:
        with open(path, errors="replace") as fh:
            data = fh.read()
    except OSError:
        return "(no log at %s)" % path
    return data[-limit:] if len(data) > limit else data


def remote_log_tail(node, uuid_str, limit=1500):
    if not uuid_str:
        return "(no job id on the row)"
    if is_local_node(node):
        return job_log_tail(uuid_str, limit)  # self: local log
    ok, out = ssh_node(node, "tail -c %d %s/%s.log 2>/dev/null" % (limit * 2, REMOTE_JOBS, uuid_str))
    if not ok:
        return "(job log on %s UNREACHABLE: %s)" % (node, out[:120])
    return (out or "")[-limit:]


# ---- D2/D3: DISPATCHER-SIDE INDEPENDENT VERIFICATION (liege 2026-09-16) -------
# OPTION C. The executor NEVER self-reports success. The DISPATCHER (this poller)
# evaluates a MACHINE-READABLE success_criterion on the node that did the work and,
# only when EVERY check passes, attests by writing verification_result="Passed"
# and verified_by=ME.
#
# WHY THIS REPLACED THE 2026-09-14 SELF-VERIFICATION ALLOWANCE
# That allowance was UNREACHABLE CODE. self_verification_evidence() was called from
# exactly one place - inside "elif vb == ME:" - but NOTHING in the pipeline ever
# wrote verified_by: the poller only ever READ it, and taskbus-agent-job.py and
# dispatch-job.sh contain zero references. So the guard always resolved to
# HumanReviewNeeded. It was a constant, not a check. The regexes were well-reasoned
# and irrelevant. Under Option C an executor's PROSE is never the basis for closing
# a row, so the allowance is DELETED rather than repaired.
# LESSON: when a guard cannot pass, first ask whether the FIELD it reads has any
# writer at all. Refining an unreachable predicate feels like progress and is not.
#
# OPTION B FALLBACK. A criterion with no machine-checkable directive is never
# guessed at. The row goes to HumanReviewNeeded for a MANUAL override, and a
# human-set verified_by (anyone but ME) IS honoured on the next pass - which is what
# makes the fallback an actual escape hatch rather than a dead end.
#
# GRAMMAR (success_criterion; ';' separated; ALL checks must pass)
#   exit_zero                            job exited 0. EXIT-ONLY evidence, valid
#                                        only for pure-compute rows.
#   file_exists:<path>                   path exists on the target node
#   file_md5:<path>=<32 hex>             md5sum matches
#   file_sha256:<path>=<64 hex>          sha256sum matches
#   http_status:<url>=<code>             HTTP status equals <code>
#   http_200:<url>                       sugar for http_status:<url>=200
#   git_ls_remote:<repo>@<branch>=<sha>  remote branch tip equals <sha> (prefix ok)
# Anything else is UNRECOGNISED and REFUSES the row (fail-closed).
#
# SECURITY. success_criterion is authored by humans and AI agents, so it is treated
# as UNTRUSTED INPUT. Every interpolated value is shlex.quote()d before it reaches a
# remote shell; nothing is interpolated raw. A criterion must not be able to become
# a command.
#
# EVERY check runs on the TARGET node, uniformly, so the verdict reflects the host
# that actually did the work.

VERIFY_SEP = ";"
VERIFY_TIMEOUT = 25

_MD5_RE = re.compile(r"^[0-9a-f]{32}$", re.I)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$", re.I)


def split_checks(text):
    return [p.strip() for p in (text or "").split(VERIFY_SEP) if p.strip()]


def _path_problem(kind, path):
    """Reject a path that this completion check could never evaluate.

    TILDE IS NOT EXPANDED. run_check() passes every path through shlex.quote()
    before it reaches the target shell, and a QUOTED tilde stays literal - so
    "~/.bare-ai/lib/x.py" is tested as a file literally named "~", and the check
    reports NOFILE even when the real file exists and is correct. That silently
    authors an ALWAYS-FAILING check, which is worse than a loud error because it
    looks exactly like a legitimate MISSING-ARTIFACT verdict.
    Caught 2026-09-17: a real criterion was authored with a tilde path and would
    have reported NOFILE on every node while the artifact was present.
    Refused at PARSE time so the mistake cannot be saved into a row at all.
    """
    if "~" in path:
        return ("%s path %r contains a tilde: a quoted tilde is NOT expanded by "
                "the target shell, so this check can never pass. Use an "
                "ABSOLUTE path instead (e.g. /home/bare-ai/...)." % (kind, path))
    return None


def parse_check(spec):
    """Parse ONE directive. Returns (check, None) or (None, problem). Strict."""
    low = spec.strip().lower()
    if low == "exit_zero":
        return ("exit_zero", ()), None
    if ":" not in spec:
        return None, "unrecognised directive %r (not exit_zero and has no colon)" % spec
    kind, rest = spec.split(":", 1)
    kind, rest = kind.strip().lower(), rest.strip()

    if kind == "file_exists":
        if not rest:
            return None, "file_exists needs a path"
        problem = _path_problem("file_exists", rest)
        if problem:
            return None, problem
        return ("file_exists", (rest,)), None

    if kind in ("file_md5", "file_sha256"):
        if "=" not in rest:
            return None, "%s needs PATH=HASH" % kind
        path, want = rest.rsplit("=", 1)
        path, want = path.strip(), want.strip().lower()
        rx = _MD5_RE if kind == "file_md5" else _SHA256_RE
        if not path:
            return None, "%s needs a path" % kind
        problem = _path_problem(kind, path)
        if problem:
            return None, problem
        if not rx.match(want):
            return None, "%s hash is not the right shape for %s" % (want, kind)
        return (kind, (path, want)), None

    if kind == "http_200":
        if not rest:
            return None, "http_200 needs a url"
        return ("http_status", (rest, 200)), None

    if kind == "http_status":
        if "=" not in rest:
            return None, "http_status needs URL=CODE"
        url, code = rest.rsplit("=", 1)
        url, code = url.strip(), code.strip()
        if not url or not code.isdigit():
            return None, "http_status needs URL=<numeric code>"
        return ("http_status", (url, int(code))), None

    if kind == "git_ls_remote":
        if "=" not in rest:
            return None, "git_ls_remote needs REPO@BRANCH=SHA"
        where, want = rest.rsplit("=", 1)
        where, want = where.strip(), want.strip().lower()
        if "@" not in where:
            return None, "git_ls_remote needs REPO@BRANCH"
        repo, branch = where.rsplit("@", 1)
        repo, branch = repo.strip(), branch.strip()
        if not repo or not branch or not want:
            return None, "git_ls_remote needs REPO@BRANCH=SHA"
        return ("git_ls_remote", (repo, branch, want)), None

    return None, "unrecognised directive %r" % kind


def _capture(argv):
    try:
        p = subprocess.run(argv, capture_output=True, text=True,
                           timeout=VERIFY_TIMEOUT)
    except subprocess.TimeoutExpired:
        return None, "timed out after %ss" % VERIFY_TIMEOUT
    except OSError as exc:
        return None, str(exc)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def run_check(node, check):
    """Evaluate ONE check on `node`. Returns (ok, evidence).

    ok is True, False, or None. None means COULD NOT EVALUATE (node unreachable,
    tool missing, timeout) - and is NEVER treated as a pass.
    """
    kind, args = check
    local = is_local_node(node)

    def remote(cmd):
        return ssh_node(node, cmd, timeout=VERIFY_TIMEOUT)

    if kind == "file_exists":
        path = args[0]
        if local:
            try:
                hit = os.path.exists(path)
            except OSError as exc:
                return None, "file_exists %s -> could not stat: %s" % (path, exc)
            return hit, "file_exists %s -> %s" % (path, hit)
        ok, out = remote("if [ -e %s ]; then echo PRESENT; else echo ABSENT; fi"
                         % shlex.quote(path))
        if not ok:
            return None, "file_exists %s on %s UNREACHABLE: %s" % (
                path, node, (out or "")[:120])
        hit = (out or "").strip().endswith("PRESENT")
        return hit, "file_exists %s on %s -> %s" % (path, node, hit)

    if kind in ("file_md5", "file_sha256"):
        path, want = args
        tool = "md5sum" if kind == "file_md5" else "sha256sum"
        # Structured: the remote branch always exits 0 and reports an absent
        # artifact explicitly, so "file missing" is a real FAIL rather than being
        # confused with "host unreachable" (which is indeterminate).
        if local:
            if not os.path.exists(path):
                return False, "%s %s -> NOFILE (artifact absent)" % (kind, path)
            rc, out = _capture([tool, path])
            if rc is None:
                return None, "%s %s did not run: %s" % (tool, path, out[:120])
        else:
            ok, out = remote("if [ -e %s ]; then %s %s; else echo NOFILE; fi"
                             % (shlex.quote(path), tool, shlex.quote(path)))
            if not ok:
                return None, "%s %s on %s UNREACHABLE: %s" % (
                    tool, path, node, (out or "")[:120])
            if "NOFILE" in (out or ""):
                return False, "%s %s on %s -> NOFILE (artifact absent)" % (
                    kind, path, node)
        got = (out or "").split()
        got = got[0].lower() if got else ""
        good = (got == want)
        return good, "%s %s = %s (want %s) -> %s" % (
            kind, path, got or "?", want, "MATCH" if good else "MISMATCH")

    if kind == "http_status":
        url, want_code = args
        cmd = ("curl -sS -o /dev/null -w '%%{http_code}' --connect-timeout 5 "
               "--max-time 20 -H 'User-Agent: Bare-AI-Fleet/1.0' %s" % shlex.quote(url))
        if local:
            rc, out = _capture(["/bin/sh", "-c", cmd])
            if rc is None:
                return None, "curl did not run for %s: %s" % (url, out[:120])
        else:
            ok, out = remote(cmd)
            if not ok:
                return None, "http_status %s on %s UNREACHABLE: %s" % (
                    url, node, (out or "")[:120])
        got = (out or "").strip()
        good = got.isdigit() and int(got) == want_code
        return good, "http_status %s -> %s (want %s) -> %s" % (
            url, got or "?", want_code, "MATCH" if good else "MISMATCH")

    if kind == "git_ls_remote":
        repo, branch, want = args
        cmd = "git ls-remote --heads %s %s" % (shlex.quote(repo), shlex.quote(branch))
        if local:
            rc, out = _capture(["/bin/sh", "-c", cmd])
            if rc is None:
                return None, "git did not run for %s: %s" % (repo, out[:120])
        else:
            ok, out = remote(cmd)
            if not ok:
                return None, "git_ls_remote %s on %s UNREACHABLE: %s" % (
                    repo, node, (out or "")[:120])
        found = []
        for line in (out or "").splitlines():
            tok = line.split()
            if len(tok) >= 2 and re.match(r"^[0-9a-f]{7,40}$", tok[0], re.I):
                found.append((tok[0].lower(), tok[1]))
        if not found:
            return False, "git_ls_remote %s %s -> no sha returned (branch missing?)" % (
                repo, branch)
        good = any(sha.startswith(want) or want.startswith(sha)
                   for sha, _ref in found)
        return good, "git_ls_remote %s %s -> %s (want %s) -> %s" % (
            repo, branch, found[0][0][:12], want[:12],
            "MATCH" if good else "MISMATCH")

    return None, "unhandled check kind %r" % (kind,)


def verify_row(node, row, exit_code=None):
    """Dispatcher's INDEPENDENT verification of a row.

    Returns (verdict, evidence, reasons); verdict is "passed" / "failed" /
    "unverifiable". ALL checks must pass for "passed". An empty or unparseable
    criterion is "unverifiable" -> Option B (manual override).
    """
    text = (row.get("success_criterion") or "").strip()
    if not text:
        return "unverifiable", [], [
            "success_criterion is EMPTY - nothing can be independently verified "
            "(Option B: a human override is required)"]

    checks, problems = [], []
    for spec in split_checks(text):
        chk, prob = parse_check(spec)
        if prob:
            problems.append(prob)
        else:
            checks.append((spec, chk))
    if problems:
        return "unverifiable", [], problems
    if not checks:
        return "unverifiable", [], ["success_criterion contains no checks"]

    evidence, reasons, all_ok = [], [], True
    for spec, chk in checks:
        if chk[0] == "exit_zero":
            if exit_code is None:
                all_ok = False
                evidence.append("exit_zero -> UNKNOWN (no exit code recorded)")
                reasons.append("exit_zero could not be evaluated: no exit code")
                continue
            good = (int(exit_code) == 0)
            evidence.append("exit_zero -> %s (exit_code=%s; EXIT-ONLY evidence, "
                            "valid only for pure-compute rows)"
                            % ("PASS" if good else "FAIL", exit_code))
            if not good:
                all_ok = False
                reasons.append("exit_zero FAILED: exit_code=%s" % exit_code)
            continue

        ok, ev = run_check(node, chk)
        evidence.append(ev)
        if ok is True:
            continue
        all_ok = False
        if ok is None:
            reasons.append("could not evaluate %r: %s" % (spec, ev))
        else:
            reasons.append("check FAILED: %s" % ev)

    return ("passed" if all_ok else "failed"), evidence, ([] if all_ok else reasons)




def criterion_verdict(row):
    """DEFECT 2 (row 123): pre-flight a row's success_criterion AT CLAIM TIME.

    PURE - no I/O, no network, safe to unit test.

    WHY THIS EXISTS. completion_guard() only ever parses success_criterion at
    COMPLETION time. A row whose criterion is human prose - which is exactly how
    the Liege authors that field - was therefore CLAIMED, DISPATCHED and RUN TO
    COMPLETION, burning its whole attempt budget, and only then refused with
    "unrecognised directive ... (not exit_zero and has no colon)". The row can
    never be satisfied, so every attempt is guaranteed waste. Refusing at claim
    time costs nothing and spends no budget.

    It calls the SAME split_checks() and parse_check() the completion guard uses,
    so the pre-flight can never disagree with the gate that actually decides.

    The guard's Option-B override is mirrored here: a row already attested by a
    verifier OTHER than this dispatcher does not need a parseable criterion, so
    it is exempt rather than blocked.

    Returns (ok: bool, problems: list[str]).
    """
    if fval(row, "verification_result") == "Passed":
        vb = fval(row, "verified_by")
        if vb and vb != ME:
            return True, []

    specs = split_checks(row.get("success_criterion"))
    if not specs:
        return False, ["success_criterion is empty - cannot meet an unstated criterion"]

    problems = []
    for spec in specs:
        check, problem = parse_check(spec)
        if problem:
            problems.append("%r: %s" % (spec, problem))
    return (not problems), problems


# ---- ROW 123 DEFECT 3: snapshot the criterion AT CLAIM TIME ----------------
# WHY: completion_guard() re-read success_criterion from Baserow at COMPLETION
# time. Baserow has NO per-field attribution, so that field can be edited between
# claim and completion - and the job would then be graded against a criterion it
# was never given, or against one that was quietly relaxed mid-run. The snapshot
# freezes what was agreed when the work STARTED.
SNAPSHOT_SUFFIX = ".criterion.json"


def _sha256(text):
    import hashlib
    return hashlib.sha256(str(text).encode("utf-8")).hexdigest()


def criterion_snapshot(row, juuid):
    """PURE. The frozen criterion record written at claim time."""
    return {
        "uuid": juuid,
        "row": row.get("id"),
        "success_criterion": row.get("success_criterion") or "",
        "ai_prompt_sha256": _sha256(row.get("ai_prompt") or ""),
        "claimed_at": now_utc().strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def snapshot_path(juuid):
    return os.path.join(JOB_DIR, str(juuid) + SNAPSHOT_SUFFIX)


def write_criterion_snapshot(row, juuid):
    """Atomic write. Returns the path, or None on failure. NEVER raises.

    Written to a temp file and os.replace()d, so a reader never sees a partial
    snapshot - the same discipline the job state file uses.
    """
    path = snapshot_path(juuid)
    tmp = path + ".tmp"
    try:
        with open(tmp, "w") as fh:
            json.dump(criterion_snapshot(row, juuid), fh, indent=2, sort_keys=True)
        os.replace(tmp, path)
        return path
    except OSError as exc:
        log("criterion snapshot write failed for %s: %s" % (juuid, exc), "WARN")
        return None


def read_criterion_snapshot(juuid):
    """The snapshot dict, or None. A missing/unreadable snapshot is NOT an error."""
    try:
        with open(snapshot_path(juuid)) as fh:
            d = json.load(fh)
        return d if isinstance(d, dict) else None
    except (OSError, ValueError):
        return None


def criterion_source(row, juuid=None):
    """Returns (text, source, drift). PURE apart from one file read.

    source is 'snapshot@claim' when a snapshot exists, else 'live-row'.
    drift is True when the live row no longer matches the snapshot. The caller
    must then say so rather than silently grading against whichever version
    happens to be current - an unexplained change of success criterion is
    exactly the failure this snapshot exists to make visible.
    """
    juuid = juuid or job_uuid_of(row)
    live = row.get("success_criterion") or ""
    if not juuid:
        return live, "live-row", False
    snap = read_criterion_snapshot(juuid)
    if not snap:
        return live, "live-row", False
    frozen = snap.get("success_criterion") or ""
    return frozen, "snapshot@claim", (frozen != live)


def completion_guard(row, node=None, exit_code=None):
    """FAIL-CLOSED. Returns (allowed: bool, reasons: list, evidence: list).

    OPTION C (liege 2026-09-16). An ACTIVE PREDICATE: it independently evaluates
    the row's machine-readable success_criterion on `node`. It no longer reads
    verification_result / verified_by as INPUTS - under Option C those are the
    OUTPUT the dispatcher writes when, and only when, this returns True.

    The Option B override path IS honoured: if a verifier OTHER than this
    dispatcher already recorded verification_result == "Passed" with verified_by
    set, that manual attestation is accepted and the checks are not re-run.

    A stamp by ME is deliberately NOT accepted as evidence. Otherwise our own
    output would become our own input, which is precisely the self-attestation
    loop this change exists to remove.
    """
    vb = fval(row, "verified_by")
    if fval(row, "verification_result") == "Passed" and vb and vb != ME:
        return True, [], ["pre-attested by %s (manual override accepted; this is "
                          "not the dispatcher's own stamp)" % vb]

    crit_text, crit_src, crit_drift = criterion_source(row)
    crit_notes = []
    if crit_text != (row.get("success_criterion") or ""):
        row = dict(row)
        row["success_criterion"] = crit_text
        crit_notes.append("graded against the CLAIM-TIME snapshot for job %s "
                          "(source=%s), NOT against the live row"
                          % (job_uuid_of(row), crit_src))
    if crit_drift:
        crit_notes.append("WARNING: the live row's success_criterion has CHANGED "
                          "since this job was claimed - the snapshot wins, and the "
                          "change is recorded here on purpose")

    if not (row.get("success_criterion") or "").strip():
        return False, ["success_criterion is empty - cannot meet an unstated "
                       "criterion"], []

    verdict, evidence, reasons = verify_row(node or node_for(row), row, exit_code)
    if verdict == "passed":
        return True, [], (evidence + crit_notes)
    return False, (reasons + crit_notes), evidence


# ------------------------------------------------------------------- note helper

def append_note(existing, line):
    stamp = now_utc().strftime("%Y-%m-%d %H:%MZ")
    chunk = "[%s] %s" % (stamp, line)
    return (existing + "\n" + chunk) if (existing or "").strip() else chunk


# ------------------------------------------------- D5: age / staleness policy
# The defect inventory: 20 non-terminal rows sat 3 days old, bulk-loaded and
# untouched, with NO age escalation, NO review reminder, and NO distinction between
# "genuinely blocked" and "forgotten". This adds that distinction as a PURE function
# so it is unit-testable, plus a read-only report.

STALE_DAYS = 3                 # at/over this age a non-terminal row is STALE
STALE_MARKER = "[STALE-SWEEP]"  # idempotency marker for opt-in annotation


def row_age_days(row, now=None):
    """How long the row has been OPEN, in days, or None if unmeasurable.

    created_on is the staleness clock, and it is deliberately preferred over
    last_modified because THE POLLER ITSELF writes to rows every cycle. Measured on
    live data 2026-09-16: row 5 had created_on 2026-09-12 but last_modified
    2026-09-16T18:37 - four days open, yet a last_modified-based policy called it
    "fresh" purely because the poller had just touched it. A staleness clock that the
    monitored process can reset is not a staleness clock.
    Fallbacks: last_modified, then start_task_at. None means UNKNOWN, never 0.
    """
    now = now or now_utc()
    for field in ("created_on", "last_modified", "start_task_at"):
        when = parse_baserow_ts(row.get(field))
        if when is not None:
            try:
                return (now - when).total_seconds() / 86400.0
            except (TypeError, ValueError):
                continue
    return None


def row_touched_days(row, now=None):
    """Days since anything last touched the row (last_modified clock).

    Reported SEPARATELY from row_age_days so "open N days" is never conflated with
    "something looked at it recently" - the poller touching a row is not review.
    """
    now = now or now_utc()
    when = parse_baserow_ts(row.get("last_modified"))
    if when is None:
        return None
    try:
        return (now - when).total_seconds() / 86400.0
    except (TypeError, ValueError):
        return None


def stale_verdict(row, index=None, now=None, stale_days=None):
    """Classify a row by age. PURE - safe to unit test, performs no I/O.

    Returns (band, age_days, detail). band is one of:
      terminal | blocked | forgotten | fresh | unknown-age
    Only "forgotten" is a candidate for escalation: it is the band that means
    "nobody is waiting on anything and nobody has looked at this".
    """
    now = now or now_utc()
    stale_days = STALE_DAYS if stale_days is None else stale_days
    status = st(row)

    if status in SATISFIED_STATES:
        return ("terminal", None, "status=%s" % status)

    if status in BLOCKING_STATES or status == "Cancelled":
        return ("blocked", None, "declared blocking state: %s" % status)

    # An UNSATISFIED DEPENDENCY is genuine waiting, not neglect. Distinguishing the
    # two is the whole point of D5, so a dependency check comes BEFORE the age test.
    for dep_id in link_ids(row, "dependant_on_task"):
        dep = (index or {}).get(dep_id)
        if dep is None:
            return ("blocked", None,
                    "depends on row %s which is NOT in the table" % dep_id)
        dep_status = st(dep)
        if dep_status in SATISFIED_STATES:
            continue
        return ("blocked", None, "waiting on dependency row %s (%s)"
                % (dep_id, dep_status or "unknown"))

    age = row_age_days(row, now=now)
    if age is None:
        # NEVER reported as fresh. An unmeasurable row is its own band so it is
        # visible; treating unknown as healthy is what caused the original silence.
        return ("unknown-age", None,
                "no usable last_modified/start_task_at - age UNKNOWN")

    if age >= stale_days:
        touched = row_touched_days(row, now=now)
        return ("forgotten", age,
                "OPEN %.1f days (created_on), non-terminal, NOT blocked - nobody is "
                "waiting on this. Last touched: %s"
                % (age, ("%.2f days ago" % touched) if touched is not None
                   else "UNKNOWN"))
    return ("fresh", age, "%.1f days old" % age)


def stale_sweep(rows, now=None, stale_days=None):
    """Read-only sweep. Returns (summary_dict, forgotten_rows). Performs no writes."""
    index = {}
    for r in rows:
        index[r.get("id")] = r
    bands = {}
    forgotten = []
    for r in rows:
        band, age, detail = stale_verdict(r, index=index, now=now,
                                         stale_days=stale_days)
        bands[band] = bands.get(band, 0) + 1
        if band == "forgotten":
            forgotten.append({"row": r.get("id"), "age_days": round(age, 2),
                              "status": st(r), "detail": detail})
    return bands, forgotten


# -------------------------------------------------------------------- the poller

class Poller:
    def __init__(self, bus, live=False):
        self.bus = bus
        self.live = live
        self.actions = []

    def record(self, kind, rid, detail):
        self.actions.append({"kind": kind, "row": rid, "detail": detail})

    # ---- guardrail 1: kill switch
    def paused(self):
        return os.path.exists(KILL_SWITCH)

    # ---- dependencies (scope section 7)
    def deps_verdict(self, row, index):
        """Returns (ok, unsatisfied_ids, blocking_ids)."""
        dep_ids = link_ids(row, "dependant_on_task")
        bad, blocking = [], []
        for did in dep_ids:
            dep = index.get(did)
            if dep is None:
                bad.append(did)
                continue
            s = st(dep)
            if s in SATISFIED_STATES:
                continue
            bad.append(did)
            if s in BLOCKING_STATES or s == "Cancelled":
                blocking.append(did)
        return (len(bad) == 0, bad, blocking)

    def build_task_prompt(self, rid, row):
        """The TASK text only. The target prepends its OWN role constitution, so a job on
        .11 runs as .11's agent, not as .4's."""
        short = (row.get("short_description") or "").strip()
        body = (row.get("ai_prompt") or "").strip()
        crit = (row.get("success_criterion") or "").strip() or "(none stated)"
        # ---- D4 FIX: human_notes ARE now delivered to the agent ----------------
        # human_notes is where a human writes a briefing BEFORE the row is claimed.
        # It was never included in the prompt, so a 2353-char briefing on row 63 was
        # silently ignored: the agent was briefed through a field nobody read.
        # Placed BEFORE the instruction so it is read as context, omitted when empty
        # so no misleading empty heading appears, and capped with a VISIBLE marker -
        # a silent truncation would be worse than no truncation.
        HUMAN_NOTES_MAX = 8000
        notes = (row.get("human_notes") or "").strip()
        notes_block = ""
        if notes:
            if len(notes) > HUMAN_NOTES_MAX:
                notes = (notes[:HUMAN_NOTES_MAX]
                         + "\n\n[... human_notes TRUNCATED at %d of %d characters - "
                           "read the row directly for the rest ...]" % (HUMAN_NOTES_MAX,
                                                                        len(notes)))
            notes_block = ("HUMAN BRIEFING (field: human_notes. Written by a human on this "
                           "row BEFORE it was claimed. Treat as authoritative context and "
                           "prefer it over general assumptions):\n"
                           + notes + "\n\n")
        return ("\n=====================================================================\n"
                "TASK-BUS JOB - row %d (dispatched by the task-bus poller on .4)\n"
                "=====================================================================\n"
                "You are the agent on the machine that received this job. Above is your own\n"
                "role constitution. This is a job from the fleet task bus: act as yourself.\n\n"
                "SHORT DESCRIPTION: %s\n\n"
                "%s"
                "DETAIL / INSTRUCTION:\n%s\n\n"
                "SUCCESS CRITERION:\n%s\n\n"
                "Report what you did and any evidence. If you cannot complete it, say so plainly\n"
                "and state exactly what is blocking you. Never claim success you cannot evidence.\n"
                % (rid, short, notes_block, body, crit))

    def dispatch_remote(self, node, juuid, rid, row):
        """Ship the task text to the target and start it detached via dispatch-job.sh.

        The task text travels as a FILE so (a) the target needs no Baserow credential of
        its own and (b) a long prompt cannot be mangled by shell quoting or argv limits.
        """
        prompt = self.build_task_prompt(rid, row)
        if is_local_node(node):
            # SELF-TARGET (2026-09-13). This host IS the target, so never scp/ssh to
            # ourselves: see dispatch_self() for the two independent defects that made
            # the old path impossible for a self target.
            self.dispatch_self(juuid, rid, prompt, row)
            return
        local_pf = "/tmp/taskbus-%s.prompt" % juuid
        remote_pf = "/tmp/taskbus-%s.prompt" % juuid
        try:
            with open(local_pf, "w", encoding="utf-8") as fh:
                fh.write(prompt)
        except OSError as e:
            log("cannot stage prompt for job %s: %s" % (juuid, e), "ERROR")
            self.record("dispatch-failed", rid, str(e))
            return
        try:
            cp = subprocess.run(["scp", "-q"] + SSH_OPTS + [local_pf,
                                "%s@%s:%s" % (SSH_USER, node, remote_pf)],
                                capture_output=True, text=True, timeout=90)
            if cp.returncode != 0:
                raise RuntimeError((cp.stderr or "scp failed").strip()[:160])
            # Liege directive 2026-10-24: hand the row's success_criterion to the target
            # dispatcher so the job record carries it. shlex.quote is mandatory - the
            # criterion contains ";" and "=" and is passed through a shell.
            import shlex as _shlex
            _crit = (row.get("success_criterion") or "").strip()
            # Multi-pass contract: the POLLER owns the criterion grammar and ships PARSED
            # checks, so the runner never has to re-implement the parser. The runner
            # executes them, resumes the session on failure, and reports honestly.
            _parsed = []
            for _s in split_checks(_crit):
                _ck, _prob = parse_check(_s)
                if _ck:
                    _parsed.append({"kind": _ck[0], "args": list(_ck[1])})
            _cwd = (row.get("workspace_dir") or "").strip() if isinstance(row.get("workspace_dir"), str) else ""
            # Do NOT default this to the dispatcher's own home: an absolute hub path shipped to a
            # worker makes that worker mkdir a directory it cannot own - exactly how row 233 died.
            # Empty means: let the runner resolve its own ~/.bare-ai/work, on the node that runs it.
            _env = ("SUCCESS_CRITERION=%s " % _shlex.quote(_crit)) if _crit else ""
            if _parsed:
                _env += "TASKBUS_CRITERION_JSON=%s " % _shlex.quote(json.dumps(_parsed))
            if _cwd:
                _env += "TASKBUS_JOB_CWD=%s " % _shlex.quote(_cwd)
            log("row %s: shipping %d criterion check(s) + cwd=%s to the runner"
                % (rid, len(_parsed), _cwd))
            cmd = ("%ssetsid nohup bash %s %s python3 %s %s >/dev/null 2>&1 &"
                   % (_env, REMOTE_DISPATCHER, juuid, REMOTE_RUNNER, remote_pf))
            ok, out = ssh_node(node, cmd)
            if not ok:
                raise RuntimeError("ssh dispatch failed: %s" % out)
            log("dispatched job %s for row %s to %s" % (juuid, rid, node))
        except Exception as e:
            log("dispatch to %s FAILED for job %s (row %s): %s" % (node, juuid, rid, e), "ERROR")
            self.record("dispatch-failed", rid, str(e)[:120])
        finally:
            try:
                os.unlink(local_pf)
            except OSError:
                pass

    def dispatch_self(self, juuid, rid, prompt, row=None):
        """Dispatch a job whose target is THIS host, with no scp and no ssh.

        Added 2026-09-13. Previously every target, including self, took the remote
        path, which made a self-target job impossible for two independent reasons:

          1. local_pf and remote_pf were the SAME /tmp path, so scp tried to replace a
             file already owned by this process while authenticating as SSH_USER.
             Under sticky /tmp that replacement is refused, so every self-target
             dispatch died with "dest open ... Permission denied" and no .4-assigned
             row could ever run.

          2. dispatch-job.sh derives its job directory from the INVOKING user's HOME.
             Over ssh that resolves to /home/bare-ai/.bare-ai/jobs, whereas
             remote_job_state() reads a self target's record from JOB_DIR - a
             DIFFERENT directory. Writer and reader could never agree, so a LIVE job
             would read as absent, its lease would expire and the row would be
             re-dispatched, executing the same task twice.

        It also avoided a stale-scrubber leak: .4's /home/bare-ai copy of
        dispatch-job.sh is an OLD build whose secret scrubber was since rewritten, so
        routing self-targets through it wrote job logs with the weaker scrubber.

        Running the dispatcher locally uses ONE job dir (JOB_DIR - the directory the
        local readers, the local log tail and the local kill path already use),
        removes the cross-user transfer entirely, and keeps the job signalable by the
        poller because it runs under this same uid.

        The prompt file is deliberately NOT unlinked: the job is detached
        (setsid+nohup) and may not have opened it yet, so deleting it here would race
        the runner.
        """
        if not os.path.exists(LOCAL_RUNNER):
            msg = "local runner missing: %s" % LOCAL_RUNNER
            log(msg, "ERROR")
            self.record("dispatch-failed", rid, msg[:120])
            return
        pf = os.path.join(JOB_DIR, "%s.prompt" % juuid)
        try:
            with open(pf, "w", encoding="utf-8") as fh:
                fh.write(prompt)
        except OSError as e:
            log("cannot stage prompt for job %s: %s" % (juuid, e), "ERROR")
            self.record("dispatch-failed", rid, str(e)[:120])
            return
        cmd = ["setsid", "nohup", "bash", DISPATCHER, juuid,
               "python3", LOCAL_RUNNER, pf]
        # A SELF-target job is still a task-bus job: it must receive the same criterion and
        # workspace the remote path ships, or the runner runs blind and the executor cannot
        # self-check. Missing this is why row 229 ran, did nothing, and still exited 0.
        env = dict(os.environ)
        _parsed = []
        crit = ((row or {}).get("success_criterion") or "").strip()
        if crit:
            env["SUCCESS_CRITERION"] = crit
            for _s in split_checks(crit):
                _ck, _pr = parse_check(_s)
                if _ck:
                    _parsed.append({"kind": _ck[0], "args": list(_ck[1])})
            if _parsed:
                env["TASKBUS_CRITERION_JSON"] = json.dumps(_parsed)
        _cwd = (row or {}).get("workspace_dir") or ""
        _cwd = _cwd.strip() if isinstance(_cwd, str) else ""
        if _cwd:
            env["TASKBUS_JOB_CWD"] = _cwd
        log("row %s: local dispatch shipping %d criterion check(s) + cwd=%s"
            % (rid, len(_parsed), _cwd))
        try:
            subprocess.run(cmd, env=env, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, start_new_session=True,
                           check=False)
            log("dispatched job %s for row %s locally on %s" % (juuid, rid, SELF_HOST))
        except OSError as e:
            log("local dispatch FAILED for job %s (row %s): %s" % (juuid, rid, e), "ERROR")
            self.record("dispatch-failed", rid, str(e)[:120])

    def dispatch(self, juuid, rid):
        """Run the configured executor for a claimed row via the M2M dispatcher.

        Detached (setsid+nohup) so it survives this process, with the log
        secret-scrubbed by dispatch-job.sh. The poller does NOT wait here: the
        next cycle reconciles via the job state file.
        """
        cmd = ["setsid", "nohup", "bash", DISPATCHER, juuid, EXECUTOR, str(rid)]
        try:
            subprocess.run(cmd, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, start_new_session=True,
                           check=False)
            log("dispatched job %s for row %s via %s" % (juuid, rid, EXECUTOR))
        except OSError as e:
            log("dispatch FAILED for job %s (row %s): %s" % (juuid, rid, e), "ERROR")
            self.record("dispatch-failed", rid, str(e))

    # ---- phase 0: Cancelled is terminal and always wins (scope section 2.4 / 3)
    def enforce_cancellations(self, rows):
        """A Cancelled row must not keep running. If a local job is alive for a
        cancelled row, stop it. This is checked before anything else each cycle.
        """
        import signal
        for row in rows:
            if st(row) != "Cancelled":
                continue
            rid = row["id"]
            uu = job_uuid_of(row)
            target = node_for(row)
            if target and not is_local_node(target):
                # Cancelled wins on a REMOTE machine too. But only act when the node is
                # REACHABLE and the job is definitively still running - killing blind is
                # how you stop the wrong thing.
                rstate, rreach = remote_job_state(target, uu)
                if not rreach:
                    log("row %s is Cancelled but %s is UNREACHABLE - not acting blind"
                        % (rid, target), "WARN")
                    self.record("cancel-unreachable", rid, "cannot reach %s" % target)
                    continue
                state = rstate or {}
                if state.get("status") != "running":
                    continue
                rpid = state.get("pid")
                ok, _out = ssh_node(target,
                    "pkill -TERM -g %s 2>/dev/null; pkill -TERM -f %s 2>/dev/null; echo done"
                    % (str(rpid), uu))
                log("row %s is Cancelled and its job runs on %s - stop issued (ok=%s)"
                    % (rid, target, ok), "WARN")
                self.record("cancelled-kill-remote", rid, "job %s on %s" % (uu, target))
                continue
            if not job_alive(uu):
                continue
            state = job_state(uu) or {}
            pid = state.get("pid")
            log("row %s is Cancelled but job %s (pid %s) is ALIVE - stopping it"
                % (rid, uu, pid), "WARN")
            try:
                os.killpg(os.getpgid(int(pid)), signal.SIGTERM)
            except (OSError, ValueError, TypeError):
                try:
                    os.kill(int(pid), signal.SIGTERM)
                except (OSError, ValueError, TypeError):
                    pass
            # Record the stop in local job state without touching the table row.
            state["status"] = "cancelled"
            state["ended_at"] = now_utc().strftime("%Y-%m-%dT%H:%M:%SZ")
            try:
                path = os.path.join(JOB_DIR, uu + ".json")
                tmp = path + ".tmp"
                with open(tmp, "w") as fh:
                    json.dump(state, fh, indent=2, sort_keys=True)
                os.replace(tmp, path)
            except OSError:
                pass
            self.record("cancelled-kill", rid, "job %s terminated" % uu)

    # ---- scheduling gate, extracted as a pure predicate so it is unit-testable.
    # It cannot be driven through a fixture row: special_ai_agent_calculation_rule is a
    # READ-ONLY LOOKUP, so the API silently ignores attempts to seed it. The rule only
    # appears once a model is chosen on the row.
    def schedule_verdict(self, row, now=None):
        """Returns (ok, kind, detail). ok=False means DO NOT claim, with a reason."""
        now = now or now_utc()
        due = parse_baserow_ts(row.get("start_task_at"))
        if due and now < due:
            return False, "not-yet-due", "start_task_at=%s" % due.strftime("%Y-%m-%dT%H:%MZ")
        if ACTIVITY_POLICY != "avoid_peak":
            return True, "ok", "policy=none"
        # Liege directive 2026-10-24: HIGH priority work bypasses the global avoid
        # window and runs regardless of the hour. Cost protection is unchanged for
        # every other priority (medium/low/blank still wait for off-peak).
        if str(fval(row, "priority") or "").strip().lower() == "high":
            return True, "ok", "priority=high bypasses the global avoid window"
        # GLOBAL window governs. The per-row rate rule is still parsed, but only for
        # reporting - it no longer gates, so an unparseable or missing rule can no longer
        # affect when jobs run.
        if in_peak(now, GLOBAL_AVOID_WINDOWS):
            nxt = next_offpeak(now, GLOBAL_AVOID_WINDOWS)
            return False, "peak-defer", ("inside the global avoid window (00:00-14:00 UTC); "
                                         "next runnable %s"
                                         % (nxt.strftime("%Y-%m-%dT%H:%MZ") if nxt else "?"))
        wins, _status = parse_peak_windows(row.get("special_ai_agent_calculation_rule"))
        return True, "ok", ("runnable; row rule parsed windows=%d" % len(wins))

    # ---- terminal jobs: report the outcome (scope section 6)
    def report_outcome(self, row, state, target=None):
        """A dispatched job reached a terminal state - write the result back.

        WITHOUT THIS the row would sit InProgress with a dead job, look stuck once
        the lease expired, be requeued and eventually Blocked - silently throwing
        away work that actually completed. This is the step that closes the loop.
        """
        rid = row["id"]
        fresh = self.bus.get_row(rid)
        if st(fresh) == "Cancelled":
            log("row %s is Cancelled - not reporting a job outcome" % rid)
            self.record("outcome-skipped-cancelled", rid, "row cancelled mid-flight")
            return
        node = target          # the node that RAN the job (NOT the status)
        tail = (remote_log_tail(node, job_uuid_of(fresh)) if node
                else job_log_tail(job_uuid_of(fresh)))
        attest = {}
        if state.get("status") == "done":
            allowed, reasons, ver_evidence = completion_guard(
                fresh, node=node, exit_code=state.get("exit_code"))
            if allowed:
                status_target = "Completed"
                # The dispatcher's ATTESTATION. Written here, and ONLY on a pass.
                attest = {"verification_result": "Passed", "verified_by": ME}
                head = ("job finished (exit 0); dispatcher INDEPENDENT verification "
                        "PASSED -> wrote verification_result=Passed, verified_by=%s" % ME)
            else:
                status_target = "HumanReviewNeeded"
                head = ("job finished (exit 0) but the completion guard did NOT pass "
                        "(Option B: manual override required): " + "; ".join(reasons))
            if ver_evidence:
                head += ("\n--- dispatcher verification evidence (on node %s) ---\n"
                         % node) + "\n".join(ver_evidence)
        elif state.get("exit_code") == 75:
            # EX_TEMPFAIL: infrastructure could not run the task, so it was never
            # attempted. Do NOT Block it and do NOT consume the attempt - undo the
            # increment the claim made, and put it back to New. Without this, a
            # missing credential silently Blocks real tasks one per cycle.
            back = max(num(fresh, "attempts_taken", 1) - 1, 0)
            note = append_note(fresh.get("machines_response"),
                               "TEMPFAIL: executor could not run (exit 75). Attempt "
                               "refunded; the task was NOT attempted.\n"
                               "--- executor output (tail) ---\n" + job_log_tail(job_uuid_of(fresh)))
            self.bus.patch(rid, {"status": "New", "attempts_taken": back,
                                 "corresponding_machine_task_id": "",
                                 "machines_response": note},
                           "TEMPFAIL -> requeue, attempt refunded")
            self.record("outcome-tempfail", rid, "exit 75, refunded to attempts=%d" % back)
            return
        else:
            status_target = "Blocked"
            head = "job FAILED (exit_code=%s)" % state.get("exit_code")
        note = append_note(fresh.get("machines_response"),
                           head + "\n--- executor output (tail) ---\n" + tail)
        payload = {"status": status_target, "machines_response": note}
        payload.update(attest)
        self.bus.patch(rid, payload, "job terminal -> %s" % status_target)
        self.record("outcome-" + status_target.lower(), rid, head[:90])

    # ---- phase 1: reconcile stuck tasks (runs BEFORE intake)
    def reconcile(self, rows, index):
        for row in rows:
            if st(row) != "InProgress":
                continue
            rid = row["id"]
            if ONLY_ROWS and rid not in ONLY_ROWS:
                continue
            uu = job_uuid_of(row)
            target = node_for(row)
            if not target:
                log("row %s is InProgress but its machine is not dispatchable by .4 - "
                    "cannot reconcile" % rid, "WARN")
                self.record("reconcile-no-target", rid,
                            "assigned_to=%s" % str(fval(row, "assigned_to_machine")))
                continue

            # ---- UNREACHABLE SAFEGUARD (liege 2026-09-13, explicitly requested) -------
            # If the target cannot be READ, the state is UNKNOWN - not dead. Treating an
            # unreachable node as "no job running" would mark a live task stuck and
            # re-dispatch it, i.e. run the same task TWICE. On any transport failure we
            # retain the lease, change nothing, and retry next cycle.
            state, reachable = remote_job_state(target, uu)
            if not reachable:
                log("row %s: %s UNREACHABLE - treating as UNKNOWN, retaining lease, "
                    "NOT remediating" % (rid, target), "WARN")
                self.record("unreachable", rid, "cannot read job state on %s" % target)
                continue

            if state and state.get("status") == "running":
                alive = remote_pid_alive(target, state.get("pid"))
                if alive is None:
                    log("row %s: pid check on %s inconclusive - retaining lease"
                        % (rid, target), "WARN")
                    self.record("unreachable", rid, "pid check failed on %s" % target)
                    continue
                if alive:
                    continue          # genuinely in flight

            # A FINISHED job is an outcome to report, NOT a stuck task.
            if state and state.get("status") in ("done", "failed"):
                self.report_outcome(row, state, target)
                continue

            last = parse_baserow_ts(row.get("last_modified"))
            age_min = (now_utc() - last).total_seconds() / 60.0 if last else 10 ** 6
            if age_min <= LEASE_MINUTES:
                continue

            log("STUCK candidate row %s: InProgress, last_modified %.1f min old, no live job"
                % (rid, age_min))

            # Ladder step 1: ALWAYS re-read before acting.
            fresh = self.bus.get_row(rid)
            fs = st(fresh)
            if fs == "Cancelled":
                log("row %s is now Cancelled - clearing local state, no write" % rid)
                self.record("stuck-cancelled", rid, "row cancelled before remediation")
                continue

            # attempts_taken is incremented in the CLAIM path only (see intake), so
            # here it already equals the number of claims made. This branch must NOT
            # increment - doing so was the double-count. It only READS the value to
            # decide whether one more claim is permitted.
            taken = num(fresh, "attempts_taken", 0)
            cap = num(fresh, "max_attempts", DEFAULT_MAX_ATTEMPTS) or DEFAULT_MAX_ATTEMPTS
            note = append_note(fresh.get("machines_response"),
                               "stuck: InProgress with %.0f min idle and no live job; "
                               "remediating (claims used %s/%s)" % (age_min, taken, cap))
            if taken < cap:
                self.bus.patch(rid, {"status": "New", "machines_response": note},
                               "stuck -> requeue as New (claims used %s/%s)" % (taken, cap))
                self.record("stuck-requeue", rid, "claims used %s/%s" % (taken, cap))
            else:
                self.bus.patch(rid, {"status": "Blocked", "machines_response": note},
                               "stuck and the total-claim cap is reached -> Blocked")
                self.record("stuck-block", rid, "claims used %s/%s" % (taken, cap))

    # ---- phase 2: intake eligible work
    _rep_idx = None
    _rep_ts = 0.0

    def node_for_row(self, row):
        """Resolve the execution node for a row under the per-machine ownership filter."""
        idx = self.rep_index()
        mode = os.environ.get("POLLER_FILTER_MODE", "").strip().lower() or "dual"
        self_rep = os.environ.get("POLLER_SELF_REP", "").strip()
        self_rep = int(self_rep) if self_rep.isdigit() else None
        return resolve_target(row, idx, mode, self_rep, node_for(row))

    def rep_index(self):
        """rep row id -> node ip, from table 43, cached for 10 minutes."""
        now = time.time()
        if self._rep_idx is not None and (now - self._rep_ts) < 600:
            return self._rep_idx
        idx = {}
        try:
            d = self.bus._req("/api/database/rows/table/43/?user_field_names=true&size=200")
            rows = d.get("results", []) if isinstance(d, dict) else []
            for r in rows:
                ident = (r.get("legal_last_name") or "").strip()
                name = (r.get("legal_first_name") or "").strip()
                node = (DISPATCHABLE.get(ident) or DISPATCHABLE.get(name)
                        or _BY_IP.get(ident) or _BY_NAME.get(name) or _BY_NAME.get(ident))
                if node:
                    idx[int(r["id"])] = node
            log("rep index: %d machine representative(s) mapped to dispatchable nodes" % len(idx))
        except Exception as e:                      # noqa: BLE001 - never kill a cycle
            log("rep index unavailable (%s) - falling back per mode" % e, "WARN")
            idx = {}
        self._rep_idx, self._rep_ts = idx, now
        return idx

    def intake(self, rows, index):
        started = 0
        # MAX_PER_DAY was previously declared, parsed and reported but NEVER
        # enforced - the "cost cap" did not exist. It is enforced here.
        budget = min(MAX_PER_CYCLE, self.daily_remaining())
        if budget <= 0:
            log("daily claim cap reached (%s) - NOT claiming this cycle" % MAX_PER_DAY,
                "WARN")
            self.record("claim-blocked-daily-cap", 0,
                        "started_today has reached %s" % MAX_PER_DAY)
            return
        for row in rows:
            if started >= budget:
                break
            if st(row) not in CLAIMABLE_STATUSES:
                continue
            rid = row["id"]
            if ONLY_ROWS and rid not in ONLY_ROWS:
                continue
            # CROSS-MACHINE: assigned_to_machine is the TARGET, not the consumer. .4
            # dispatches to any node it can reach. A machine .4 cannot reach (e.g. the
            # ingress node, which .4 has no key for) is skipped, not claimed-then-failed.
            target_node, path_used, why = self.node_for_row(row)
            if not target_node:
                self.record("target-not-dispatchable", rid, why)
                continue
            if path_used == "legacy":
                log("row %s: claimed via LEGACY assigned_to_machine (%s) - no 6378 mapping yet"
                    % (rid, why), "WARN")

            # ---- SCHEDULING GATE (start_task_at + peak/off-peak), deterministic
            vok, vkind, vdetail = self.schedule_verdict(row)
            if not vok:
                log("row %s: %s (%s) - not claiming" % (rid, vkind, vdetail))
                self.record(vkind, rid, vdetail)
                continue
            due = None

            ok, bad, blocking = self.deps_verdict(row, index)
            if not ok:
                if blocking:
                    # scope section 7: Block the dependant, naming the blocking row.
                    fresh = self.bus.get_row(rid)
                    if st(fresh) == "New" and not self.bus.dry_run:
                        self.bus.patch(rid, {
                            "status": "Blocked",
                            "machines_response": append_note(
                                fresh.get("machines_response"),
                                "dependency %s is not satisfiable; blocking per scope section 7"
                                % ", ".join(str(x) for x in blocking))},
                            "unsatisfied blocking dependency -> Blocked")
                    self.record("dep-blocked", rid, "blocking deps %s" % blocking)
                else:
                    self.record("dep-wait", rid, "unsatisfied deps %s" % bad)
                continue

            # guardrail 6: never act on an unread row - full read, then re-verify.
            fresh = self.bus.get_row(rid)
            if st(fresh) not in CLAIMABLE_STATUSES:
                log("row %s changed to %s between scan and claim - skipping" % (rid, st(fresh)))
                self.record("claim-race", rid, "status was %s at re-read" % st(fresh))
                continue
            if not (fresh.get("ai_prompt") or "").strip():
                log("row %s has no ai_prompt - refusing to act on an unread/empty task" % rid)
                self.record("claim-empty", rid, "ai_prompt empty")
                continue

            # ---- FAIL-CLOSED CRITERION GATE (added 2026-09-20, row 123 defect 2) ----
            # Validate success_criterion with the SAME parser completion_guard()
            # will use, BEFORE any budget is spent. See criterion_verdict().
            cok, cproblems = criterion_verdict(fresh)
            if not cok:
                detail = "; ".join(cproblems)
                if not self.live:
                    log("DRY-RUN  would BLOCK row %s at the criterion gate: %s" % (rid, detail))
                    self.record("would-block-criterion", rid, detail)
                    continue
                note = append_note(
                    fresh.get("machines_response"),
                    "claim refused at the CRITERION GATE: success_criterion is not a "
                    "machine-readable verify directive, so this row could never be "
                    "verified even if the work were done. Problems: %s. Author the "
                    "criterion as exit_zero or as one or more space-separated "
                    "kind:value directives (file_exists:/abs/path, file_md5:/abs/path=hex, "
                    "file_sha256:/abs/path=hex, http_200:url, http_status:url=code, "
                    "git_ls_remote:repo@branch=sha)." % detail)
                self.bus.patch(rid, {"status": "Blocked", "machines_response": note},
                               "unparseable success_criterion -> Blocked (row 123 defect 2)")
                self.record("claim-blocked-criterion", rid, detail)
                continue


            # ---- FAIL-CLOSED CLAIM CAP GATE (added 2026-09-14, defect row 82) ----
            # A row may be CLAIMED only while attempts_taken is BELOW max_attempts.
            # Before this gate the cap was read purely to format a note, so
            # attempts_taken could exceed max_attempts without bound (proven: a probe
            # drove intake() with rows at 2/2, 3/2 and 9/2 and all three were reported
            # claimable). reconcile() already enforced the cap on the STUCK path; this
            # closes the same hole on the CLAIM path.
            # Dry-run must report the refusal too, otherwise the dry run lies about
            # what live mode would do.
            taken_now = num(fresh, "attempts_taken", 0)
            cap_now = (num(fresh, "max_attempts", DEFAULT_MAX_ATTEMPTS)
                       or DEFAULT_MAX_ATTEMPTS)
            if taken_now >= cap_now:
                detail = "claims used %s/%s" % (taken_now, cap_now)
                if not self.live:
                    log("DRY-RUN  would BLOCK row %s at the claim cap: %s" % (rid, detail))
                    self.record("would-block-cap", rid, detail)
                    continue
                note = append_note(
                    fresh.get("machines_response"),
                    "claim refused: attempts_taken %s has reached max_attempts %s; row "
                    "is not claimable again (claim cap enforced at intake, defect row 82)"
                    % (taken_now, cap_now))
                self.bus.patch(rid, {"status": "Blocked", "machines_response": note},
                               "claim cap reached -> Blocked (%s)" % detail)
                self.record("claim-blocked-cap", rid, detail)
                continue

            if not self.live:
                log("DRY-RUN  would CLAIM row %s: %s" % (rid, (fresh.get("short_description") or "")[:80]))
                self.record("would-claim", rid, (fresh.get("short_description") or "")[:80])
                started += 1
                continue

            # FAIL-CLOSED GATE: live mode must never claim what it cannot execute.
            if not EXECUTOR:
                log("REFUSING to claim row %s: no executor configured. Set EXECUTOR= "
                    "in %s. Live mode is fail-closed by design."
                    % (rid, EXEC_CONF), "ERROR")
                self.record("claim-refused-no-executor", rid,
                            "no executor in " + EXEC_CONF)
                continue

            juuid = "td-taskbus-" + uuidlib.uuid4().hex[:12]
            # THE single increment site. Counts CLAIMS, so max_attempts caps the
            # TOTAL number of claims (liege directive 2026-09-12), matching row 5
            # ai_prompt: "On pickup: set status=InProgress, increment attempt".
            # reconcile() must never increment as well - doing so previously
            # double-counted and let attempts_taken exceed max_attempts (proven on
            # row 66: values 1, 2, 3 against a cap of 2).
            taken = num(fresh, "attempts_taken", 0) + 1
            self.bus.patch(rid, {
                "status": "InProgress",
                "attempts_taken": taken,
                "corresponding_machine_task_id": "job:" + juuid,
                "machines_response": append_note(
                    fresh.get("machines_response"),
                    "claimed by %s; dispatch job %s; retries used %s of %s"
                    % (ME, juuid, taken, num(fresh, "max_attempts", DEFAULT_MAX_ATTEMPTS)))},
                "claim -> InProgress")
            desc = (fresh.get("short_description") or "").upper()
            is_fixture = any(m.upper() in desc for m in FIXTURE_MARKERS)
            self.record("claimed-fixture" if is_fixture else "claimed", rid, juuid)
            # ROW 123 DEFECT 3: freeze the criterion BEFORE dispatch, so
            # verification can never be graded against a criterion that was
            # edited after the job started.
            if write_criterion_snapshot(fresh, juuid):
                log("row %s: criterion snapshot frozen for job %s" % (rid, juuid))
            else:
                log("row %s: could NOT freeze a criterion snapshot for job %s - "
                    "verification will fall back to the live row" % (rid, juuid), "WARN")
            self.dispatch_remote(target_node, juuid, rid, fresh)
            started += 1

    # ---- heartbeat + cost accounting (guardrail 3)
    def daily_remaining(self):
        """How many more REAL claims are allowed today (0 if the cap is spent)."""
        today = now_utc().strftime("%Y-%m-%d")
        try:
            with open(STATE_FILE) as fh:
                state = json.load(fh)
        except (OSError, ValueError):
            return MAX_PER_DAY
        if state.get("date") != today:
            return MAX_PER_DAY
        return max(MAX_PER_DAY - int(state.get("started_today") or 0), 0)

    def heartbeat(self, summary):
        today = now_utc().strftime("%Y-%m-%d")
        state = {}
        if os.path.exists(STATE_FILE):
            try:
                with open(STATE_FILE) as fh:
                    state = json.load(fh)
            except (OSError, ValueError):
                state = {}
        if state.get("date") != today:
            state = {"date": today, "started_today": 0}
        state["started_today"] = state.get("started_today", 0) + summary.get("claims", 0)
        state["last_cycle"] = now_utc().strftime("%Y-%m-%dT%H:%M:%SZ")
        state["last_summary"] = summary
        tmp = STATE_FILE + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(state, fh, indent=2, sort_keys=True)
        os.replace(tmp, STATE_FILE)   # atomic
        return state

    def cycle(self):
        """Single-cycle entry point, guarding ONE-CYCLE-AT-A-TIME.

        The invariant that matters is that two cycles never run concurrently, NOT
        that only one process exists. Holding this lock for the process lifetime
        looked equivalent and was WRONG: a long-running daemon held it forever, so
        every manual, cron or test invocation was silently refused ("another poller
        instance holds the lock") and appeared to succeed while doing nothing.
        Taken here and released in finally, so a daemon is idle-but-unlocked between
        cycles and an operator can still run one.
        """
        summary = {"claims": 0, "stuck": 0, "skipped": 0}
        lock_fh = open(LOCK_FILE, "w")
        try:
            fcntl.flock(lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            log("another cycle is already in progress - skipping this cycle", "WARN")
            lock_fh.close()
            return {"skipped_locked": True}
        try:
            return self._cycle_locked(summary)
        finally:
            # Released even on exception, and by the kernel on process death.
            fcntl.flock(lock_fh, fcntl.LOCK_UN)
            lock_fh.close()

    def _cycle_locked(self, summary):
        if self.paused():
            log("KILL SWITCH present (%s) - exiting without action" % KILL_SWITCH, "WARN")
            return {"paused": True}

        # CONTEXT GATE (2026-10-26). The poller must not claim or dispatch anything unless
        # the constitution, the archetype and this node's environment file are present and
        # non-empty. Logged every cycle with size + digest so "is it loaded" is answerable
        # from the journal. Fail closed on ACTING, not on the process: the daemon stays up
        # and re-checks next cycle rather than entering a restart loop.
        ctx_ok, ctx_detail = load_agent_context()
        log("context: %s" % ctx_detail)
        if not ctx_ok:
            log("CONTEXT INCOMPLETE - refusing to act this cycle", "WARN")
            self.record("context-missing", 0, ctx_detail)
            return {"context_missing": True}

        # Re-acquire the identity each cycle: a JWT has a lifetime, and this process runs for
        # days. Cheap (one Vault read + one login) and it means a rotated poller password is
        # picked up without a restart.
        self.bus.refresh_auth()

        rows = self.bus.all_rows()
        index = {r["id"]: r for r in rows}

        # The ONE place the actable rule is enforced. Everything downstream sees
        # only New/InProgress rows, so no other status can ever be claimed or
        # remediated, regardless of what the loops below do.
        actable = [r for r in rows if st(r) in ACTABLE_STATUSES]
        ignored = sorted(r["id"] for r in rows if st(r) not in ACTABLE_STATUSES)
        log("cycle start: %d rows read, %d not Closed, %d actable (New/InProgress)"
            % (len(rows), sum(1 for r in rows if st(r) != "Closed"), len(actable)))
        log("not actable, ignored: %s" % ignored)

        # Safety stop, deliberately OUTSIDE the actable set: it stops a job that is
        # already running for a Cancelled row and writes only LOCAL job state - it
        # never patches the table row. See the build doc for why this is not
        # considered "acting on" a non-actable status.
        self.enforce_cancellations(rows)

        self.reconcile(actable, index)       # reconciliation FIRST
        self.intake(actable, index)

        summary["stuck"] = sum(1 for a in self.actions if a["kind"].startswith("stuck"))
        # NOTE: dry-run "would-claim" actions must NOT count toward the daily cap.
        # They previously did, so a day of dry-runs exhausted the budget and would
        # have blocked every real claim.
        summary["claims"] = sum(1 for a in self.actions if a["kind"] == "claimed")
        summary["would_claim"] = sum(1 for a in self.actions if a["kind"] == "would-claim")
        state = self.heartbeat(summary)
        log("cycle end: actions=%d, started_today=%s"
            % (len(self.actions), state.get("started_today")))
        return state


# ------------------------------------------------------------------------- main

def main():
    global LEASE_MINUTES, MAX_PER_CYCLE, MAX_PER_DAY, ONLY_ROWS, ACTIVITY_POLICY
    ap = argparse.ArgumentParser(description="Task-Bus Poller / Watchdog (.4)")
    ap.add_argument("--live", action="store_true",
                    help="actually write to the table (default is dry-run)")
    ap.add_argument("--once", action="store_true", help="run one cycle and exit")
    ap.add_argument("--lease-minutes", type=int, default=LEASE_MINUTES,
                    help="staleness window for stuck detection (testability)")
    ap.add_argument("--max-per-cycle", type=int, default=MAX_PER_CYCLE)
    ap.add_argument("--max-per-day", type=int, default=MAX_PER_DAY)
    ap.add_argument("--activity-policy", choices=POLICIES, default=ACTIVITY_POLICY,
                    help="avoid_peak (default, run only in the cheaper window) or none")
    ap.add_argument("--only-row", type=int, action="append", default=[],
                    help="restrict action to these row ids (repeatable; safe live testing)")
    ap.add_argument("--interval", type=int, default=POLL_INTERVAL)
    ap.add_argument("--status", action="store_true", help="show kill switch / caps / heartbeat and exit")
    ap.add_argument("--stale-report", action="store_true",
                    help="READ-ONLY: summarise non-terminal rows by AGE BAND and exit; "
                         "distinguishes genuinely blocked from forgotten")
    ap.add_argument("--stale-annotate", action="store_true",
                    help="OPT-IN escalation: with --live, append ONE idempotent "
                         "STALE-SWEEP note to FORGOTTEN rows (default: OFF)")
    ap.add_argument("--stale-days", type=int, default=STALE_DAYS,
                    help="age threshold in days for the staleness bands")
    ap.add_argument("--complete-check", type=int, metavar="ROW",
                    help="independently verify a row's success_criterion on its target "
                         "node and print the verdict (NOTE: this now PROBES the node)")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--executor", metavar="PATH",
                    help="executor script invoked as <executor> <row_id> on claim")
    args = ap.parse_args()

    if args.status:
        stt = {}
        if os.path.exists(STATE_FILE):
            try:
                with open(STATE_FILE) as fh:
                    stt = json.load(fh)
            except (OSError, ValueError):
                stt = {}
        out = {"paused": os.path.exists(KILL_SWITCH),
               "executor": (args.executor or load_executor()),
               "kill_switch_path": KILL_SWITCH,
               "max_per_cycle": MAX_PER_CYCLE,
               "max_per_day": MAX_PER_DAY,
               "lease_minutes": LEASE_MINUTES,
               "me": ME,
               "state": stt}
        print(json.dumps(out, indent=2))
        return 0

    bus = TaskBus(dry_run=not args.live)

    if args.stale_report or args.stale_annotate:
        rows = bus.all_rows()
        bands, forgotten = stale_sweep(rows, stale_days=args.stale_days)
        out = {"stale_days": args.stale_days,
               "total_rows": len(rows),
               "bands": bands,
               "forgotten": forgotten,
               "written": 0}
        if args.stale_annotate:
            if not args.live:
                out["refused"] = ("--stale-annotate requires --live; refusing to "
                                  "write. Re-run with --live to escalate.")
                print(json.dumps(out, indent=2))
                return 3
            written = 0
            for item in forgotten:
                rid = item["row"]
                fresh = bus.get_row(rid)
                existing = fresh.get("machines_response") or ""
                if STALE_MARKER in existing:
                    continue          # idempotent: annotate once, never spam
                note = append_note(
                    existing,
                    "%s STALE: %s. Escalation is a REVIEW REMINDER, not a verdict. "
                    "If this is genuinely blocked, record the blocker; if it is "
                    "forgotten, close, cancel or re-scope it."
                    % (STALE_MARKER, item["detail"]))
                bus.patch(rid, {"machines_response": note},
                          "stale sweep annotation")
                written += 1
            out["written"] = written
        print(json.dumps(out, indent=2))
        return 0

    if args.complete_check:
        row = bus.get_row(args.complete_check)
        allowed, reasons, evidence = completion_guard(row)
        print(json.dumps({"row": args.complete_check,
                          "status": st(row),
                          "completed_allowed": allowed,
                          "refusal_reasons": reasons,
                          "self_verification_evidence": evidence}, indent=2))
        return 0 if allowed else 3

    LEASE_MINUTES = args.lease_minutes
    MAX_PER_CYCLE = args.max_per_cycle
    MAX_PER_DAY = args.max_per_day
    ACTIVITY_POLICY = args.activity_policy
    ONLY_ROWS = set(args.only_row)
    global EXECUTOR
    EXECUTOR = args.executor or load_executor()
    if args.live:
        log("executor: %s" % (EXECUTOR or "NONE - live mode will REFUSE to claim"),
            "INFO" if EXECUTOR else "WARN")
    if ONLY_ROWS:
        log("row filter ACTIVE - acting only on rows %s" % sorted(ONLY_ROWS), "WARN")
    poller = Poller(bus, live=args.live)
    mode = "LIVE" if args.live else "DRY-RUN"
    log("taskbus-poller starting in %s mode (once=%s)" % (mode, args.once))

    if args.once:
        res = poller.cycle()
        if args.json:
            print(json.dumps({"mode": mode, "actions": poller.actions, "state": res}, indent=2))
        return 0

    while True:
        try:
            poller.actions = []
            poller.cycle()
        except Exception as e:
            log("cycle error: %s" % e, "ERROR")
        time.sleep(args.interval)


if __name__ == "__main__":
    sys.exit(main())
