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
from datetime import datetime
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

# ── PROVIDER -> CREDENTIAL PATH (row 720/722, 2026-11-20) ────────────────────
# A model credential is only valid for the provider that issued it. Sending the
# node's DeepSeek key to Google's endpoint produced
#   400 INVALID_ARGUMENT "Please pass a valid API key"
# on every pass. The route therefore decides WHICH secret path is read.
#
# Measured on this node on 2026-11-20 (status codes only, no values):
#   secret/data/deepseek/config  200      secret/data/anthropic/config  403
#   secret/data/gemini/config    200      secret/data/google/config     403
#   secret/data/openai/config    200      (google is NOT the Gemini path here)
# A provider absent from this map has no credential on this node; a route to it is
# refused rather than signed with someone else's key.
PROVIDER_SECRET_PATHS = {
    "deepseek": "secret/data/deepseek/config",
    "gemini": "secret/data/gemini/config",
    "openai": "secret/data/openai/config",
    "anthropic": "secret/data/anthropic/config",
}


def provider_of_endpoint(url):
    """Which provider issues keys for this endpoint URL.

    Host-based, on purpose: model names carry a provider suffix that does not always
    agree with the base_url the catalogue returns, and it is the URL the client POSTs
    to that decides which key is accepted.
    """
    u = (url or "").strip().lower()
    if not u:
        return ""
    if "googleapis.com" in u or "generativelanguage" in u:
        return "gemini"
    if "deepseek.com" in u:
        return "deepseek"
    if "anthropic.com" in u:
        return "anthropic"
    if "openai.com" in u or "azure.com" in u:
        return "openai"
    if "moonshot" in u:
        return "moonshot"
    if "mistral" in u:
        return "mistral"
    if "localhost" in u or "127.0.0.1" in u or "ollama" in u:
        return "ollama"
    return ""

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


def _central_models():
    """(payload, error) from the CENTRAL model list. READ-ONLY, one fetch.

    A User-Agent is MANDATORY: Cloudflare answers the default python-urllib UA with
    403 while curl/browser/node UAs pass - verified. Without it the runner would
    conclude the central API was down and silently fall back to hardcoded values.
    """
    try:
        rq = urllib.request.Request(CENTRAL_MODELS_URL)
        rq.add_header("Accept", "application/json")
        rq.add_header("User-Agent", "bare-ai-taskbus/1.0 curl/8.5.0")
        with urllib.request.urlopen(rq, timeout=25) as r:
            return json.loads(r.read()), ""
    except Exception as e:
        return None, "%s: %s" % (type(e).__name__, str(e)[:70])


def _completions_url(base_url):
    """The full chat-completions URL the CLI needs, from a catalogue base_url.

    base_url arrives WITHOUT the completions path ('https://api.deepseek.com'), so:
        .../v1        -> + /chat/completions
        otherwise     -> + /v1/chat/completions
    (the Alibaba entries already end in /compatible-mode/v1, which resolves correctly)
    """
    bu = (base_url or "").strip().rstrip("/")
    if not bu:
        return ""
    if "://" not in bu:
        bu = "http://" + bu
    return bu + "/chat/completions" if bu.endswith("/v1") else bu + "/v1/chat/completions"


def _model_core(model_id):
    """The part before the pipe: the CLI REJECTS the 'deepseek-v4-flash|deepseek-cn' form."""
    return (model_id or "").split("|")[0].strip()


def central_route(shortcut):
    """Resolve (endpoint, model, note) from the CENTRAL model list by SHORTCUT.

    The list is the single source of truth for per-model routes and shortcut numbers
    (e.g. 303 -> deepseek-v4-flash), which is how the interactive launcher works.
    """
    data, err = _central_models()
    if data is None:
        return None, None, err
    for m in (data.get("models") or []):
        if str(m.get("shortcut")) != str(shortcut):
            continue
        mid = _model_core(m.get("model_id"))
        ep = _completions_url(m.get("base_url"))
        if not mid or not ep:
            return None, None, "shortcut %s incomplete in list" % shortcut
        return ep, mid, "central list shortcut %s" % shortcut
    return None, None, "shortcut %s not present in list" % shortcut


def catalogue_route_for_model(model_id):
    """Resolve (endpoint, model, note) from the CENTRAL model list by MODEL ID.

    Matches the stamped id first, then the id with the provider suffix stripped,
    because a snapshot and the list may disagree about the suffix.
    """
    want = (model_id or "").strip()
    if not want:
        return None, None, "no model id supplied"
    data, err = _central_models()
    if data is None:
        return None, None, err
    want_core = _model_core(want)
    for m in (data.get("models") or []):
        mid_full = (m.get("model_id") or "").strip()
        if not mid_full:
            continue
        mid_core = _model_core(mid_full)
        if mid_full != want and (not want_core or mid_core != want_core):
            continue
        ep = _completions_url(m.get("base_url"))
        if not ep or not mid_core:
            continue
        return ep, mid_core, "central list model %s" % mid_full
    return None, None, "model %s not present in the central list" % want


def apply_profile_route(ctx, env, row_model=None):
    """Make a VERIFIED snapshot's route authoritative over the node profile.

    WHY (row 697): .16's snapshot routed deepseek-v4-flash|deepseek-cn while
    ~/.bare-ai/profile.env said claude-sonnet-4-6 @ api.anthropic.com. The profile
    won because nothing here read the snapshot route, so a DeepSeek key was posted
    to Anthropic and answered 401. A verified snapshot is the authority for what
    the row runs on; the node profile only fills in when the snapshot names no
    route at all.

    WHERE THE ROUTE COMES FROM, in this order:
      1. the verified snapshot itself (execution_config / snapshot), via
         profile_context.profile_route();
      2. the ROW's stamped model_catalog, exported by the poller as
         TASKBUS_ROW_MODEL. Row 698 proved this is where the model actually is:
         execution_config carries only policy knobs (routing_strategy,
         exclude_providers, ceiling, max_attempts, require_human_review,
         role_alias, role hash), so the snapshot path found nothing and profile.env
         won again - a DeepSeek key was posted to Anthropic.
    The row model is used ONLY when the profile is VERIFIED, because an unverified
    row value must not get to overrule local configuration.

    Returns the applied route dict, or None when nothing usable was found - in
    which case the caller's profile/catalogue decision stands.
    """
    route = (ctx or {}).get("route") or None
    row_value = str(row_model or "").strip()
    if route:
        model = str(route.get("model_id") or "").strip()
        base = str(route.get("base_url") or "").strip()
        source = str(route.get("source") or "execution_profile")
    elif row_value and isinstance(ctx, dict) and ctx.get("verified") is True:
        model, base, source = row_value, "", "row.model_catalog"
    else:
        return None
    if not model and not base:
        return None
    if base:
        endpoint, resolved, note = _completions_url(base), _model_core(model), source
    else:
        endpoint, resolved, note = catalogue_route_for_model(model)
        if source != "execution_profile":
            # Keep the provenance: the catalogue note says how it resolved, not
            # where the model came from, and "row.model_catalog" is the part an
            # operator needs when the profile and the row disagree.
            note = "%s via %s" % (note, source)
    if not endpoint or not resolved:
        print("target-job: ROUTER WARNING execution_profile route %r could not be "
              "resolved (%s) - the node profile route stands" % (model, note))
        return None
    previous_endpoint = env.get("BARE_AI_ENDPOINT") or ""
    previous_model = env.get("BARE_AI_MODEL") or ""
    env["BARE_AI_ENDPOINT"] = endpoint
    env["BARE_AI_MODEL"] = resolved
    print("target-job: route source = execution_profile (%s) model=%s endpoint=%s"
          % (note, resolved, endpoint))
    if previous_endpoint and previous_endpoint != endpoint:
        # The key provenance is now worth checking: a key that belongs to the
        # overridden provider would fail exactly as row 697 did.
        print("target-job: ROUTER WARNING execution_profile overrode the node profile "
              "route (%s model=%s); confirm the credential matches %s"
              % (previous_endpoint, previous_model, resolved))
    return {"model": resolved, "endpoint": endpoint, "note": note}


def _route_context(snap_ctx, profile_ctx):
    """The VERIFIED execution-profile context to resolve the route from.

    TWO gates can produce one and they read different variables:
      * load_claim_snapshot() reads TASKBUS_CLAIM_FILE / TASKBUS_CLAIM_JSON;
      * profile_gate() reads TASKBUS_CLAIM_JSON / TASKBUS_CLAIM_PAYLOAD.
    The poller ships TASKBUS_CLAIM_PAYLOAD, so on a real dispatch snap_ctx is None
    and profile_ctx is the verified snapshot. Resolving from snap_ctx alone is what
    let profile.env win on row 701 while the row model sat in the environment.
    """
    for ctx in (snap_ctx, profile_ctx):
        if isinstance(ctx, dict) and ctx.get("verified") is True:
            return ctx
    return None


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


def vault_api_key(secret_path=None):
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
        # An explicit path (the route's provider) wins over the node default. Without
        # this, every job read the one path in model-credential.path no matter which
        # provider the endpoint belonged to.
        _p = (secret_path or "").strip()
        try:
            _fh = None if _p else open(
                os.path.join(HOME, ".bare-ai", "config", "model-credential.path"))
            if _fh is None:
                raise OSError("explicit provider path supplied")
            with _fh as _f:
                for _ln in _f:
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


# ---- heartbeat credential: environment first, then this node's Vault -------------
# WHY this exists: the poller used to export TASKBUS_API_KEY into the supervisor env
# prefix, which is rendered into the REMOTE ssh command line - so the key was visible
# in ps on the target node. The key is no longer sent, and the job resolves its own
# here, on the node that actually runs it. Cached because a heartbeat on a timer would
# otherwise pay a full AppRole login plus secret read on every beat.
_HEARTBEAT_KEY = {"key": ""}


AGENT_KEY_PATH_FILE = os.path.join(HOME, ".bare-ai", "config", "taskbus-agent-key.path")


def _agent_key_secret_path():
    """The Vault path holding THIS NODE's TaskBus agent key, or "".

    Order: the untracked per-node path file, then AGENT_KEY_SECRET_PATH. There is
    deliberately no built-in default, because the path contains the node id and
    this is a public repository.
    """
    try:
        with open(AGENT_KEY_PATH_FILE, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line and not line.startswith("#"):
                    return line
    except OSError:
        pass
    return (os.environ.get("AGENT_KEY_SECRET_PATH") or "").strip()


AGENT_KEY_FIELDS = ("taskbus_api_key", "api_key")


def _agent_key_field(payload):
    """The agent key inside a Vault KV-v2 read body, or "" when there is none.

    Field order matters: taskbus_api_key is what the per-node machine secret
    carries (verified on a freshly provisioned node, 2026-11-19); api_key is the
    older shape, kept so a node provisioned before the rename still boots. Nothing
    else is ever accepted here - a provider credential in this slot is a
    configuration fault, not a key to try anyway (row 702).
    """
    if not isinstance(payload, dict):
        return ""
    secret = payload.get("data")
    if isinstance(secret, dict) and isinstance(secret.get("data"), dict):
        secret = secret.get("data")
    if not isinstance(secret, dict):
        return ""
    for field in AGENT_KEY_FIELDS:
        value = secret.get(field)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _agent_api_key():
    """(key, source) for this node's TaskBus AGENT key - a bck- key, NEVER a model key.

    WHY THIS EXISTS (row 702): the heartbeat used vault_api_key(), which reads
    model-credential.path - on this fleet that is a provider secret (sk-...). The
    gateway answered 401 to every heartbeat, the lease was never extended, and the
    row was re-claimed while attempt 1's child was still running.

    A model credential is never acceptable here: if the resolved value does not
    look like an agent key, that is a configuration fault, not a key to try anyway.
    """
    env_key = (os.environ.get("TASKBUS_API_KEY") or "").strip()
    if env_key:
        return env_key, "env"
    if _HEARTBEAT_KEY["key"]:
        return _HEARTBEAT_KEY["key"], "vault-cached"
    path = _agent_key_secret_path()
    if not path:
        return "", ("no TaskBus agent key: set %s (or AGENT_KEY_SECRET_PATH) to this "
                    "node's agent-key secret path" % AGENT_KEY_PATH_FILE)
    cfg = load_env(VAULT_ENV)
    addr = (cfg.get("VAULT_ADDR") or "").rstrip("/")
    rid, sid = cfg.get("VAULT_ROLE_ID"), cfg.get("VAULT_SECRET_ID")
    if not (addr and rid):
        return "", "vault config incomplete (need VAULT_ADDR + VAULT_ROLE_ID)"
    ctx = ssl.create_default_context(cafile=CA_FILE) if os.path.exists(CA_FILE) else None
    try:
        ident = {"role_id": rid}
        if sid:
            ident["secret_id"] = sid
        login = urllib.request.Request(addr + "/v1/auth/approle/login",
                                       data=json.dumps(ident).encode(), method="POST")
        login.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(login, timeout=20, context=ctx) as resp:
            token = (json.loads(resp.read()).get("auth") or {}).get("client_token")
        if not token:
            return "", "approle login returned no token"
        read = urllib.request.Request(addr + "/v1/" + path)
        read.add_header("X-Vault-Token", token)
        with urllib.request.urlopen(read, timeout=20, context=ctx) as resp:
            data = json.loads(resp.read())
    except Exception as exc:                          # noqa: BLE001 - reported, not raised
        return "", "agent-key fetch failed: %s" % str(exc)[:120]
    key = _agent_key_field(data)
    if not key:
        return "", "agent-key secret has no taskbus_api_key or api_key field"
    _HEARTBEAT_KEY["key"] = key
    return key, "vault"


def _heartbeat_api_key():
    """The heartbeat credential: this node's TaskBus agent key, nothing else."""
    return _agent_api_key()


def _reset_heartbeat_api_key_cache():
    """Forget the cached key. Test seam; also the hook for a forced re-read."""
    _HEARTBEAT_KEY["key"] = ""


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


def claim_snapshot_env_source():
    """Name the env var that supplies a claim snapshot, or None when none does.

    The two gates read different variables: load_claim_snapshot() understands
    TASKBUS_CLAIM_FILE and TASKBUS_CLAIM_JSON, while profile_gate() understands
    TASKBUS_CLAIM_JSON and TASKBUS_CLAIM_PAYLOAD. A source understood by only one
    of them is still a supplied snapshot, so this list must cover both, or the
    caller reports the legacy path while the profile gate is about to run.
    """
    for _name in ("TASKBUS_CLAIM_JSON", "TASKBUS_CLAIM_FILE",
                  "TASKBUS_CLAIM_PAYLOAD"):
        if (os.environ.get(_name) or "").strip():
            return _name
    return None


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


# --- Stage 0 supervised runner (flag-gated; default OFF) ---------------------
# Why: the legacy path makes one CLI call per pass and only inspects the exit
# code. With TASKBUS_SUPERVISOR=1 the runner instead drives a state-file loop
# with per-criterion evidence, a lease heartbeat and a process-group kill-switch.
# With the flag unset the code below is never reached and behaviour is identical.

def _supervisor_enabled():
    return (os.environ.get("TASKBUS_SUPERVISOR") or "").strip().lower() in ("1", "true", "yes", "on")


def _supervisor_module():
    try:
        import taskbus_supervisor as sup
        return sup
    except Exception:                                # noqa: BLE001
        return None


def _criterion_label(criteria):
    """One stable label for a parsed criterion list.

    The supervisor keys its state file, its brief and its evidence by a criterion
    LABEL string, while _execute_criterion() takes the parsed check list. This is
    the bridge between the two. It must stay deterministic: the same label is
    registered on pass 1 and matched when evidence arrives on pass N.
    """
    parts = []
    for check in criteria or []:
        if not isinstance(check, dict):
            continue
        kind = str(check.get("kind") or "").strip()
        if not kind:
            continue
        args = check.get("args") or []
        parts.append((kind + ":" + ";".join(str(a) for a in args)) if args else kind)
    return " ; ".join(parts)


def _supervisor_criterion_spec(task_text, criteria=None):
    """The criterion LABEL for the supervisor brief, or "" when none was supplied.

    WHY the ticket envelope is no longer read here: taskbus_ticket_meta() returns
    (row_id, title) - a 2-tuple, never a mapping - so calling .get() on it raised
    AttributeError on the first real supervised dispatch (row 694). The envelope
    carries no criterion in any case: the POLLER parses success_criterion and ships
    the result as TASKBUS_CRITERION_JSON, which main() decodes and passes in here.
    task_text stays in the signature for callers that pass it positionally.
    """
    del task_text
    spec = (os.environ.get("TASKBUS_CRITERION") or "").strip()
    if spec:
        return spec
    return _criterion_label(criteria)


def _supervised_main(pf, cwd, task_path, task_text, env, max_passes, timeout,
                     criterion_spec=None):
    sup = _supervisor_module()
    if sup is None:
        print("supervisor: taskbus_supervisor module unavailable - falling back to legacy path")
        return None
    row_id = (os.environ.get("TASKBUS_ROW_ID") or "").strip() or "unknown"
    workdir = cwd or os.getcwd()
    state_path = os.path.join(workdir, ".taskbus-state-%s.md" % row_id)
    # Fail closed HERE, where the child env is handed over. main() resolves the
    # credential before calling in (profile -> model-credentials.env -> Vault ->
    # catalogue route) and the supervisor passes the same env to every child, so
    # this only fires when a caller or a refactor supplies an unresolved
    # environment. Without it the CLI answers with its own "Please set
    # BARE_AI_API_KEY" text, which reads like a credential outage rather than the
    # wiring fault it is.
    if not (env.get("BARE_AI_API_KEY") or env.get("BARE_AI_ENDPOINT")):
        print("supervisor: REFUSING - the child environment has neither "
              "BARE_AI_API_KEY nor BARE_AI_ENDPOINT, so the CLI cannot route; "
              "the caller must resolve credentials before the supervised run "
              "(task NOT attempted, no attempt consumed)", file=sys.stderr)
        return 75
    # criterion_spec is the PARSED check list main() decoded from the poller's
    # TASKBUS_CRITERION_JSON. The supervisor keys on a label string; only
    # _execute_criterion takes the parsed list.
    parsed = [c for c in (criterion_spec or []) if isinstance(c, dict)]
    label = _supervisor_criterion_spec(task_text, parsed)
    criteria = [label] if label else []
    spawn_depth = int((os.environ.get("TASKBUS_SPAWN_DEPTH") or "0").strip() or 0)

    try:
        with open(task_path, encoding="utf-8") as fh:
            task_body = fh.read()
    except OSError:
        task_body = task_text

    # Set by on_lost the instant the claim stops being ours. Read by run_cli's
    # drain watch so the child dies mid-pass, not at the next pass boundary -
    # row 702 left a 1000 s child running beside a re-claim.
    _lost = {"reason": ""}

    def _on_claim_lost(reason):
        _lost["reason"] = str(reason or "claim lost")
        print("supervisor: claim LOST (%s) - killing the active child" % _lost["reason"],
              flush=True)

    def run_cli(prompt_text, pass_no):
        prompt_file = os.path.join(workdir, ".taskbus-supervised-pass%d.prompt" % pass_no)
        with open(prompt_file, "w", encoding="utf-8") as fh:
            fh.write(prompt_text)
        cmd = [NODE, CLI, "--yolo", "--tools"]
        if pass_no > 1:
            cmd += ["--resume", "latest"]
        cmd += ["-p", "", "--prompt-file", prompt_file, "-o", "json"]
        proc = subprocess.Popen(cmd, env=env, cwd=workdir, stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                start_new_session=True)
        try:
            # watch makes the drain return the moment the claim is lost, so the
            # kill below happens mid-stream instead of after the child exits.
            out, err = sup.drain_streams(proc, watch=lambda: bool(_lost["reason"]))
            if _lost["reason"]:
                sup.terminate_process_group(proc)
            else:
                try:
                    proc.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    sup.terminate_process_group(proc)
        finally:
            if proc.poll() is None:
                sup.terminate_process_group(proc)
        result = {"exit_code": proc.returncode if proc.returncode is not None else -1,
                  "stdout": out.decode("utf-8", "replace"),
                  "stderr": err.decode("utf-8", "replace")}
        if parsed:
            try:
                ok, report = _execute_criterion(parsed)
            except Exception as exc:                 # noqa: BLE001
                ok, report = False, ["criterion check failed: %s" % exc]
            if ok and label:
                # The SAME string the supervisor registered, or the evidence is
                # rejected as unattached and the run can never reach verified.
                result["evidence"] = [{"criterion": label,
                                       "evidence": " | ".join(report)[:800]}]
        return result

    def heartbeat_call():
        nonce = (os.environ.get("TASKBUS_CLAIM_NONCE") or "").strip()
        if not nonce:
            raise RuntimeError("no TASKBUS_CLAIM_NONCE supplied")
        base = (os.environ.get("TASKBUS_API_BASE") or "").rstrip("/")
        if not base:
            raise RuntimeError("no TASKBUS_API_BASE supplied")
        key, key_source = _heartbeat_api_key()
        if not key:
            raise RuntimeError("no heartbeat credential: TASKBUS_API_KEY is unset and "
                               "the Vault fetch failed (%s)" % key_source)
        if key_source == "vault":
            # Once per process, because the key is cached after this. Never the key
            # itself - this line goes to the job log and is copied into the row.
            print("supervisor: heartbeat credential sourced from Vault on this node")
        url = "%s/v1/taskbus/rows/%s/heartbeat" % (base, row_id)
        req = urllib.request.Request(url, data=b"{}", method="POST",
                                     headers={"X-API-Key": key, "X-Claim-Nonce": nonce,
                                              "Content-Type": "application/json",
                                              "User-Agent": "bare-ai-supervisor/1.0"})
        # One observable line per beat, flushed: a heartbeat that fails silently
        # cannot be diagnosed from a log that never mentions it (row 702).
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                status = getattr(resp, "status", 200)
                body = resp.read()[:400]
        except urllib.error.HTTPError as exc:
            excerpt = ""
            try:
                excerpt = exc.read().decode("utf-8", "replace")[:160]
            except Exception:                        # noqa: BLE001
                pass
            print("supervisor: heartbeat row=%s status=%s url=%s error=%r"
                  % (row_id, exc.code, url, excerpt), flush=True)
            # 403/409/410 mean ownership or the nonce is gone: FATAL, act now.
            raise sup.HeartbeatLost("heartbeat HTTP %s %s" % (exc.code, excerpt),
                                    status=exc.code)
        except Exception as exc:                     # noqa: BLE001 - transport/unknown
            print("supervisor: heartbeat row=%s status=transport-error url=%s error=%r"
                  % (row_id, url, str(exc)[:160]), flush=True)
            raise sup.HeartbeatLost("heartbeat transport error: %s" % str(exc)[:160])
        lease = ""
        try:
            lease = (json.loads(body or b"{}") or {}).get("lease_expires_at") or ""
        except ValueError:
            pass
        print("supervisor: heartbeat row=%s status=%s url=%s lease_expires_at=%s"
              % (row_id, status, url, lease or "<none>"), flush=True)

    have_nonce = bool((os.environ.get("TASKBUS_CLAIM_NONCE") or "").strip())
    print("supervisor: enabled row=%s state=%s criteria=%d depth=%d heartbeat=%s"
          % (row_id, os.path.basename(state_path), len(criteria), spawn_depth,
             "on" if have_nonce else "DISABLED(no nonce)"))

    def reflect(text):
        return sup.reflect_to_role_diary(
            os.path.join(os.path.expanduser("~"), ".bare-ai", "role.md"),
            os.path.join(os.path.expanduser("~"), "bare-necessities-workspace",
                         "bare-ai-diary", _today_stamp() + ".md"),
            text)

    result = sup.run_supervised_loop(
        row_id=row_id, task_text=task_body, state_path=state_path,
        run_cli=run_cli, heartbeat_call=heartbeat_call if have_nonce else None,
        allowed_paths=_supervisor_allowed_paths(workdir), criteria=criteria,
        spawn_depth=spawn_depth, max_passes=max_passes,
        on_lost=_on_claim_lost,
        max_execution_seconds=min(timeout * max(1, max_passes), sup.DEFAULT_MAX_EXECUTION_SECONDS),
        reflect=reflect, log=lambda m: print(m, flush=True))

    print("SUPERVISOR_RESULT " + json.dumps({
        "outcome": result["outcome"], "reason": result["reason"],
        "passes": result["passes"], "state_path": result["state_path"],
        "status_hint": "HumanReviewNeeded",
    }, sort_keys=True))
    return 0 if result["outcome"] == "verified" else 1


def _supervisor_allowed_paths(workdir):
    paths = [workdir]
    inc = os.path.join(os.path.expanduser("~"), ".bare-ai", "config", "job-include-dirs")
    try:
        with open(inc, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line and not line.startswith("#"):
                    paths.append(line)
    except OSError:
        pass
    return paths


def _today_stamp():
    return datetime.now().strftime("%Y-%m-%d")


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
        # A snapshot can arrive through a variable this gate does not read: the
        # poller sets TASKBUS_CLAIM_PAYLOAD, which profile_gate() verifies below.
        # Announcing the legacy path here was a false diagnostic that sent an
        # operator hunting for a gate that does run.
        _claim_source = claim_snapshot_env_source()
        if _claim_source is None:
            print("target-job: no claim snapshot supplied - legacy path, no profile gate")
        else:
            print("target-job: claim snapshot supplied via %s - the profile gate below verifies it" % _claim_source)

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
    # 1b. A VERIFIED execution_profile route OUTRANKS the node profile (rows 697,
    #     698). The route comes from the snapshot when it carries one, otherwise from
    #     the model the poller stamped on the row (TASKBUS_ROW_MODEL). Applied here,
    #     before the catalogue block, so the catalogue is skipped once a route is set.
    _route_ctx = _route_context(_snap_ctx, profile_ctx)
    _profile_route = apply_profile_route(_route_ctx, env,
                                        row_model=os.environ.get("TASKBUS_ROW_MODEL", ""))
    if env.get("BARE_AI_API_KEY"):
        print("target-job: credential source = node profile/model-credentials.env")

    # 2. Vault key is a FILL-IN only; it never overrides a profile credential.
    #    The path is chosen by the PROVIDER THE ROUTE RESOLVED TO, not by the single
    #    node default: a Gemini endpoint needs a Google key even when the node's
    #    model-credential.path points at DeepSeek.
    if not env.get("BARE_AI_API_KEY"):
        _route_provider = provider_of_endpoint(env.get("BARE_AI_ENDPOINT"))
        _route_path = PROVIDER_SECRET_PATHS.get(_route_provider)
        vkey, vsrc = vault_api_key(_route_path)
        if vkey:
            env["BARE_AI_API_KEY"] = vkey
            env["BARE_AI_CREDENTIAL_PROVIDER"] = _route_provider
            print("target-job: credential source = Vault (%s) for provider=%s"
                  % (vsrc, _route_provider or "unknown"))
        else:
            print("target-job: no profile credential; vault says (%s) for provider=%s path=%s"
                  % (vsrc, _route_provider or "unknown", _route_path or "node default"))

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
    # THE authoritative provenance line: one place, after every route decision has
    # been made. The "route source = ..." lines above are pre-override history and
    # read as though the node profile won even when the snapshot overrode it.
    print("target-job: route in effect = %s model=%s endpoint=%s"
          % ("execution_profile" if _profile_route else "catalogue/node profile",
             env.get("BARE_AI_MODEL"), env.get("BARE_AI_ENDPOINT")))

    # 4b. HARD PROVIDER/CREDENTIAL CONSISTENCY GATE (row 720/722).
    # The old code printed "confirm the credential matches" and then sent the request
    # anyway. A warning is not a control. If we know which provider issued the key and
    # it is not the provider that owns the endpoint, refuse the pass: the attempt is
    # not consumed and the row is retried with a correct credential instead of being
    # burned on a 400 the provider can never accept.
    _ep_provider = provider_of_endpoint(env.get("BARE_AI_ENDPOINT"))
    _key_provider = (env.get("BARE_AI_CREDENTIAL_PROVIDER") or "").strip()
    if _ep_provider and _key_provider and _ep_provider != _key_provider:
        print("target-job: TEMPFAIL - credential/endpoint provider mismatch: "
              "endpoint is %s but the key belongs to %s. Refusing to send a key to "
              "the wrong provider; task NOT attempted."
              % (_ep_provider, _key_provider), file=sys.stderr)
        return 75
    if _ep_provider and not _key_provider:
        print("target-job: credential provider unknown for endpoint provider=%s; "
              "proceeding (the provider will reject a wrong key)" % _ep_provider)
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
    if _supervisor_enabled():
        supervised = _supervised_main(pf, cwd, task_path, task, env, MAX_PASSES, TIMEOUT,
                                      criterion_spec=spec)
        if supervised is not None:
            return supervised
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
