#!/usr/bin/env python3
"""Deploy an OpenNext Worker to Cloudflare with the token injected from Vault.

The API token is fetched at runtime and passed to the child process ONLY through
its environment. It is never written to disk and never placed on argv, so it
cannot leak via shell history or a process listing.

Usage:
    deploy_cf_worker.py <repo_path> <vault_secret_path> [--dry-run] [--bin NAME]

Example:
    deploy_cf_worker.py ~/Bare-ConnectFi-Website-frontend \
        cloudflare/bare-connectfi-landing
"""
import json
import os
import ssl
import subprocess
import sys
import urllib.request

import vault_auth

VAULT_ENV = os.path.expanduser("~/.bare-ai/config/vault.env")

# Repo basename -> canonical host used for the post-deploy IndexNow ping.
INDEXNOW_HOSTS = {
    "cloudintegrationcorporation-frontend": "www.cloudintegrationcorporation.com",
    "bare-erp-front-end": "bare-ai.net",
    "Bare-ConnectFi-Website-frontend": "bare-connectfi.net",
}
PING_SCRIPT = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "ping_indexnow.py"
)


def vault_addr():
    addr = os.environ.get("VAULT_ADDR")
    if addr:
        return addr.rstrip("/")
    with open(VAULT_ENV, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line.startswith("export VAULT_ADDR="):
                return line.split("=", 1)[1].strip().strip('"').rstrip("/")
    raise SystemExit("VAULT_ADDR is not configured")


def fetch_secret(path):
    token = os.environ.get("VAULT_TOKEN")
    if not token:
        raise SystemExit("VAULT_TOKEN not present in environment - refusing to deploy")
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    url = "{0}/v1/secret/data/{1}".format(vault_addr(), path)
    req = urllib.request.Request(url, headers={"X-Vault-Token": token})
    with urllib.request.urlopen(req, context=ctx, timeout=15) as resp:
        return json.load(resp)["data"]["data"]


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if len(args) < 2:
        raise SystemExit(__doc__)
    repo = os.path.expanduser(args[0])
    secret_path = args[1]
    binary = "opennextjs-cloudflare"
    if "--bin" in sys.argv:
        binary = sys.argv[sys.argv.index("--bin") + 1]

    try:
        data = vault_auth.fetch_secret(secret_path)
    except vault_auth.VaultUnavailable as exc:
        raise SystemExit("Vault unavailable - refusing to deploy: {0}".format(exc))
    api_token = data.get("api_token")
    account_id = data.get("account_id")
    if not api_token or not account_id:
        raise SystemExit("Vault secret {0} missing api_token/account_id".format(secret_path))

    worker = os.path.join(repo, ".open-next", "worker.js")
    if not os.path.exists(worker):
        raise SystemExit("No build found at {0} - run the OpenNext build first".format(worker))

    env = dict(os.environ)
    env["CLOUDFLARE_API_TOKEN"] = api_token
    env["CLOUDFLARE_ACCOUNT_ID"] = account_id
    env["CI"] = "1"

    cmd = [os.path.join(repo, "node_modules", ".bin", binary), "deploy"]

    if "--dry-run" in sys.argv:
        print("dry-run OK: token and account id resolved from Vault")
        print("would execute: {0} (cwd={1})".format(" ".join(cmd), repo))
        return 0

    print("Deploying {0} from {1}".format(binary, repo))
    rc = subprocess.run(cmd, cwd=repo, env=env, text=True).returncode

    # Post-deploy IndexNow ping. Best-effort on purpose: a search-engine
    # notification must never turn a good deploy into a failed one.
    if rc == 0 and "--no-indexnow" not in sys.argv:
        ping_indexnow(repo)
    return rc


def ping_indexnow(repo):
    host = INDEXNOW_HOSTS.get(os.path.basename(os.path.normpath(repo)))
    if not host:
        print("IndexNow: no host mapped for {0} - skipped".format(repo))
        return
    if not os.path.exists(PING_SCRIPT):
        print("IndexNow: {0} missing - skipped".format(PING_SCRIPT))
        return
    print("IndexNow: submitting {0} ...".format(host))
    try:
        proc = subprocess.run(
            [sys.executable, PING_SCRIPT, "--host", host], text=True
        )
        if proc.returncode != 0:
            print("IndexNow: submission reported a failure (deploy itself succeeded)")
    except Exception as exc:
        print("IndexNow: ping error {0} (deploy itself succeeded)".format(exc))


if __name__ == "__main__":
    sys.exit(main())
