#!/usr/bin/env python3
"""taskbus-agent-job.py - TARGET-SIDE runner for a task-bus job.

Invoked on the target node by the .4 poller, via dispatch-job.sh:
    dispatch-job.sh <uuid> python3 /home/bare-ai/.bare-ai/lib/taskbus-agent-job.py <prompt_file>

It runs THIS NODE'S OWN AGENT IDENTITY (its role.md + technical constitution), not .4's -
each machine should act as itself. The task text arrives in the prompt file so the target
needs no Baserow credential of its own.
"""
import json, os, subprocess, sys, tempfile
import ssl
import urllib.request, urllib.error

HOME = os.path.expanduser("~")
VAULT_ENV = os.path.join(HOME, ".bare-ai", "config", "vault.env")
VAULT_SECRET_PATH = "secret/data/bare-ai-brain/config"
VAULT_KEY_FIELD = "api_key"
CA_FILE = os.path.join(HOME, ".bare-ai", "ca", "vault-ca.crt")


CENTRAL_MODELS_URL = "https://api.bare-ai.net/v1/models"
DEFAULT_SHORTCUT = os.environ.get("TASKBUS_MODEL_SHORTCUT", "303")
FALLBACK_ENDPOINT = "https://api.deepseek.com/v1/chat/completions"
FALLBACK_MODEL = "deepseek-v4-flash"


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
    AppRole secret_id, so a dispatched job can authenticate without .4 sending secrets
    over the wire. Without this the target had no model credential at all and every
    cross-machine job exited 75 (TEMPFAIL) - found on the first real dispatch."""
    cfg = load_env(VAULT_ENV)
    addr = (cfg.get("VAULT_ADDR") or "").rstrip("/")
    rid, sid = cfg.get("VAULT_ROLE_ID"), cfg.get("VAULT_SECRET_ID")
    if not (addr and rid and sid):
        return None, "vault config incomplete"
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
            body = json.dumps({"role_id": rid, "secret_id": sid}).encode()
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
        rq2 = urllib.request.Request("%s/v1/%s" % (addr, VAULT_SECRET_PATH))
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
# on .13/.18/.19 (they ship node at /usr/bin/node), so jobs dispatched to those nodes died
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

# ---- criterion execution -------------------------------------------------
# The POLLER parses success_criterion (single source of truth for the grammar) and ships the
# parsed checks here as JSON. This runner only EXECUTES them, so the grammar can never diverge.
# Kinds: http_status[url,code] | file_exists[path] | file_md5[path,hex] | file_sha256[path,hex]
#        git_ls_remote[...] -> not executable here; reported as deferred (the poller re-checks).
def _run_check(kind, args):
    import hashlib, urllib.request, urllib.error
    try:
        if kind == "http_status":
            url, want = args[0], int(args[1])
            req = urllib.request.Request(url, method="GET")
            try:
                with urllib.request.urlopen(req, timeout=20) as r:
                    got = r.status
            except urllib.error.HTTPError as e:
                got = e.code
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


def main():
    if len(sys.argv) < 2:
        print("usage: taskbus-agent-job.py <prompt_file> [workspace_dir]", file=sys.stderr)
        return 2
    pf = sys.argv[1]
    try:
        task = open(pf, encoding="utf-8").read()
    except OSError as e:
        print("cannot read prompt file %s: %s" % (pf, e), file=sys.stderr)
        return 2

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
    vkey, vsrc = vault_api_key()
    if vkey:
        env["BARE_AI_API_KEY"] = vkey
        print("target-job: credential source = Vault (%s)" % vsrc)
    else:
        print("target-job: credential source = local env (%s)" % vsrc)
    if not (env.get("BARE_AI_API_KEY") or env.get("BARE_AI_ENDPOINT")):
        print("target-job: TEMPFAIL - no model credential available on this node; "
              "task NOT attempted, no attempt consumed", file=sys.stderr)
        return 75

    sc = shortcut_from_prompt(task) or DEFAULT_SHORTCUT
    cep, cmodel, cnote = central_route(sc)
    if cep and cmodel:
        env["BARE_AI_ENDPOINT"], env["BARE_AI_MODEL"] = cep, cmodel
        print("target-job: model route = %s -> model=%s endpoint=%s" % (cnote, cmodel, cep))
    else:
        env.setdefault("BARE_AI_ENDPOINT", FALLBACK_ENDPOINT)
        env.setdefault("BARE_AI_MODEL", FALLBACK_MODEL)
        print("target-job: central list UNAVAILABLE (%s) - HARDCODED fallback %s" % (cnote, FALLBACK_MODEL))
    env.pop("NODE_TLS_REJECT_UNAUTHORIZED", None)
    ca = os.path.join(HOME, ".bare-ai", "ca", "vault-ca.crt")
    if os.path.exists(ca):
        env["NODE_EXTRA_CA_CERTS"] = ca
    env["BARE_AI_NO_TOOLS"] = "false"          # explicit; --tools is passed too, belt and braces
    env.setdefault("BARE_AI_DISABLE_WORKSPACE_TRUST", "true")

    task_path = os.path.join(cwd, ".taskbus-task.prompt")
    with open(task_path, "w", encoding="utf-8") as fh:
        fh.write(task)

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
