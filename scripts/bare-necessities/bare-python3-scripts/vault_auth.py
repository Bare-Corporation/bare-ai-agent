#!/usr/bin/env python3
"""Shared Vault read helper with an AppRole fallback.

Order of preference:
  1. The ambient VAULT_TOKEN (what the agent normally carries).
  2. If that token is rejected AND VAULT_ROLE_ID + VAULT_SECRET_ID are both present,
     log in via AppRole and retry once. This is the pattern documented in the fleet
     constitution, and it means a long-running session whose token TTL lapses can
     re-authenticate itself instead of needing a human.

Raises VaultUnavailable with a precise reason if neither path works. Callers must
treat that as fatal and must NOT fall back to a cached or backup token.
"""
import json
import os
import ssl
import urllib.error
import urllib.request

VAULT_ENV = os.path.expanduser("~/.bare-ai/config/vault.env")


class VaultUnavailable(RuntimeError):
    pass


def _parse_env_file(path):
    out = {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                if line.startswith("export "):
                    line = line[len("export "):]
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip().strip('"').strip("'")
    except FileNotFoundError:
        pass
    return out


def vault_addr():
    addr = os.environ.get("VAULT_ADDR") or _parse_env_file(VAULT_ENV).get("VAULT_ADDR")
    if not addr:
        raise VaultUnavailable("VAULT_ADDR is not configured")
    return addr.rstrip("/")


def _ctx():
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _get(path, token):
    req = urllib.request.Request(
        vault_addr() + path, headers={"X-Vault-Token": token}
    )
    with urllib.request.urlopen(req, context=_ctx(), timeout=15) as resp:
        return json.load(resp)


def _approle_login():
    """AppRole login. Two shapes are supported:

    1. role_id only - works when the role has bind_secret_id=false (this fleet's
       bare-ai-role is configured that way). This is the documented procedure for
       minting a short-lived in-memory token when the ambient one has lapsed.
    2. role_id + secret_id - the conventional AppRole shape, used when a
       VAULT_SECRET_ID happens to be available.
    """
    env = _parse_env_file(VAULT_ENV)
    role_id = os.environ.get("VAULT_ROLE_ID") or env.get("VAULT_ROLE_ID")
    secret_id = os.environ.get("VAULT_SECRET_ID") or env.get("VAULT_SECRET_ID")
    if not role_id:
        return None
    payload = {"role_id": role_id}
    if secret_id:
        payload["secret_id"] = secret_id
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        vault_addr() + "/v1/auth/approle/login",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, context=_ctx(), timeout=15) as resp:
            payload = json.load(resp)
        return payload["auth"]["client_token"]
    except Exception:
        return None


def fetch_secret(path):
    """Read secret/data/<path> and return its data dict."""
    token = os.environ.get("VAULT_TOKEN")
    reason = None

    if token:
        try:
            return _get("/v1/secret/data/" + path, token)["data"]["data"]
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:200]
            reason = "VAULT_TOKEN rejected (HTTP %s): %s" % (exc.code, detail)
        except Exception as exc:
            reason = "VAULT_TOKEN read failed: %s" % exc
    else:
        reason = "VAULT_TOKEN not present in environment"

    fresh = _approle_login()
    if fresh:
        os.environ["VAULT_TOKEN"] = fresh
        try:
            return _get("/v1/secret/data/" + path, fresh)["data"]["data"]
        except Exception as exc:
            raise VaultUnavailable(
                reason + "; AppRole login succeeded but the read still failed: %s" % exc
            )

    raise VaultUnavailable(
        reason
        + "; AppRole re-login unavailable (need VAULT_ROLE_ID and VAULT_SECRET_ID). "
        "Refresh the token or add VAULT_SECRET_ID to ~/.bare-ai/config/vault.env."
    )
