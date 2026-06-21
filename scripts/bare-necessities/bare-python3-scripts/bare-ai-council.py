#!/usr/bin/env python3
"""
version: v0.0.1
bare-ai-council.py — Bare-AI Council API client
Part of the bare-necessities toolkit. Deployed to:
  ~/bare-necessities-workspace/scripts/bare-python3-scripts/bare-ai-council.py
Symlinked to: /usr/local/bin/council.py

Invoke from bare-ai-cli via run_shell_command, or directly from the terminal.

Usage:
  council.py "your task here"
  council.py "your task here" --models claude-sonnet-4-6 deepseek-v4-pro
  council.py "your task here" --rounds 3 --json

Options:
  --models M1 M2     Models to use (default: claude-sonnet-4-6, deepseek-v4-pro)
  --rounds N         Max debate rounds per stage (default: 2)
  --roles R1 R2      Agent roles (default: Senior Engineer, Critical Reviewer)
  --json             Emit structured JSON output (for proxy/tool_call integration)
  --timeout N        Max seconds to wait for result (default: 600)
  --poll N           Poll interval in seconds (default: 10)

Exit codes:
  0 — Council reached agreement
  1 — Error (API, timeout, etc.)
  2 — Council did NOT reach agreement (result still printed)

Environment (optional overrides — Vault is the primary source):
  BARE_COUNCIL_API_KEY  — Override the API key (skips Vault lookup entirely)
  BARE_COUNCIL_URL      — Override the base URL

Vault path (primary credential source — same pattern as all other cloud models):
  secret/data/bare-ai-council/config  →  fields: api_key, base_url

PROXY NOTE (Option 2 integration):
  If this script is wrapped by an OpenAI-compatible proxy, the proxy must
  block internally on the poll loop before returning a response — the
  bare-ai-cli expects a synchronous reply. The CLI will appear unresponsive
  for the full council deliberation time (~2-4 minutes). Set your proxy's
  read timeout well above DEFAULT_TIMEOUT (600s) or the connection will
  drop before the result arrives.
"""

import sys
import os
import ssl
import time
import json
import argparse
import urllib.request
import urllib.error

# ── Config ────────────────────────────────────────────────────────────────────

DEFAULT_BASE_URL = "https://api.bare-ai.net/v1/council"
DEFAULT_MODELS   = ["claude-sonnet-4-6", "deepseek-v4-pro"]
DEFAULT_ROLES    = ["Senior Engineer", "Critical Reviewer"]
DEFAULT_ROUNDS   = 2
DEFAULT_TIMEOUT  = 600   # 10 min ceiling
DEFAULT_POLL     = 10    # seconds between status checks

VAULT_SECRET_PATH = "secret/data/bare-ai-council/config"

# ── Vault Helpers ─────────────────────────────────────────────────────────────

def _ssl_context() -> ssl.SSLContext:
    """Permissive SSL context — mirrors the -k flag used throughout bare-ai."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _load_vault_env() -> dict:
    """
    Read Vault credentials from env vars and/or vault.env.
    Env vars take priority (for CI/cron with explicit exports).
    """
    result = {}
    env_keys = {"VAULT_ADDR", "VAULT_ROLE_ID", "VAULT_SECRET_ID"}

    for key in env_keys:
        val = os.environ.get(key)
        if val:
            result[key] = val

    vault_env_path = os.path.expanduser("~/.bare-ai/config/vault.env")
    if os.path.exists(vault_env_path):
        with open(vault_env_path) as f:
            for line in f:
                line = line.strip()
                for key in env_keys:
                    if key not in result and (
                        line.startswith(f"export {key}=") or
                        line.startswith(f"{key}=")
                    ):
                        result[key] = line.split("=", 1)[1].strip().strip('"\'')
    return result


def _vault_approle_login(vault_addr: str, role_id: str, secret_id: str) -> str:
    """Authenticate with Vault via AppRole. Returns a short-lived client token."""
    url     = f"{vault_addr}/v1/auth/approle/login"
    payload = json.dumps({"role_id": role_id, "secret_id": secret_id}).encode()
    req     = urllib.request.Request(
        url, data=payload,
        headers={"Content-Type": "application/json"},
        method="POST"
    )
    try:
        with urllib.request.urlopen(req, context=_ssl_context()) as resp:
            return json.loads(resp.read())["auth"]["client_token"]
    except Exception as e:
        print(f"[council] Vault AppRole login failed: {e}", file=sys.stderr)
        sys.exit(1)


def resolve_council_config() -> tuple[str, str]:
    """
    Returns (api_key, base_url) for the Council API.

    Priority:
      1. BARE_COUNCIL_API_KEY env var — explicit override, skips Vault entirely.
         Use for CI, cron, or manual testing only.
      2. Vault at secret/data/bare-ai-council/config — the primary source,
         retrieved via AppRole exactly like every other cloud model key.

    Exits with a clear, actionable error message if neither source has a key.
    Purchase a Council API key at https://bare-ai.net then store it with:
      vault kv put secret/bare-ai-council/config \\
        api_key='<your-key>' \\
        base_url='https://api.bare-ai.net/v1/council'
    """
    # Priority 1: explicit env var override
    env_key = os.environ.get("BARE_COUNCIL_API_KEY")
    if env_key:
        return env_key, os.environ.get("BARE_COUNCIL_URL", DEFAULT_BASE_URL)

    # Priority 2: Vault AppRole (same mechanism as claude-sonnet-4-6, deepseek-v4-pro etc.)
    creds   = _load_vault_env()
    missing = [k for k in ("VAULT_ADDR", "VAULT_ROLE_ID", "VAULT_SECRET_ID")
               if not creds.get(k)]
    if missing:
        print(
            f"[council] ERROR: Missing Vault credentials: {', '.join(missing)}\n"
            f"Ensure ~/.bare-ai/config/vault.env contains "
            f"VAULT_ADDR, VAULT_ROLE_ID, and VAULT_SECRET_ID.",
            file=sys.stderr
        )
        sys.exit(1)

    token = _vault_approle_login(
        creds["VAULT_ADDR"], creds["VAULT_ROLE_ID"], creds["VAULT_SECRET_ID"]
    )

    secret_url = f"{creds['VAULT_ADDR']}/v1/{VAULT_SECRET_PATH}"
    req = urllib.request.Request(
        secret_url,
        headers={"X-Vault-Token": token},
        method="GET"
    )
    try:
        with urllib.request.urlopen(req, context=_ssl_context()) as resp:
            secret_data = json.loads(resp.read()).get("data", {}).get("data", {})
            api_key  = secret_data.get("api_key", "")
            base_url = secret_data.get("base_url", DEFAULT_BASE_URL)

            if not api_key or api_key == "enterYourKey":
                print(
                    "[council] ERROR: Council API key not configured in Vault.\n"
                    "Purchase a key at https://bare-ai.net then run:\n"
                    "  vault kv put secret/bare-ai-council/config \\\n"
                    "    api_key='<your-key>' \\\n"
                    "    base_url='https://api.bare-ai.net/v1/council'",
                    file=sys.stderr
                )
                sys.exit(1)

            return api_key, base_url

    except urllib.error.HTTPError as e:
        if e.code == 404:
            print(
                f"[council] ERROR: {VAULT_SECRET_PATH} not found in Vault.\n"
                "Purchase a key at https://bare-ai.net then run:\n"
                "  vault kv put secret/bare-ai-council/config \\\n"
                "    api_key='<your-key>' \\\n"
                "    base_url='https://api.bare-ai.net/v1/council'",
                file=sys.stderr
            )
        else:
            print(f"[council] Vault secret fetch failed: HTTP {e.code}", file=sys.stderr)
        sys.exit(1)


# ── API Helpers ───────────────────────────────────────────────────────────────

def api_request(url: str, api_key: str, payload: dict | None = None) -> dict:
    """Single JSON API call. POST if payload given, GET otherwise."""
    headers = {"X-API-Key": api_key, "Content-Type": "application/json"}
    data    = json.dumps(payload).encode() if payload else None
    method  = "POST" if data else "GET"
    req     = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        print(f"[council] HTTP {e.code} from {url}: {body}", file=sys.stderr)
        sys.exit(1)
    except urllib.error.URLError as e:
        print(f"[council] Connection error: {e.reason}", file=sys.stderr)
        sys.exit(1)


def build_yaml(task: str, models: list, roles: list, rounds: int) -> str:
    agents = ""
    for i, (model, role) in enumerate(zip(models, roles), 1):
        is_lead  = i == 1
        sign_off = "End with [APPROVED] only when fully confident." if is_lead \
                   else "End with [APPROVED] only when you genuinely agree."
        agents += f"""      - id: agent{i}
        role: {role}
        model: {model}
        rank: {i}
        system_prompt: "You are a {role}. {sign_off}"
        temperature: 0.3
        max_tokens: 2000
"""
    return f"""pipeline_name: cli-council
vault_enabled: true
max_debate_rounds: {rounds}
stages:
  - name: analysis
    max_debate_rounds: {rounds}
    team:
{agents}"""


def poll_until_complete(base_url: str, job_id: str, api_key: str,
                        timeout: int, poll_interval: int) -> dict:
    deadline = time.time() + timeout
    spinner  = ["|", "/", "─", "\\"]
    tick     = 0
    while time.time() < deadline:
        status = api_request(f"{base_url}/status/{job_id}", api_key)
        state  = status.get("status", "unknown")
        if state == "complete":
            return status
        if state in ("failed", "error"):
            print(f"\n[council] Job {job_id} failed: {status}", file=sys.stderr)
            sys.exit(1)
        elapsed = int(time.time() - (deadline - timeout))
        tokens  = status.get("tokens_total", 0)
        print(
            f"\r  {spinner[tick % 4]}  [{state}] "
            f"{elapsed}s elapsed · {tokens} tokens so far",
            end="", flush=True
        )
        tick += 1
        time.sleep(poll_interval)

    print(f"\n[council] Timeout after {timeout}s waiting for {job_id}", file=sys.stderr)
    sys.exit(1)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Bare-AI Council API client",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("task",             help="Task or question for the Council")
    parser.add_argument("--models", nargs="+", default=DEFAULT_MODELS,
                        help="Models to use (space-separated)")
    parser.add_argument("--roles",  nargs="+", default=DEFAULT_ROLES,
                        help="Agent roles (space-separated, must match --models count)")
    parser.add_argument("--rounds", type=int,  default=DEFAULT_ROUNDS,
                        help=f"Max debate rounds (default: {DEFAULT_ROUNDS})")
    parser.add_argument("--json",   action="store_true",
                        help="Emit structured JSON (for proxy/tool_call integration)")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT,
                        help=f"Max wait seconds (default: {DEFAULT_TIMEOUT})")
    parser.add_argument("--poll",   type=int,  default=DEFAULT_POLL,
                        help=f"Poll interval seconds (default: {DEFAULT_POLL})")
    args = parser.parse_args()

    if len(args.models) != len(args.roles):
        print("[council] --models and --roles must have the same count", file=sys.stderr)
        sys.exit(1)

    # Resolve credentials from Vault (or env var override)
    api_key, base_url = resolve_council_config()

    yaml_cfg = build_yaml(args.task, args.models, args.roles, args.rounds)

    # Submit
    print(f"[council] Submitting to Bare-AI Council ({', '.join(args.models)}) ...")
    job    = api_request(f"{base_url}/run", api_key,
                         {"yaml_config": yaml_cfg, "task": args.task})
    job_id = job["job_id"]
    print(f"[council] Job ID: {job_id}  Pipeline: {job.get('pipeline_name')}")

    # Poll
    poll_until_complete(base_url, job_id, api_key, args.timeout, args.poll)
    print()  # newline after spinner

    # Fetch result
    result = api_request(f"{base_url}/result/{job_id}", api_key)

    # ── Output ────────────────────────────────────────────────────────────────
    agreed_overall = True

    if args.json:
        output = {
            "job_id":           job_id,
            "duration_seconds": result.get("duration_seconds"),
            "cost_usd":         result.get("cost_usd_billed"),
            "token_summary":    result.get("token_summary", {}),
            "stages":           []
        }
        for stage in result.get("stage_results", []):
            agreed_overall = agreed_overall and stage.get("agreed", False)
            output["stages"].append({
                "stage":        stage["stage_name"],
                "agreed":       stage["agreed"],
                "rounds":       stage["rounds"],
                "final_output": stage.get("final_output", "")
            })
        output["agreed"] = agreed_overall
        print(json.dumps(output, indent=2))

    else:
        dur  = result.get("duration_seconds", 0)
        cost = result.get("cost_usd_billed", 0)
        toks = result.get("token_summary", {})

        print(f"\n{'='*60}")
        print(f"  BARE-AI COUNCIL RESULT")
        print(f"  Job: {job_id}")
        print(f"  Duration: {dur:.1f}s  |  Cost: ${cost:.4f}")
        for model, t in toks.items():
            print(f"  {model}: {t.get('total', 0):,} tokens "
                  f"({t.get('prompt', 0):,} prompt + {t.get('completion', 0):,} completion)")
        print(f"{'='*60}\n")

        for stage in result.get("stage_results", []):
            agreed         = stage.get("agreed", False)
            agreed_overall = agreed_overall and agreed
            icon           = "✅" if agreed else "⚠️"
            print(f"{icon} Stage: {stage['stage_name']} "
                  f"| Agreed: {agreed} | Rounds: {stage['rounds']}\n")
            print(stage.get("final_output", "(no output)"))
            print()

        if not agreed_overall:
            print("⚠️  Council did NOT reach full agreement — review output carefully.")

    sys.exit(0 if agreed_overall else 2)


if __name__ == "__main__":
    main()