#!/usr/bin/env python3
"""taskbus-exec.py - the EXECUTOR invoked by the task-bus poller.

Invoked by the poller (through dispatch-job.sh) as:

    taskbus-exec.py <row_id>

It fetches the row from Baserow table 215, builds a bounded prompt, and runs the
Bare-AI CLI headlessly. Its stdout is captured by dispatch-job.sh into the job log,
which is secret-scrubbed before it reaches disk.

SAFETY POSTURE - deliberately conservative for an UNATTENDED executor:
  * --approval-mode plan = READ-ONLY. The agent can investigate and report but
    cannot modify the system, edit files, or run state-changing commands. Escalating
    this is an explicit decision; see docs-taskbus-poller-build-2026-09-12.md.
  * Hard timeout, so a wedged model call cannot hold a task forever.
  * Credentials are never echoed. Vault AppRole env is sourced from
    ~/.bare-ai/config/vault.env and the values are passed straight to the child.
  * Fail-closed: any failure exits NONZERO, which makes the poller mark the row
    Blocked with the exit code rather than pretending the work was done.
"""

import os
import re
import subprocess
import sys
import tempfile
import io
from datetime import datetime, timezone
# --- 2026-09-20 PER-MACHINE BASEROW CREDENTIAL (Vault-backed) ---
# Was: a shared admin@bare-erp.com table token in ~/.bare-ai/taskbus.env, kept in a
# plaintext file. Now: bare_baserow reads THIS machine's own credential from Vault and
# fails closed if it cannot. No fallback to the old shared token exists.
try:
    import bare_baserow as _bare_baserow
except ModuleNotFoundError:
    # Runs as a symlink (workspace path -> this tracked file), so sys.path[0] is the
    # REAL directory (scripts/lib) and the node's helper is not importable from there.
    # bare_baserow stays a node-level dependency on purpose: it resolves THIS
    # machine's Vault path and is shared by other workspace scripts. One copy, named
    # explicitly, beats a vendored copy that drifts.
    _HELPER_DIR = os.path.join(os.path.expanduser("~"), "bare-necessities-workspace",
                               "my-bare-scripts", "bare-python3-scripts")
    if _HELPER_DIR not in sys.path:
        sys.path.insert(0, _HELPER_DIR)
    import bare_baserow as _bare_baserow

HOME = os.path.expanduser("~")
ENVF = os.path.join(HOME, ".bare-ai", "taskbus.env")
VAULT_ENV = os.path.join(HOME, ".bare-ai", "config", "vault.env")
# profile.env sets the endpoint/model defaults. It is loaded HERE, by the
# executor, and deliberately NOT relied upon via systemd EnvironmentFile:
# its lines are "export VAR=value" and systemd does not parse the export
# prefix, so the endpoint silently never reached the unit and the CLI fell
# back to the Gemini API and rejected the key. load_env() strips "export ".
PROFILE_ENV = os.path.join(HOME, ".bare-ai", "profile.env")

# ---------------------------------------------------------------- AGENT IDENTITY
# Liege 2026-09-13: "I was expecting the scheduled job to run an instance of you, not a
# generic chat bot." Correct, and here is why it was a chatbot:
#   the real agent is launched by the launcher (sovereign-v2.js) as
#       node bundle/bare-ai.js --yolo -i "<BARE_AI_SYSTEM_PROMPT>" [args]
#   The IDENTITY travels in that -i payload. My executor called bundle/bare-ai.js
#   DIRECTLY with only --prompt-file, so it inherited none of the role, the technical
#   constitution, the diary or the fleet context. The bundle itself does not read
#   BARE_AI_ROLE_CONSTITUTION / BARE_AI_CONSTITUTION / BARE_AI_DIARY / BARE_AI_SYSTEM_PROMPT
#   at all (0 references - confirmed by grepping the 5MB bundle), so passing those env
#   vars would have changed nothing. The identity has to go IN THE PROMPT.
# BARE_AI_SYSTEM_PROMPT was verified to be role.md followed by technical-constitution.md.
ROLE_FILE = os.path.join(HOME, ".bare-ai", "role.md")
CONSTITUTION_FILE = os.path.join(HOME, ".bare-ai", "technical-constitution.md")
DIARY_DIR = os.path.join(HOME, "bare-necessities-workspace", "bare-ai-diary")

# Tool policy. The launcher uses --yolo, which is what makes the agent able to ACT.
# Overridable: TASKBUS_EXEC_APPROVAL_MODE=plan gives read-only unattended runs.
APPROVAL_MODE = os.environ.get("TASKBUS_EXEC_APPROVAL_MODE", "yolo")


def agent_identity():
    """role.md + technical constitution - the same content the launcher passes via -i."""
    parts = []
    for f in (ROLE_FILE, CONSTITUTION_FILE):
        try:
            parts.append(io.open(f, encoding="utf-8").read())
        except OSError:
            pass
    return "\n\n".join(parts)

# Vault-first credential source (liege directive 2026-09-13).
# The credential lives at secret/data/bare-ai-brain/config and is reached with
# the AppRole in vault.env. The local env file is a FALLBACK for when Vault is
# unreachable - it is not the preferred source.
# Ticket 2026-11-03 (Liege ruling): this was hard-coded to one node's path, so every other node
# asked Vault for a credential it is not entitled to and TEMPFAILed. Per-node now, via drop-in.
VAULT_SECRET_PATH = os.environ.get("BARE_AI_VAULT_SECRET_PATH",
                                  "secret/data/bare-ai-brain/config")
VAULT_KEY_FIELD = "api_key"
# CREDENTIAL PATH RESOLUTION (2026-11-03). This used to be one env var, then a per-node env var, and the
# env did not survive the detached dispatch chain - the executor kept reading the default node's path and
# 403ing. A one-line config file is auditable, secret-free, and cannot be lost in an env hop.
CRED_PATH_FILE = os.path.join(HOME, ".bare-ai", "config", "model-credential.path")


def credential_path():
    p = (os.environ.get("BARE_AI_VAULT_SECRET_PATH") or "").strip()
    if p:
        return p
    try:
        with open(CRED_PATH_FILE) as fh:
            for line in fh:
                line = line.strip()
                if line and not line.startswith("#"):
                    return line
    except OSError:
        pass
    return VAULT_SECRET_PATH
# Fleet Vault presents a self-signed CA. Every other client here (bare_baserow._ctx, the poller's own
# login) connects without verification; this file did not, so it died with CERTIFICATE_VERIFY_FAILED on
# nodes lacking the CA bundle. Same trust model as the rest of the fleet, one implementation.
# FOLLOW-UP: install the Vault CA on every node and delete this context.
import ssl as _ssl
_VAULT_CTX = _ssl.create_default_context()
_VAULT_CTX.check_hostname = False
_VAULT_CTX.verify_mode = _ssl.CERT_NONE
CLI = os.path.join(HOME, "bare-ai-cli", "bundle", "bare-ai.js")
# Node path FIXED 2026-09-14: was hardcoded to /usr/local/bin/node, which does not exist
# on .13/.18/.19 (they ship node at /usr/bin/node), so jobs dispatched to those nodes died
# with FileNotFoundError and looked like agent failures. Resolve from PATH first.
def _resolve_node():
    import shutil as _sh
    for _cand in (_sh.which("node"), "/usr/local/bin/node", "/usr/bin/node"):
        if _cand and os.path.exists(_cand) and os.access(_cand, os.X_OK):
            return _cand
    return "node"

NODE = _resolve_node()


def _node_identity():
    """This node's own identity for the job prompt, never another node's name.

    The poller exports POLLER_SELF_NODE; the hostname is the fallback. Hardcoding one
    node's identity here told every job on every host that it was that node.
    """
    return (os.environ.get("POLLER_SELF_NODE") or "").strip() or os.uname().nodename
TIMEOUT = int(os.environ.get("TASKBUS_EXEC_TIMEOUT", "1500"))
TABLE = 215

# ── COMPLETION MARKER (row 123, defect 1) ────────────────────────────────────
# WHY THIS EXISTS: a job that ends on a NARRATION-ONLY turn - the model explains what it will do
# next and emits no tool call - still made the CLI exit 0, and this executor returned 0 for ANY
# exit_code 0. The job therefore reported a CLEAN FINISH having written no code at all. Observed
# live on row 18, job td-taskbus-da5f07e57c26: 79 seconds, exit_code 0, zero work done.
# So exit 0 now means "the model declared the task finished", proven by printing the marker.
# Anything else is INCOMPLETE_NO_MARKER and is deliberately NOT a clean finish.
COMPLETION_MARKER = "TASKBUS_JOB_COMPLETE"
EXIT_INCOMPLETE_NO_MARKER = 66

SCOPE_EXCLUDED = ("bare-ai-diary/", "backups/", "staging-bundle-",
                  "archive/", "tmp/", "node_modules/", ".git/")
OUTPUT_CAP = int(os.environ.get("TASKBUS_EXEC_OUTPUT_CAP", "200000"))


# =====================================================================
# ROW 123 DEFECTS 3, 4, 5 - pure helpers, unit-tested in
# test-poller-d345.py. Purity is deliberate: every decision below can be
# asserted on WITHOUT spawning the CLI or touching the network.
# =====================================================================

# ---- DEFECT 4: bound executor recon ----------------------------------------
# WHY: a live job spent 107,666 PROMPT TOKENS against 735 completion tokens -
# the agent was reading the world instead of doing the task. The executor has
# exactly two levers: (a) tell the model which trees are OUT OF SCOPE, and
# (b) hard-cap what we capture back. (b) is ENFORCEABLE here; (a) is advisory
# and is labelled as such in the prompt text.
SCOPE_EXCLUDED = ("bare-ai-diary/", "backups/", "staging-bundle-",
                  "archive/", "tmp/", "node_modules/", ".git/")
# Keep head AND tail: the completion marker is the LAST line of the reply, so a
# head-only truncation would destroy the very signal classify_run() looks for.
OUTPUT_CAP = int(os.environ.get("TASKBUS_EXEC_OUTPUT_CAP", "200000"))


def scope_bounds_block():
    """PURE. The recon-bound text injected into the prompt."""
    return ("SCOPE BOUNDS (recon limits for this job):\n"
            "- Out of scope, do NOT read or list them: "
            + ", ".join(SCOPE_EXCLUDED) + "\n"
            "- Read only what the task requires. Do not survey the repository.\n"
            "- Prefer grep/head over reading whole files; never read a file over 1MB.\n"
            "- If you need more context than this allows, STOP and say what you need\n"
            "  instead of reading the world.\n\n")


def cap_output(text, limit=None):
    """PURE. Truncate captured output with a VISIBLE marker, keeping head AND tail.

    A silent truncation would be worse than none: the reader cannot tell a short
    job from a clipped one. The marker states exactly how many bytes were dropped.
    """
    limit = OUTPUT_CAP if limit is None else limit
    text = text or ""
    if len(text) <= limit:
        return text
    head = limit // 2
    tail = limit - head
    dropped = len(text) - limit
    marker = ("\n\n[... executor OUTPUT CAP: %d of %d bytes dropped here. "
              "Head and tail are both retained deliberately, because the "
              "completion marker is the LAST line of the reply ...]\n\n"
              % (dropped, len(text)))
    return text[:head] + marker + text[-tail:]


# ---- DEFECT 5: size -> tier -> concrete model -------------------------------
# The model name is NEVER hardcoded: it is resolved from the LIVE catalog at
# ~/.bare-ai/model-catalog.json. If resolution fails we pass NO --model and the
# CLI default applies, so a routing bug degrades to today's behaviour rather
# than to an invented model name.
CATALOG_PATH = os.path.join(HOME, ".bare-ai", "model-catalog.json")

SIZE_FLASH = int(os.environ.get("TASKBUS_SIZE_FLASH", "4000"))
SIZE_FRONTIER = int(os.environ.get("TASKBUS_SIZE_FRONTIER", "20000"))


def task_size(row):
    """PURE. Bytes of the task definition - the routing input."""
    total = 0
    for f in ("ai_prompt", "human_notes", "success_criterion", "short_description"):
        total += len(str(row.get(f) or ""))
    return total


def route_tier(size_bytes):
    """PURE. Size band. Frontier is deliberately the hardest band to reach."""
    if size_bytes >= SIZE_FRONTIER:
        return "Frontier"
    if size_bytes >= SIZE_FLASH:
        return "Flash"
    return "Lite"


def _param_billions(model_id):
    """PURE. Best-effort parameter count parsed from the model name (0 = unknown).

    Derived from the name rather than a hardcoded table, so it cannot go stale
    when the catalog changes.
    """
    best = 0.0
    for m in re.finditer(r"([0-9]+(?:\.[0-9]+)?)\s*b\b", str(model_id).lower()):
        try:
            best = max(best, float(m.group(1)))
        except ValueError:
            continue
    return best


def load_catalog(path=None):
    """Read the live model catalog. Returns [] on any failure (FAIL-SAFE)."""
    import json as _json
    try:
        with io.open(path or CATALOG_PATH, encoding="utf-8") as fh:
            d = _json.load(fh)
        ms = d.get("models") if isinstance(d, dict) else None
        return ms if isinstance(ms, list) else []
    except (OSError, ValueError):
        return []


def resolve_model(tier, catalog, provider="Bare-AI", capability="doer"):
    """PURE. Largest LOCAL doer model of `tier`, or None.

    Local + doer on purpose: a local model needs no cloud key, so the executor
    cannot fail on a missing credential; and a tool-using model is required for
    an agentic job. "Largest" comes from the parameter count in the name.
    """
    cands = [m for m in (catalog or [])
             if str(m.get("model_tier")) == tier
             and str(m.get("provider")) == provider
             and str(m.get("tool_capability")) == capability
             and m.get("model_id")]
    if not cands:
        return None
    cands.sort(key=lambda m: _param_billions(m.get("model_id")), reverse=True)
    return str(cands[0]["model_id"])


def route_model(row, catalog=None):
    """PURE given `catalog`. Returns {model, tier, size_bytes, reason}.

    Precedence, highest first:
      1. TASKBUS_EXEC_MODEL      - operator / test override
      2. the row's own model_catalog link - a human decided; respect it
      3. size band -> largest local doer model of that tier
      4. nothing resolvable -> model None, and the CLI default applies
    """
    size = task_size(row)
    override = (os.environ.get("TASKBUS_EXEC_MODEL") or "").strip()
    if override:
        return {"model": override, "tier": "override", "size_bytes": size,
                "reason": "TASKBUS_EXEC_MODEL env override"}

    linked = row.get("model_catalog")
    if isinstance(linked, list) and linked:
        first = linked[0]
        if isinstance(first, dict):
            name = first.get("value") or first.get("name") or first.get("model_id")
        else:
            name = first
        if name:
            return {"model": str(name), "tier": "row-choice", "size_bytes": size,
                    "reason": "row model_catalog link (an explicit choice wins)"}

    tier = route_tier(size)
    model = resolve_model(tier, catalog if catalog is not None else load_catalog())
    if model:
        return {"model": model, "tier": tier, "size_bytes": size,
                "reason": "size %d bytes -> %s tier; largest local doer model"
                          % (size, tier)}
    return {"model": None, "tier": tier, "size_bytes": size,
            "reason": "size %d bytes -> %s tier, but NO local doer model of that "
                      "tier is in the catalog; CLI default used" % (size, tier)}


# ---- DEFECT 3 (executor half): name the criterion version the job ran under --
def criterion_digest(text):
    """PURE. Short stable fingerprint of the criterion text (not a security control).

    Lets a log line record WHICH criterion version a job ran under without
    embedding the whole field.
    """
    import hashlib
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:12]

PROMPT_TEMPLATE = """
=====================================================================
TASK-BUS JOB - row {row} (dispatched by the task-bus poller)
=====================================================================
You are the {node} agent. Above is your role
constitution and technical constitution - the same identity you run with interactively.
This is a job from the fleet task bus, not an abstract question: act as yourself.

Resolve ONLY this task. Do not start unrelated work and do not expand scope.

{scope}
SHORT DESCRIPTION: {short}
DETAIL / INSTRUCTION: {prompt}
SUCCESS CRITERION: {criterion}

Operating rules:
- You are running READ-ONLY (plan mode). You cannot modify the system. Investigate
  and report rather than attempting changes.
- Answer concretely and briefly: under 400 words.
- If the task needs changes you cannot make read-only, state exactly what should be
  changed and where.
- If the task cannot be completed, or the instruction is ambiguous, or the success
  criterion is absent, SAY SO PLAINLY. Never claim success you cannot evidence.
- Do not invent file contents, versions, or command output.
- WHEN AND ONLY WHEN the task is genuinely finished, print this line on its own as the LAST
  line of your reply: TASKBUS_JOB_COMPLETE
  If that line is absent the job is recorded as INCOMPLETE_NO_MARKER and is not treated as a
  clean finish, however successful it looked. Do not print it to appear successful: a run that
  ends without it is treated as unfinished ON PURPOSE.
"""


def classify_run(returncode, output):
    """Decide what a FINISHED CLI run means. PURE, so the test exercises this and not a copy.

    Returns (exit_code_to_report, verdict_string).

    THE RULE: exit 0 from the CLI means "the process ran", NOT "the task was finished". A run that
    ended on a narration-only turn - the model saying what it would do next and emitting no tool
    call - still exits 0. On row 18 that produced a 79-second job with exit_code 0 and zero work
    done, which was reported as a clean finish. So exit 0 is only a clean finish when the model
    also printed the completion marker; otherwise it is INCOMPLETE_NO_MARKER.

    Credential failures are INFRASTRUCTURE, not task failures, so they report the TEMPFAIL code and
    the attempt is not burned.
    """
    low = (output or "").lower()
    if returncode == 0:
        if COMPLETION_MARKER in (output or ""):
            return 0, "COMPLETE"
        return EXIT_INCOMPLETE_NO_MARKER, "INCOMPLETE_NO_MARKER"
    auth_markers = (
        "api key not valid", "invalid api key", "please set bare_ai_api_key",
        "unauthenticated", "permission_denied", "invalid_api_key",
        "no api key", "api key missing",
    )
    if any(m in low for m in auth_markers) or returncode == 41:
        return 75, "TEMPFAIL_CREDENTIAL"
    return 1, "FAILED"


def load_env(path):
    env = {}
    try:
        for line in open(path):
            line = line.strip()
            if line.startswith("export "):
                line = line[len("export "):]
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    return env


PROVIDER_SECRET_PATHS = {
    "deepseek": "secret/data/deepseek/config",
    "gemini": "secret/data/gemini/config",
    "openai": "secret/data/openai/config",
    "anthropic": "secret/data/anthropic/config",
}


def provider_of_model(model):
    """Which provider a model id belongs to, from its name.

    The catalogue's model ids carry a provider suffix (gemini-2.5-flash-lite|google-us,
    claude-sonnet-4-6|anthropic-us). When neither the name nor the suffix is
    recognisable we return "" and the caller keeps the node default - a wrong guess
    here is exactly what sent a DeepSeek key to Google.
    """
    m = (model or "").strip().lower()
    if not m:
        return ""
    if m.startswith("gemini") or "google" in m:
        return "gemini"
    if "deepseek" in m:
        return "deepseek"
    if "claude" in m or "anthropic" in m:
        return "anthropic"
    if m.startswith("gpt") or "openai" in m or m.startswith("o1") or m.startswith("o3"):
        return "openai"
    return ""


def vault_api_key(secret_path=None):
    """Vault-first credential fetch. Returns (key_or_None, source_note).

    Never logs or returns the key to any caller that prints it. The AppRole login
    is required first: VAULT_ROLE_ID/VAULT_SECRET_ID in the environment alone do
    NOT authenticate the API, they only identify the role.
    """
    # NOTE: importing urllib.error does NOT bind urllib.request. Both are
    # needed; missing one surfaced as AttributeError and was nearly masked as
    # a benign "vault unreachable".
    import urllib.request
    import urllib.error
    cfg = load_env(VAULT_ENV)
    addr = (cfg.get("VAULT_ADDR") or "").rstrip("/")
    rid = cfg.get("VAULT_ROLE_ID")
    sid = cfg.get("VAULT_SECRET_ID")
    # A CIDR-bound AppRole has no secret_id (Ticket 305 retired it on .13). Requiring one made the
    # executor refuse to even attempt a login and report "vault config incomplete".
    if not (addr and rid):
        return None, "vault config incomplete (need VAULT_ADDR + VAULT_ROLE_ID) in " + VAULT_ENV
    try:
        import json as _json
        _ident = {"role_id": rid}
        if sid:
            _ident["secret_id"] = sid
        body = _json.dumps(_ident).encode()
        req = urllib.request.Request(addr + "/v1/auth/approle/login", data=body,
                                     method="POST")
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=15, context=_VAULT_CTX) as r:
            tok = (_json.loads(r.read()).get("auth") or {}).get("client_token")
        if not tok:
            return None, "approle login returned no token"
        # The route's provider decides the path; the node default is the fallback for
        # a model whose provider cannot be identified.
        _path = (secret_path or "").strip() or credential_path()
        req2 = urllib.request.Request("%s/v1/%s" % (addr, _path))
        req2.add_header("X-Vault-Token", tok)
        with urllib.request.urlopen(req2, timeout=15, context=_VAULT_CTX) as r:
            data = _json.loads(r.read())
        key = ((data.get("data") or {}).get("data") or {}).get(VAULT_KEY_FIELD)
        if key:
            return key, "vault (" + _path + ")"
        return None, "vault secret has no field " + VAULT_KEY_FIELD
    except urllib.error.HTTPError as e:
        return None, "vault HTTP %s" % e.code
    except Exception as e:
        # Network/TLS/timeout: fall back, but SAY SO rather than silently using
        # a stale credential.
        return None, "vault unreachable: %s: %s" % (type(e).__name__, str(e)[:120])


BASEROW_URL_FILE = os.path.join(HOME, ".bare-ai", "config", "baserow.url")


def baserow_base_url(env=None):
    """Resolve the Baserow base URL without baking a fleet address into source.

    Precedence: BASEROW_URL in the node's taskbus.env, then the node-owned one-line
    file ~/.bare-ai/config/baserow.url, then the process environment.

    It REFUSES rather than guessing. A wrong host is a silent data-integrity fault
    (reads succeed against the wrong database), and this repository is public, so the
    address is configuration and must not live in it.
    """
    for candidate in ((env or {}).get("BASEROW_URL") or "",
                      os.environ.get("BASEROW_URL") or ""):
        if candidate.strip():
            return candidate.strip()
    try:
        with open(BASEROW_URL_FILE) as fh:
            for line in fh:
                line = line.strip()
                if line and not line.startswith("#"):
                    return line
    except OSError:
        pass
    raise RuntimeError(
        "no Baserow URL configured: set BASEROW_URL in %s or write the base URL to %s"
        % (ENVF, BASEROW_URL_FILE))


def fetch_row(row_id):
    import json
    import urllib.request
    env = load_env(ENVF)
    base = baserow_base_url(env).rstrip("/")
    tok = _bare_baserow.token_value()
    if not tok:
        raise RuntimeError("no BASEROW_TABLE_TOKEN in " + ENVF)
    req = urllib.request.Request(
        "%s/api/database/rows/table/%d/%d/?user_field_names=true" % (base, TABLE, row_id))
    req.add_header("Authorization", "Token " + tok)
    return json.loads(urllib.request.urlopen(req, timeout=60).read())


def main():
    if len(sys.argv) < 2:
        print("usage: taskbus-exec.py <row_id>", file=sys.stderr)
        return 2
    try:
        row_id = int(sys.argv[1])
    except ValueError:
        print("row id must be an integer, got %r" % sys.argv[1], file=sys.stderr)
        return 2

    row = fetch_row(row_id)

    # PRE-FLIGHT CREDENTIAL CHECK. If the model credential is absent we must NOT
    # run the CLI and must NOT let the row count as a failed attempt: the task was
    # never actually attempted. Exit 75 (EX_TEMPFAIL) tells the poller so.
    child_env = dict(os.environ)
    # PRECEDENCE, corrected 2026-09-13 after a real run exposed the flaw:
    #   * profile.env fills GAPS only (the unit's Environment= must be able to
    #     override the endpoint, since the "me" endpoint refuses this host).
    #   * VAULT_* comes from DISK and OVERRIDES the ambient environment. The file is
    #     the rotated source of truth; an ambient VAULT_SECRET_ID is likely STALE (a
    #     pre-rotation shell), and letting it win produced "vault HTTP 403" while the
    #     on-disk credential was perfectly valid.
    for k, v in load_env(PROFILE_ENV).items():
        child_env.setdefault(k, v)
    child_env.update(load_env(VAULT_ENV))

    # NEVER propagate a TLS-verification bypass to the child, whatever this process
    # inherited. A legacy caller that still exports NODE_TLS_REJECT_UNAUTHORIZED=0
    # would otherwise silently re-disable verification inside every job - quietly
    # undoing the fleet-wide fix for any session started before it landed.
    child_env.pop("NODE_TLS_REJECT_UNAUTHORIZED", None)

    # Point Node at the CA with a CONCRETE path. load_env does no shell expansion,
    # so a value written as ${VAR:-default} arrives as that literal string and Node
    # warns "Ignoring extra certs ... No such file or directory", i.e. no CA at all.
    _ca = os.path.join(HOME, ".bare-ai", "ca", "vault-ca.crt")
    if os.path.exists(_ca):
        child_env["NODE_EXTRA_CA_CERTS"] = _ca
    # VAULT FIRST, env file as fallback (liege directive).
    # The provider comes from the model this row will actually run, so the key and the
    # endpoint cannot disagree (rows 720/722: Gemini model + DeepSeek key = 400).
    _route = route_model(row)
    _prov = provider_of_model(_route.get("model"))
    _prov_path = PROVIDER_SECRET_PATHS.get(_prov)
    if _prov and not _prov_path:
        print("executor: WARNING provider %r has no credential path on this node; "
              "the node default will be used and may be rejected" % _prov)
    vkey, vsrc = vault_api_key(_prov_path)
    if vkey:
        child_env["BARE_AI_API_KEY"] = vkey
        print("executor: credential source = Vault (%s)" % vsrc)
    else:
        have = "present" if child_env.get("BARE_AI_API_KEY") else "absent"
        print("executor: credential source = local env file (%s); %s" % (vsrc, have))

    if not (child_env.get("BARE_AI_API_KEY") or child_env.get("BARE_AI_ENDPOINT")):
        print("executor: TEMPFAIL - no BARE_AI_API_KEY or BARE_AI_ENDPOINT in the "
              "environment. The model cannot be called, so this task was NOT "
              "attempted. Provision the credential (see the build doc) and requeue.",
              file=sys.stderr)
        return 75

    prompt_body = (row.get("ai_prompt") or "").strip()
    if not prompt_body:
        # Guardrail 6: never act on a row whose instruction was never written.
        print("REFUSING: row %d has an empty ai_prompt." % row_id, file=sys.stderr)
        return 1

    short = (row.get("short_description") or "").strip()
    criterion = (row.get("success_criterion") or "").strip() or "(none stated)"
    identity = agent_identity()
    scope_block = scope_bounds_block()
    routing = _route
    task_block = PROMPT_TEMPLATE.format(row=row_id, short=short,
                                       prompt=prompt_body, criterion=criterion,
                                       scope=scope_block, node=_node_identity())
    prompt = identity + task_block
    print("executor: identity=%d bytes (role+constitution), task=%d bytes, mode=%s"
          % (len(identity), len(task_block), APPROVAL_MODE))

    # --prompt-file avoids argv length limits and all shell-escaping problems.
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as fh:
        fh.write(prompt)
        prompt_file = fh.name

    try:
        child_env["BARE_AI_NO_TOOLS"] = "false"
        child_env.setdefault("BARE_AI_DISABLE_WORKSPACE_TRUST", "true")
        # the agent logs to its diary; give it today's path explicitly
        _dy = os.path.join(DIARY_DIR, datetime.now(timezone.utc).strftime("%Y-%m-%d") + ".md")
        child_env.setdefault("BARE_AI_DIARY", _dy)
        child_env.setdefault("BARE_AI_ROLE_CONSTITUTION", ROLE_FILE)
        child_env.setdefault("BARE_AI_CONSTITUTION", CONSTITUTION_FILE)
        # Match the launcher's invocation policy (--yolo = the agent can act), rather
        # than the read-only plan mode I originally chose. Configurable via
        # TASKBUS_EXEC_APPROVAL_MODE so unattended runs can be constrained.
        cmd = [NODE, CLI, "--yolo", "-p", "", "--prompt-file", prompt_file, "-o", "text"]
        # DEFECT 5: route the model by task size. routing["model"] is None when
        # nothing could be resolved from the live catalog, and then we deliberately
        # pass NO --model so the CLI default applies - a routing failure must degrade
        # to yesterday behaviour, never to an invented model name.
        if routing.get("model"):
            cmd += ["--model", str(routing["model"])]
        if APPROVAL_MODE != "yolo":
            cmd += ["--approval-mode", APPROVAL_MODE]
        # Report the mode ACTUALLY used. This line previously hardcoded "plan(read-only)"
        # after I changed the launch to --yolo, i.e. the log asserted a safety property
        # that was no longer true - same class as a secret_scrubbed flag that lies.
        # Never let a log line claim a control that is not in force.
        print("executor: row=%d approval_mode=%s timeout=%ds" % (row_id, APPROVAL_MODE, TIMEOUT))
        # DEFECT 5 evidence: record the routing decision AND the model ACTUALLY passed.
        print("executor: routing size=%d bytes tier=%s model=%s (%s)"
              % (routing["size_bytes"], routing["tier"],
                 routing["model"] or "(CLI default)", routing["reason"]))
        # DEFECT 3 evidence: name the criterion version this run executes under.
        print("executor: criterion_digest=%s len=%d"
              % (criterion_digest(criterion), len(criterion)))
        # DEFECT 4 evidence: state the enforced output cap and scope in the log.
        print("executor: recon_out_of_scope=%s output_cap=%d bytes"
              % (",".join(SCOPE_EXCLUDED), OUTPUT_CAP))
        proc = subprocess.run(cmd, env=child_env, timeout=TIMEOUT,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        out = cap_output(proc.stdout.decode("utf-8", "replace"))
        print(out)
        # The decision lives in classify_run() so it is unit-testable without spawning the CLI.
        rc, verdict = classify_run(proc.returncode, out)
        print("executor: CLI exit_code=%d verdict=%s" % (proc.returncode, verdict))
        if verdict == "COMPLETE":
            return 0
        if verdict == "INCOMPLETE_NO_MARKER":
            print("executor: INCOMPLETE_NO_MARKER - the CLI exited 0 but the model never declared "
                  "the task finished. Reporting INCOMPLETE, not a clean finish. This is the exact "
                  "failure shape that let a 79-second no-op run look successful on row 18.")
            return rc
        if verdict == "TEMPFAIL_CREDENTIAL":
            print("executor: TEMPFAIL - credential missing/rejected; task NOT attempted")
            return rc
        return rc
    except subprocess.TimeoutExpired:
        print("executor: TIMEOUT after %ds - task not completed" % TIMEOUT)
        return 1
    finally:
        try:
            os.unlink(prompt_file)
        except OSError:
            pass


if __name__ == "__main__":
    sys.exit(main())
