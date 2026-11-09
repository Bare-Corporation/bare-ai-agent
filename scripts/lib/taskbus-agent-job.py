#!/usr/bin/env python3
"""taskbus-agent-job.py - TARGET-SIDE runner for a task-bus job.

Invoked on the target node by the dispatcher's poller, via dispatch-job.sh:
    dispatch-job.sh <uuid> python3 /home/bare-ai/.bare-ai/lib/taskbus-agent-job.py <prompt_file>

It runs THIS NODE'S OWN AGENT IDENTITY (its role.md + technical constitution), not the dispatcher's -
each machine should act as itself. The task text arrives in the prompt file so the target
needs no Baserow credential of its own.
"""
import json, os, subprocess, sys, tempfile
import ssl
import urllib.request, urllib.error

HOME = os.path.expanduser("~")
VAULT_ENV = os.path.join(HOME, ".bare-ai", "config", "vault.env")
# LEGACY FALLBACK, kept only so a node that predates model-credential.path keeps working.
# The 1-to-1 Vault namespace makes a node credential path per-machine, so every node is
# expected to declare its own in ~/.bare-ai/config/model-credential.path; using this value
# now warns on stderr. Do not add new dependencies on it.
VAULT_SECRET_PATH = "secret/data/bare-ai-brain/config"
VAULT_KEY_FIELD = "api_key"
CA_FILE = os.path.join(HOME, ".bare-ai", "ca", "vault-ca.crt")

# ---- T4 (row 529): pinned execution-profile verification -------------------
# The gateway stamps an immutable execution_profile snapshot on every
# successful claim. This runner re-derives every hash before it executes
# anything, so a row can never run under role text the gateway did not verify.
# The module lives beside the repo copy; the DEPLOYED runner lives in
# ~/.bare-ai/lib, so both candidate directories are searched.
_PC_DIRS = [os.path.dirname(os.path.abspath(__file__)),
            os.path.join(HOME, "bare-ai-agent", "scripts", "lib")]
for _d in _PC_DIRS:
    if os.path.isdir(_d) and _d not in sys.path:
        sys.path.append(_d)
_pc = None
_PC_ERROR = ""
try:
    import profile_context as _pc
except Exception as _exc:  # noqa: BLE001
    _PC_ERROR = "%s: %s" % (type(_exc).__name__, _exc)



CENTRAL_MODELS_URL = "https://api.bare-ai.net/v1/models"
DEFAULT_SHORTCUT = os.environ.get("TASKBUS_MODEL_SHORTCUT", "303")
FALLBACK_ENDPOINT = "https://api.deepseek.com/v1/chat/completions"
FALLBACK_MODEL = "deepseek-v4-flash"

# Sent as the User-Agent on every criterion check (TICKET 307). A default
# python-urllib UA is rejected at the edge with 403, which is indistinguishable
# from a real API 403 and made every http_status check fail.
CHECK_UA = "bare-ai-taskbus-verifier/1.0"


# Shell constraints the harness enforces. Stated here because an executor that discovers the rule by
# hitting it burns passes: on 2026-11-05 a job spent all three passes repeating an inline loop that
# captured command output, and every attempt came back blocked.
SHELL_RULES = chr(10).join([
    "SHELL RULES (enforced by the harness - plan around them):",
    "- Command substitution is BLOCKED in every command argument: no dollar-paren, no backticks.",
    "- Need a value from one command in another? Do it inside ONE python3 program instead.",
    "- Multi-step shell work goes in a script file you then execute; never an inline loop that",
    "  captures command output into an argument list.",
    "- If a tool returns Blocked: command substitution detected, that is a PIVOT signal: change",
    "  the approach and retry once. Do not repeat the same shape of command.",
    "- If the task truly needs substitution, say so explicitly instead of exiting silently.",
]) + chr(10) + chr(10)


# ---------------------------------------------------- 3-TIER CONTEXT INJECTION
# The CLI has no internal system channel that reads these files (verified 2026-11-06: the bundle
# contains no reference to technical-constitution and reads no file under ~/.bare-ai), so the RUNNER
# must deliver them. Without this an executor agent is ungoverned: on 2026-11-06 a sub-agent on .16
# burned every pass on blocked command substitution because nothing had ever told it the rule.
#
# Tier 1 technical-constitution.md is MANDATORY and has a repo fallback; a missing Tier 1 is loud.
# Tier 2 bare-environment.md (public role) and Tier 3 bare-archetype.md (private role) are optional
# by design: a node migrated off role.md carries the environment file instead, and not every node
# has an archetype.
CONST_PATH = os.path.join(HOME, ".bare-ai", "technical-constitution.md")
CONST_FALLBACK = os.path.join(HOME, "bare-ai-agent", "scripts", "templates",
                             "technical-constitution.md")
ENV_PATH = os.path.join(HOME, ".bare-ai", "bare-environment.md")
ARCH_PATH = os.path.join(HOME, ".bare-ai", "bare-archetype.md")


def _read_context(path):
    """Return the file body, or an empty string when absent. Never raises."""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read().strip()
    except OSError:
        return ""


def build_context_preamble(pinned_role=None, pinned_label=""):
    """Assemble the 3-tier context preamble. Returns (text, sizes) so the caller can log it.

    Deterministic: same files in, same preamble out, no clock, no network, no ordering surprises.
    """
    const = _read_context(CONST_PATH)
    const_src = "node"
    if not const:
        const = _read_context(CONST_FALLBACK)
        const_src = "repo-template" if const else "MISSING"
    if not const:
        sys.stderr.write(
            "target-job: WARNING Tier 1 technical-constitution.md is MISSING at %s and the repo "
            "template at %s was not readable - this job runs WITHOUT the technical constitution. "
            "That is a node defect, not a ticket defect.\n" % (CONST_PATH, CONST_FALLBACK))
    env_body = _read_context(ENV_PATH)
    arch = _read_context(ARCH_PATH)
    parts = []
    if const:
        parts.append("# SYSTEM CONTEXT & OPERATIONAL CONSTITUTION\n" + const)
    if pinned_role:
        # T4: a gateway-pinned role REPLACES the local role tiers. Executing the
        # snapshot is the whole point - a local file that disagrees must never win.
        parts.append("# PINNED EXECUTION-PROFILE ROLE (" + (pinned_label or "snapshot") + ")" + "\n" + pinned_role)
    else:
        if env_body:
            parts.append("# HOST ENVIRONMENT & PUBLIC ROLE (bare-environment)\n" + env_body)
        if arch:
            parts.append("# AGENT ARCHETYPE & PRIVATE ROLE (bare-archetype)\n" + arch)
    text = "\n\n".join(parts)
    if text:
        text += "\n\n"
    return text, {"constitution": len(const), "constitution_src": const_src,
                  "environment": len(env_body), "archetype": len(arch)}


def central_route(shortcut):
    """Resolve (endpoint, model, note) from the CENTRAL model list.

    The list is the single source of truth for per-model routes and shortcut numbers
    (e.g. 303 -> deepseek-v4-flash), which is how the interactive launcher works.

    TWO CONVERSIONS ARE REQUIRED, both established by testing against the real API:
      1. model_id arrives as 'deepseek-v4-flash|deepseek-cn'. The CLI REJECTS the pipe
         form (rc=1), so take the part before the pipe.
      2. base_url arrives WITHOUT the completions path ('https://api.deepseek.com').
         The CLI needs the full endpoint, so append it:
            .../v1        -> + /chat/completions
            otherwise     -> + /v1/chat/completions
         (the Alibaba entries already end in /compatible-mode/v1, which resolves correctly)
      3. A User-Agent is MANDATORY. Cloudflare returns 403 for the default python-urllib
         UA while curl/browser/node UAs pass - verified. Without this the runner would
         conclude the central API was down and silently fall back to hardcoded values.
    """
    try:
        rq = urllib.request.Request(CENTRAL_MODELS_URL)
        rq.add_header("Accept", "application/json")
        rq.add_header("User-Agent", "bare-ai-taskbus/1.0 curl/8.5.0")
        with urllib.request.urlopen(rq, timeout=25) as r:
            data = json.loads(r.read())
    except Exception as e:
        return None, None, "%s: %s" % (type(e).__name__, str(e)[:70])
    for m in (data.get("models") or []):
        if str(m.get("shortcut")) == str(shortcut):
            mid = (m.get("model_id") or "").split("|")[0].strip()
            bu = (m.get("base_url") or "").strip().rstrip("/")
            if not mid or not bu:
                return None, None, "shortcut %s incomplete in list" % shortcut
            if "://" not in bu:
                bu = "http://" + bu
            ep = bu + "/chat/completions" if bu.endswith("/v1") else bu + "/v1/chat/completions"
            return ep, mid, "central list shortcut %s" % shortcut
    return None, None, "shortcut %s not present in list" % shortcut


def shortcut_from_prompt(text):
    """Optional 'MODEL_SHORTCUT: <n>' line lets a task choose its own model."""
    import re as _re
    m = _re.search(r"^\s*MODEL_SHORTCUT\s*:\s*([0-9]+)\s*$", text or "", _re.M)
    return m.group(1) if m else None


def taskbus_ticket_meta(text):
    """Return (row_id, title) from the task-bus prompt envelope, or (None, None).

    WHY: runner logs and job JSONs carried NO row id, so after a run nobody could say which Baserow
    row a job belonged to (measured on a worker node, 2026-11-04: neither td-taskbus-*.log nor *.json named a
    row). The envelope the poller already writes contains it, so re-read it here rather than inventing
    another channel:
        TASK-BUS JOB - row 276 (dispatched by the task-bus poller)
        SHORT DESCRIPTION: <title, possibly WRAPPED over several lines>
    Continuation lines up to the first blank line are joined with spaces, because long titles wrap
    (row 276 wraps mid-sentence) and first-line-only would silently truncate.
    Never raises: a malformed envelope must not kill a job.
    """
    import re as _re
    row_id, title = None, None
    try:
        m = _re.search(r"^TASK-BUS JOB\s*-\s*row\s*([0-9]+)", text or "", _re.M)
        if m:
            row_id = m.group(1)
        m2 = _re.search(r"^SHORT DESCRIPTION:\s*(.*)$", text or "", _re.M)
        if m2:
            parts = [m2.group(1).strip()]
            for line in (text or "")[m2.end():].split(chr(10))[1:]:
                s = line.strip()
                if not s:
                    break
                parts.append(s)
            title = " ".join(p for p in parts if p).strip()
        if title:
            title = title[:200]
    except Exception:
        return row_id, title
    return row_id, title


def load_env(path):
    env = {}
    try:
        for line in open(path, errors="replace"):
            s = line.strip()
            s = s[len("export "):] if s.startswith("export ") else s
            if "=" in s and not s.startswith("#"):
                k, v = s.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    return env


def vault_api_key():
    """Vault-first credential fetch. Each node has its own vault.env with the rotated
    AppRole secret_id, so a dispatched job can authenticate without the dispatcher sending secrets
    over the wire. Without this the target had no model credential at all and every
    cross-machine job exited 75 (TEMPFAIL) - found on the first real dispatch."""
    cfg = load_env(VAULT_ENV)
    addr = (cfg.get("VAULT_ADDR") or "").rstrip("/")
    rid, sid = cfg.get("VAULT_ROLE_ID"), cfg.get("VAULT_SECRET_ID")
    # A CIDR-bound AppRole carries no secret_id (Ticket 305). Requiring one made this node refuse
    # to attempt a login at all and report "vault config incomplete" forever.
    if not (addr and rid):
        return None, "vault config incomplete (need VAULT_ADDR + VAULT_ROLE_ID)"
    ctx = ssl.create_default_context(cafile=CA_FILE) if os.path.exists(CA_FILE) else None
    # BOUNDED RETRY on the AppRole login. Observed intermittently returning
    # 403 {"errors":["permission denied"]} for a role_id/secret_id pair that then
    # succeeds immediately on retry - including 12/12 successes in a rapid burst, so
    # it is NOT rate limiting and NOT an invalid credential. Retrying a few times
    # converts a transient Vault-side hiccup into a successful job instead of a
    # TEMPFAIL. Bounded, so a genuinely bad credential still fails promptly.
    import time as _time
    tok, last = None, ""
    for attempt in range(3):
        try:
            _ident = {"role_id": rid}
            if sid:
                _ident["secret_id"] = sid
            body = json.dumps(_ident).encode()
            rq = urllib.request.Request(addr + "/v1/auth/approle/login", data=body, method="POST")
            rq.add_header("Content-Type", "application/json")
            with urllib.request.urlopen(rq, timeout=20, context=ctx) as r:
                tok = (json.loads(r.read()).get("auth") or {}).get("client_token")
            if tok:
                if attempt:
                    print("target-job: vault login succeeded on attempt %d" % (attempt + 1))
                break
            last = "approle login returned no token"
        except urllib.error.HTTPError as e:
            last = "approle-login HTTP %s" % e.code
            if e.code not in (403, 429, 500, 502, 503):
                break
        except Exception as e:
            last = "%s: %s" % (type(e).__name__, str(e)[:60])
        if attempt < 2:
            print("target-job: vault login attempt %d failed (%s) - retrying" % (attempt + 1, last))
            _time.sleep(2 + 3 * attempt)
    if not tok:
        return None, last or "approle login failed"
    try:
        # Per-node credential path from an auditable file (path only, no secret). The constant is
        # the fallback so the default dispatcher, which the constant belongs to, behaves exactly as before.
        _p = ""
        try:
            with open(os.path.join(HOME, ".bare-ai", "config", "model-credential.path")) as _fh:
                for _ln in _fh:
                    _ln = _ln.strip()
                    if _ln and not _ln.startswith("#"):
                        _p = _ln
                        break
        except OSError:
            pass
        if not _p:
            sys.stderr.write(
                "target-job: WARNING no model-credential.path on this node; falling back to "
                "the legacy shared Vault path. Set ~/.bare-ai/config/model-credential.path "
                "to this machine own secret path." + chr(10))
        rq2 = urllib.request.Request("%s/v1/%s" % (addr, _p or VAULT_SECRET_PATH))
        rq2.add_header("X-Vault-Token", tok)
        with urllib.request.urlopen(rq2, timeout=20, context=ctx) as r:
            data = json.loads(r.read())
        key = ((data.get("data") or {}).get("data") or {}).get(VAULT_KEY_FIELD)
        return (key, "vault") if key else (None, "no %s field" % VAULT_KEY_FIELD)
    except urllib.error.HTTPError as e:
        # Report WHICH request failed. A bare "vault HTTP 403" cannot distinguish
        # "the AppRole login was refused" from "the login worked but the token cannot
        # read the secret path" - two completely different faults that were conflated
        # in earlier debugging.
        stage = "secret-read" if "auth/approle/login" not in (e.url or "") else "approle-login"
        body = ""
        try:
            body = e.read().decode("utf-8", "replace")[:120]
        except Exception:
            pass
        return None, "vault %s HTTP %s %s" % (stage, e.code, body)
    except Exception as e:
        return None, "%s: %s" % (type(e).__name__, str(e)[:80])
CLI = os.path.join(HOME, "bare-ai-cli", "bundle", "bare-ai.js")
# Node path FIXED 2026-09-14: was hardcoded to /usr/local/bin/node, which does not exist
# on nodes that ship node at /usr/bin/node), so jobs dispatched to those nodes died
# with FileNotFoundError and looked like agent failures. Resolve from PATH first.
def _resolve_node():
    import shutil as _sh
    for _cand in (_sh.which("node"), "/usr/local/bin/node", "/usr/bin/node"):
        if _cand and os.path.exists(_cand) and os.access(_cand, os.X_OK):
            return _cand
    return "node"

NODE = _resolve_node()
ROLE = os.path.join(HOME, ".bare-ai", "role.md")
CONST = os.path.join(HOME, ".bare-ai", "technical-constitution.md")
TIMEOUT = int(os.environ.get("TASKBUS_EXEC_TIMEOUT", "1500"))
APPROVAL = os.environ.get("TASKBUS_EXEC_APPROVAL_MODE", "yolo")

MAX_PASSES = int(os.environ.get("TASKBUS_MAX_PASSES", "3"))

# ---- TICKET 306: secret redaction before anything reaches a log --------------------
# WHY: machines_response in Baserow is readable by the stakeholder account, so any secret that
# reaches stdout/stderr is durably exposed. This scrubber runs on ALL captured output.
# Deliberately regex-free: plain scanning is auditable and cannot be broken by escape mangling.
_SECRET_NAMES = ("SECRET", "TOKEN", "PASSWORD", "PASSWD", "API_KEY", "APIKEY",
                 "PRIVATE_KEY", "ROLE_ID", "SECRET_ID", "CREDENTIAL", "WEBHOOK")


def _marker(name):
    return "<REDACTED:" + name + ">"


def _looks_secret_value(v):
    """Value-shape detection, for secrets that carry no helpful label."""
    s = v.strip().strip('"').strip("'")
    if len(s) < 16:
        return None
    low = s.lower()
    if low.startswith("sk-"):
        return "sk-like-token"
    if low.startswith("hvs.") or low.startswith("hv."):
        return "vault-token"
    if low.startswith("ghp_") or low.startswith("gho_") or low.startswith("ghs_"):
        return "github-token"
    if low.startswith("xox"):
        return "slack-token"
    if s[:4] == "AKIA" and len(s) >= 20:
        return "aws-key-id"
    if s.count(".") == 2 and len(s) >= 40:
        seg = s.split(".")
        if all(len(x) >= 8 for x in seg):
            return "jwt-like"
    return None


def _redact_line(line):
    out = line
    for sep in ("=", ":"):
        if sep not in out:
            continue
        idx = out.find(sep)
        while idx != -1:
            left = out[:idx].strip().split()[-1].strip('"').strip("'").upper() if out[:idx].strip() else ""
            rest = out[idx + 1:]
            if any(tok in left for tok in _SECRET_NAMES) and rest.strip():
                stop = len(rest)
                for ch in (",", ";"):
                    j = rest.find(ch)
                    if j != -1 and j < stop:
                        stop = j
                out = out[:idx + 1] + " " + _marker(left[-24:] or "SECRET") + rest[stop:]
                break
            idx = out.find(sep, idx + 1)
    parts = out.split(" ")
    for i, tok in enumerate(parts):
        kind = _looks_secret_value(tok)
        if kind:
            parts[i] = _marker(kind)
    return " ".join(parts)


def _redact_secrets(text):
    """Fail CLOSED: if redaction raises, withhold the output rather than emit it raw."""
    if not text:
        return text
    try:
        lines = text.split(chr(10))
        out = []
        i = 0
        while i < len(lines):
            ln = lines[i]
            if "-----BEGIN" in ln and "PRIVATE KEY-----" in ln:
                out.append(_marker("pem-private-key"))
                j = i + 1
                while j < len(lines) and "-----END" not in lines[j]:
                    j += 1
                i = j + 1
                continue
            out.append(_redact_line(ln))
            i += 1
        return chr(10).join(out)
    except Exception as exc:
        return "<output withheld: redaction error " + type(exc).__name__ + ">"


# ---- criterion execution -------------------------------------------------
# The POLLER parses success_criterion (single source of truth for the grammar) and ships the
# parsed checks here as JSON. This runner only EXECUTES them, so the grammar can never diverge.
# Kinds: http_status[url,code] | file_exists[path] | file_md5[path,hex] |
#        file_sha256[path,hex] | file_excludes[path,literal-text]
#        git_ls_remote[...] -> not executable here; reported as deferred (the poller re-checks).
def _run_check(kind, args):
    import hashlib, urllib.request, urllib.error
    try:
        if kind == "http_status":
            url, want = args[0], int(args[1])
            req = urllib.request.Request(url, method="GET")
            # TICKET 307: a User-Agent is mandatory. Cloudflare answers the default
            # python-urllib UA with 403 "error code: 1010", indistinguishable from a real API
            # 403, which made every http_status check fail regardless of the endpoint.
            req.add_header("User-Agent", CHECK_UA)
            req.add_header("Accept", "application/json, text/plain;q=0.9, */*;q=0.8")
            body, hdrs = b"", {}
            try:
                with urllib.request.urlopen(req, timeout=20) as r:
                    got = r.status
                    hdrs = dict(getattr(r, "headers", {}) or {})
                    body = r.read(512)
            except urllib.error.HTTPError as e:
                got = e.code
                hdrs = dict(getattr(e, "headers", {}) or {})
                try:
                    body = e.read(512)
                except Exception:
                    body = b""
            low = body.decode("utf-8", "replace").lower()
            edge_block = got == 403 and ("error code: 1010" in low
                                         or str(hdrs.get("cf-mitigated", "")).lower() == "challenge")
            if edge_block:
                return None, ("%s -> 403 Cloudflare 1010 (edge bot block, NOT the API); "
                              "criterion UNVERIFIABLE - fix the checker UA" % url)
            return (got == want), "%s -> %s (want %s)" % (url, got, want)
        if kind == "file_exists":
            p = args[0]
            return os.path.exists(p), "%s exists=%s" % (p, os.path.exists(p))
        if kind in ("file_md5", "file_sha256"):
            p, want = args[0], str(args[1]).lower()
            if not os.path.exists(p):
                return False, "%s MISSING" % p
            h = hashlib.new("md5" if kind == "file_md5" else "sha256")
            with open(p, "rb") as fh:
                for chunk in iter(lambda: fh.read(65536), b""):
                    h.update(chunk)
            got = h.hexdigest()
            return (got == want), "%s %s=%s (want %s)" % (p, kind, got[:12], want[:12])
        if kind == "file_excludes":
            # NEGATIVE assertion: PASS only if the file exists and the literal text is ABSENT.
            # Missing file is a FAIL - absence cannot be verified in a file that is not there.
            p, lit = args[0], str(args[1])
            if not os.path.exists(p):
                return False, "%s MISSING - cannot verify absence in a file that is not there" % p
            with open(p, "r", encoding="utf-8", errors="replace") as fh:
                body = fh.read()
            found = lit in body
            return (not found), "%s %s %r" % (p, "CONTAINS" if found else "excludes", lit)
        if kind == "git_ls_remote":
            return None, "git_ls_remote deferred to the poller"
        return None, "unknown check kind %r deferred" % kind
    except Exception as e:
        return False, "%s raised %s: %s" % (kind, type(e).__name__, str(e)[:120])


def _execute_criterion(spec):
    """spec: list of {"kind":..,"args":[..]}. Returns (all_ok, report_lines)."""
    ok_all, lines, executed = True, [], 0
    for c in spec:
        res = _run_check(c.get("kind"), c.get("args") or [])
        ok, msg = res[0], res[1]
        if ok is None:
            lines.append("  deferred: " + msg)
            continue
        executed += 1
        lines.append(("  PASS: " if ok else "  FAIL: ") + msg)
        if not ok:
            ok_all = False
    if executed == 0:
        return None, lines          # nothing executable here -> let the poller decide
    return ok_all, lines


def load_claim_snapshot():
    """Return (ctx, reason). ctx is None when no snapshot was supplied.

    reason is None on success (or when nothing was supplied), and a structured
    failure code when the snapshot exists but cannot be trusted. The caller MUST
    refuse to execute whenever reason is not None.
    """
    path = (os.environ.get("TASKBUS_CLAIM_FILE") or "").strip()
    raw = (os.environ.get("TASKBUS_CLAIM_JSON") or "").strip()
    payload = None
    if path:
        try:
            with open(path, encoding="utf-8") as fh:
                payload = json.load(fh)
        except (OSError, ValueError) as exc:
            return None, "claim_snapshot_unreadable:%s" % exc
    elif raw:
        try:
            payload = json.loads(raw)
        except ValueError as exc:
            return None, "claim_snapshot_unreadable:%s" % exc
    else:
        return None, None

    if _pc is None:
        return None, "profile_context_unavailable:%s" % _PC_ERROR
    if not isinstance(payload, dict) or "execution_profile" not in payload:
        # Fail closed: a claim with no profile block must never silently downgrade
        # to the local role, which is the exact defect this ticket exists to stop.
        return None, "profile_snapshot_missing"
    try:
        ctx = _pc.verify(payload)
    except _pc.ProfileHashMismatch as exc:
        return None, "profile_hash_mismatch:%s" % exc.which
    except _pc.ProfileContextError as exc:
        return None, "profile_snapshot_unusable:%s" % exc
    except Exception as exc:  # noqa: BLE001
        return None, "profile_snapshot_unusable:%s: %s" % (type(exc).__name__, exc)
    return ctx, None


def apply_snapshot_router(ctx, env):
    """Export the snapshot routing config and enforce what is enforceable here."""
    cfg = (ctx or {}).get("router") or {}
    if not cfg:
        return []
    notes = []
    strategy = cfg.get("routing_strategy")
    ceiling = cfg.get("routing_ceiling_eur")
    if strategy:
        env["BARE_TASKBUS_ROUTING_STRATEGY"] = str(strategy)
    if ceiling is not None:
        env["BARE_TASKBUS_ROUTING_CEILING_EUR"] = str(ceiling)
    excluded = cfg.get("exclude_providers") or []
    if excluded:
        env["BARE_TASKBUS_EXCLUDE_PROVIDERS"] = ",".join(str(p) for p in excluded)
        chosen = "%s %s" % (env.get("BARE_AI_ENDPOINT") or "", env.get("BARE_AI_MODEL") or "")
        for provider in excluded:
            if str(provider).lower() and str(provider).lower() in chosen.lower():
                notes.append("route %r is an EXCLUDED provider" % str(provider))
    return notes



# --------------------------------------------------------------- T4 profile gate
# A claim that carries an execution_profile snapshot must be VERIFIED before the
# job runs. profile_context re-derives all three hashes under the gateway's
# canonicalisation; any mismatch stops the job.
PROFILE_CTX_MAX_BYTES = 2 * 1024 * 1024
_PROFILE_MOD = {"tried": False, "mod": None}


class ProfileGateFailure(Exception):
    """The snapshot cannot be trusted. The job must NOT execute."""
    reason = "profile_hash_mismatch"

    def __init__(self, which="", detail=""):
        self.which = which
        self.detail = detail
        super().__init__("%s:%s %s" % (self.reason, which, detail))


class ProfileGateUnusable(ProfileGateFailure):
    """No usable snapshot where one was required."""
    reason = "profile_snapshot_unusable"


def _load_profile_context():
    import importlib.util
    here = os.path.dirname(os.path.abspath(__file__))
    extra = (os.environ.get("BARE_PROFILE_CONTEXT_DIR") or "").strip()
    for d in ([here] + ([extra] if extra else [])):
        p = os.path.join(d, "profile_context.py")
        if os.path.exists(p):
            spec = importlib.util.spec_from_file_location("profile_context", p)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod
    return None


def profile_context_module():
    if not _PROFILE_MOD["tried"]:
        _PROFILE_MOD["tried"] = True
        _PROFILE_MOD["mod"] = _load_profile_context()
    return _PROFILE_MOD["mod"]


def load_claim_payload():
    """Return (payload, source). Inline JSON wins over a file path."""
    raw = (os.environ.get("TASKBUS_CLAIM_JSON") or "").strip()
    if raw:
        try:
            return json.loads(raw), "inline"
        except ValueError as exc:
            raise ProfileGateUnusable("claim_json_unparseable", str(exc))
    path = (os.environ.get("TASKBUS_CLAIM_PAYLOAD") or "").strip()
    if not path:
        return None, "absent"
    try:
        if os.path.getsize(path) > PROFILE_CTX_MAX_BYTES:
            raise ProfileGateUnusable("claim_payload_too_large", path)
        with open(path, encoding="utf-8") as fh:
            return json.load(fh), path
    except (OSError, ValueError) as exc:
        raise ProfileGateUnusable("claim_payload_unreadable", str(exc))


def profile_gate(required=False):
    """Verify the claim snapshot. Returns ctx, or None when no payload shipped.

    A row bound to an execution profile must never silently fall back to the
    node's local role - that is the downgrade this gate exists to prevent, so
    required=True turns an absent payload into a refusal.
    """
    pc = profile_context_module()
    if pc is None:
        raise ProfileGateUnusable("verifier_missing", "profile_context.py not found")
    payload, _source = load_claim_payload()
    if payload is None:
        if required:
            raise ProfileGateUnusable(
                "snapshot_absent", "profile-bound row shipped no claim payload")
        return None
    try:
        return pc.verify(payload)
    except pc.ProfileHashMismatch as exc:
        raise ProfileGateFailure(getattr(exc, "which", "unknown"), exc.detail) from exc
    except pc.ProfileContextError as exc:
        raise ProfileGateUnusable(getattr(exc, "code", "unusable"), str(exc)) from exc


def write_snapshot_role(ctx, cwd):
    """Materialise the verified role markdown so it, not role.md, is injected."""
    path = os.path.join(cwd, ".taskbus-role.md")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(ctx["system_prompt"])
    return path


def profile_gate_stamp(ctx):
    return profile_context_module().audit_stamp(ctx)


def profile_gate_json(ctx=None, failure=None):
    if failure is not None:
        body = {"ok": False, "reason": failure.reason, "which": failure.which}
    elif ctx is None:
        body = {"ok": True, "applied": False}
    else:
        body = {"ok": True, "applied": True, "slug": ctx["slug"],
                "profile_hash": ctx["profile_hash"],
                "config_hash": ctx["config_hash"],
                "role_content_sha256": ctx["role_content_sha256"],
                "routing_strategy": ctx["router"]["routing_strategy"],
                "routing_ceiling_eur": ctx["router"]["routing_ceiling_eur"],
                "markdown_bytes": ctx["markdown_bytes"]}
    return "PROFILE_GATE_JSON: " + json.dumps(body, sort_keys=True)


def verify_only_cli(argv):
    """Exercise the gate on the DEPLOYED artefact without calling a model."""
    required = "--required" in argv
    try:
        ctx = profile_gate(required=required)
    except ProfileGateFailure as exc:
        print("PROFILE_GATE: FAILED reason=%s which=%s detail=%s"
              % (exc.reason, exc.which, exc.detail[:200]))
        print(profile_gate_json(failure=exc))
        return 78
    if ctx is None:
        print("PROFILE_GATE: NO_PAYLOAD (gate inert; row not profile-bound)")
        print(profile_gate_json(ctx=None))
        return 0
    print("PROFILE_GATE: VERIFIED slug=%s v%s" % (ctx["slug"], ctx["version"]))
    for c in ctx["checks"]:
        print("  %-42s %s  %s"
              % (c["name"], "OK" if c["ok"] else "MISMATCH", c["expected"][:16]))
    print("PROFILE_GATE: AUDIT " + profile_gate_stamp(ctx))
    print(profile_gate_json(ctx=ctx))
    return 0

def main():
    if "--verify-only" in sys.argv:
        return verify_only_cli(sys.argv)
    if len(sys.argv) < 2:
        print("usage: taskbus-agent-job.py <prompt_file> [workspace_dir]", file=sys.stderr)
        return 2
    pf = sys.argv[1]
    try:
        task = open(pf, encoding="utf-8").read()
    except OSError as e:
        print("cannot read prompt file %s: %s" % (pf, e), file=sys.stderr)
        return 2

    # ---- T4 GATE: verify the pinned execution-profile snapshot BEFORE any work.
    # Fail closed: a mismatch means the row does not execute at all, and the
    # poller blocks it on this exit code plus the structured reason line.
    _snap_ctx, _snap_reason = load_claim_snapshot()
    if _snap_reason is not None:
        print("target-job: PROFILE_VERIFICATION_FAILED reason=%s" % _snap_reason)
        print("target-job: REFUSING TO EXECUTE on an unverified snapshot",
              file=sys.stderr)
        return 3
    if _snap_ctx is not None:
        global MAX_PASSES
        _cfg = _snap_ctx.get("router") or {}
        print("target-job: profile verified %s" % _pc.audit_stamp(_snap_ctx))
        try:
            _cap = int(_cfg.get("max_attempts") or 0)
        except (TypeError, ValueError):
            _cap = 0
        if _cap > 0 and _cap < MAX_PASSES:
            print("target-job: pass cap from snapshot max_attempts: %d -> %d"
                  % (MAX_PASSES, _cap))
            MAX_PASSES = _cap
        _stamp = _pc.audit_stamp(_snap_ctx)
        _stamp_file = (os.environ.get("TASKBUS_AUDIT_FILE") or "").strip()
        if _stamp_file:
            try:
                with open(_stamp_file, "w", encoding="utf-8") as _sfh:
                    _sfh.write(_stamp + chr(10))
            except OSError as _se:
                print("target-job: audit stamp not written: %s" % _se)
    else:
        print("target-job: no claim snapshot supplied - legacy path, no profile gate")

    # ---- PINNED WORKING DIRECTORY ----------------------------------------
    # Sessions are CWD-SCOPED. A fixed per-job directory is what makes --resume latest safe;
    # without it a resume can attach to an unrelated session in $HOME.
    cwd = (sys.argv[2] if len(sys.argv) > 2 else os.environ.get("TASKBUS_JOB_CWD") or "").strip()
    if not cwd:
        cwd = os.path.join(HOME, ".bare-ai", "work")
    try:
        os.makedirs(cwd, exist_ok=True)
    except OSError as e:
        print("cannot create workspace %s: %s" % (cwd, e), file=sys.stderr)
        return 2

    # The task envelope only. The CLI loads the constitution through its own system channel;
    # prepending role.md + constitution here was pure dilution (~235 KB per job).
    # ---- T4: verify the execution-profile snapshot BEFORE anything executes ----
    _req = (os.environ.get("TASKBUS_PROFILE_REQUIRED") or "").strip().lower()
    profile_ctx = None
    try:
        profile_ctx = profile_gate(required=_req in ("1", "true", "yes"))
    except ProfileGateFailure as _exc:
        sys.stderr.write(("target-job: PROFILE_GATE FAILED reason=%s which=%s - "
                          "job NOT attempted" % (_exc.reason, _exc.which)) + chr(10))
        print(profile_gate_json(failure=_exc))
        return 78
    if profile_ctx is not None:
        print("target-job: execution_profile VERIFIED %s"
              % profile_gate_stamp(profile_ctx))

    taskbus_row_id, taskbus_title = taskbus_ticket_meta(task)
    print("target-job: baserow_row=%s title=%s" % (taskbus_row_id, taskbus_title))

    print("target-job: task=%d bytes, cwd=%s, mode=%s, max_passes=%d (no preamble injected)"
          % (len(task), cwd, APPROVAL, MAX_PASSES))

    spec = []
    raw = os.environ.get("TASKBUS_CRITERION_JSON") or ""
    if raw:
        try:
            spec = json.loads(raw)
            print("target-job: criterion checks supplied = %d" % len(spec))
        except ValueError as e:
            print("target-job: criterion JSON unparseable (%s) - deferring to poller" % e)

    env = dict(os.environ)

    # 1. NODE PROFILE FIRST (Liege 2026-11-03). A dispatched pass must be the same environment as an
    #    interactive session on this node: profile.env decides the route, model-credentials.env holds the
    #    key that matches it. Overriding either with the catalogue route or a Vault key produced a
    #    provider URL signed with a platform key, which is a 401 on every node, permanently.
    profile = load_env(os.path.join(HOME, ".bare-ai", "profile.env"))
    creds = load_env(os.path.join(HOME, ".bare-ai", "model-credentials.env"))
    for _src in (profile, creds):
        for _k, _v in _src.items():
            if _v:
                env[_k] = _v
    if profile.get("BARE_AI_ENDPOINT"):
        print("target-job: route source = node profile (%s) model=%s"
              % (profile.get("BARE_AI_PROFILE") or "?", env.get("BARE_AI_MODEL")))
    if env.get("BARE_AI_API_KEY"):
        print("target-job: credential source = node profile/model-credentials.env")

    # 2. Vault key is a FILL-IN only; it never overrides a profile credential.
    if not env.get("BARE_AI_API_KEY"):
        vkey, vsrc = vault_api_key()
        if vkey:
            env["BARE_AI_API_KEY"] = vkey
            print("target-job: credential source = Vault (%s)" % vsrc)
        else:
            print("target-job: no profile credential; vault says (%s)" % vsrc)

    # 3. fail closed, unchanged in spirit: no credential from either source means no attempt.
    if not env.get("BARE_AI_API_KEY"):
        print("target-job: TEMPFAIL - no model credential available on this node "
              "(profile and Vault both empty); task NOT attempted, no attempt consumed", file=sys.stderr)
        return 75

    # 4. catalogue route ONLY when the profile did not define one.
    if not env.get("BARE_AI_ENDPOINT"):
        sc = shortcut_from_prompt(task) or DEFAULT_SHORTCUT
        cep, cmodel, cnote = central_route(sc)
        if cep and cmodel:
            env["BARE_AI_ENDPOINT"], env["BARE_AI_MODEL"] = cep, cmodel
            print("target-job: route source = central catalogue %s -> model=%s endpoint=%s"
                  % (cnote, cmodel, cep))
        else:
            env.setdefault("BARE_AI_ENDPOINT", FALLBACK_ENDPOINT)
            env.setdefault("BARE_AI_MODEL", FALLBACK_MODEL)
            print("target-job: central list UNAVAILABLE (%s) - HARDCODED fallback %s"
                  % (cnote, FALLBACK_MODEL))
    else:
        env.setdefault("BARE_AI_MODEL", FALLBACK_MODEL)
    env.pop("NODE_TLS_REJECT_UNAUTHORIZED", None)
    ca = os.path.join(HOME, ".bare-ai", "ca", "vault-ca.crt")
    if os.path.exists(ca):
        env["NODE_EXTRA_CA_CERTS"] = ca
    env["BARE_AI_NO_TOOLS"] = "false"          # explicit; --tools is passed too, belt and braces
    env.setdefault("BARE_AI_DISABLE_WORKSPACE_TRUST", "true")
    if profile_ctx is not None:
        _role_path = write_snapshot_role(profile_ctx, cwd)
        env["BARE_AI_SYSTEM_MD"] = _role_path
        print("target-job: system prompt source = VERIFIED SNAPSHOT "
              "(%d bytes, profile=%s) - local role file NOT used"
              % (profile_ctx["markdown_bytes"], profile_ctx["slug"]))
    if _snap_ctx is not None:
        _route_notes = apply_snapshot_router(_snap_ctx, env)
        for _n in _route_notes:
            print("target-job: ROUTER WARNING %s" % _n)

    task_path = os.path.join(cwd, ".taskbus-task.prompt")
    with open(task_path, "w", encoding="utf-8") as fh:
        if _snap_ctx is not None:
            _label = "profile=%s v%s" % (_snap_ctx.get("slug"), _snap_ctx.get("version"))
            _ctx, _ctx_sizes = build_context_preamble(
                pinned_role=_snap_ctx.get("system_prompt"), pinned_label=_label)
        else:
            _ctx, _ctx_sizes = build_context_preamble()
        sys.stderr.write(
            "target-job: context tiers constitution=%dB(%s) environment=%dB archetype=%dB\n"
            % (_ctx_sizes["constitution"], _ctx_sizes["constitution_src"],
               _ctx_sizes["environment"], _ctx_sizes["archetype"]))
        if profile_ctx is not None:
            fh.write(_ctx + SHELL_RULES
                     + "EXECUTION PROFILE ROLE (verified snapshot "
                     + profile_ctx["slug"] + ", role hash "
                     + profile_ctx["role_content_sha256"][:16] + ")"
                     + chr(10) + chr(10)
                     + profile_ctx["system_prompt"] + chr(10) + chr(10) + task)
        else:
            fh.write(_ctx + SHELL_RULES + task)

    feedback = ""
    last_report = []
    for attempt in range(1, MAX_PASSES + 1):
        pf_use = task_path
        if feedback:
            pf_use = os.path.join(cwd, ".taskbus-pass%d.prompt" % attempt)
            with open(pf_use, "w", encoding="utf-8") as fh:
                fh.write(feedback)
        cmd = [NODE, CLI, "--yolo", "--tools"]
        if attempt > 1:
            cmd += ["--resume", "latest"]
        # 2026-11-03: a job could only read $HOME/.bare-ai/work, so tickets about a repo tree could
        # never be worked. Per-node list, one absolute path per line; absent file = unchanged behaviour.
        _inc_f = os.path.join(HOME, ".bare-ai", "config", "job-include-dirs")
        _inc = []
        try:
            with open(_inc_f) as _fh:
                for _ln in _fh:
                    _ln = _ln.strip()
                    if _ln and not _ln.startswith("#"):
                        _inc.append(_ln)
        except OSError:
            pass
        if _inc:
            cmd += ["--include-directories"] + _inc
            print("target-job: include directories = %s" % ", ".join(_inc))
        cmd += ["-p", "", "--prompt-file", pf_use, "-o", "text"]
        print("target-job: ===== pass %d/%d ===== %s" % (attempt, MAX_PASSES,
              "resume latest" if attempt > 1 else "fresh session"))
        try:
            p = subprocess.run(cmd, env=env, cwd=cwd, timeout=TIMEOUT,
                               stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT)
        except subprocess.TimeoutExpired:
            print("target-job: TIMEOUT after %ds on pass %d - task not completed" % (TIMEOUT, attempt))
            return 1
        out = p.stdout.decode("utf-8", "replace")
        out = _redact_secrets(out)          # TICKET 306: scrub before it can reach a log
        print(out[-4000:] if len(out) > 4000 else out)
        print("target-job: pass %d CLI exit_code=%d" % (attempt, p.returncode))
        if p.returncode != 0:
            low = out.lower()
            if any(m in low for m in ("api key not valid", "invalid api key", "please set bare_ai_api_key",
                                      "unauthenticated", "permission_denied", "no api key")):
                print("target-job: TEMPFAIL - credential missing/rejected; task NOT attempted")
                return 75
            return 1
        if not spec:
            print("target-job: no criterion supplied - single pass, poller remains the authority")
            return 0
        ok, report = _execute_criterion(spec)
        last_report = report
        print("target-job: criterion after pass %d:" % attempt)
        for line in report:
            print(line)
        if ok is None:
            print("target-job: no executable checks here - poller remains the authority")
            return 0
        if ok:
            print("target-job: CRITERION SATISFIED after %d pass(es)" % attempt)
            return 0
        feedback = ("Your previous attempt did not satisfy the acceptance criteria. "
                    "Verification output:\n\n" + "\n".join(report) +
                    "\n\nFix the failures and satisfy every criterion. Do not claim success without evidence.\n")
        print("target-job: criterion NOT satisfied - %s" %
              ("resuming with the failure output" if attempt < MAX_PASSES else "passes exhausted"))

    print("target-job: FAILED - criterion still unsatisfied after %d passes" % MAX_PASSES)
    for line in last_report:
        print(line)
    return 1


if __name__ == "__main__":
    sys.exit(main())
