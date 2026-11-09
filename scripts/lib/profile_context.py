#!/usr/bin/env python3
"""profile_context.py - verify and act on the execution-profile snapshot from a claim.

WHY THIS EXISTS
The claim gate returns an immutable snapshot of the profile that authorised the
claim. If the runner ignores it, or trusts it without checking, a row can execute
under role text the gateway never verified. This module is the runner's half of
that contract: it re-derives every hash locally and refuses to proceed on any
mismatch.

WHAT IT CHECKS (all three must pass, fail-closed)
  1. profile_hash  == sha256(canonical_json(snapshot))
  2. config_hash   == sha256(canonical_json(snapshot.execution_config))
  3. role_content_sha256 == sha256(utf-8 bytes of snapshot.role.content_markdown)
     and the snapshot's role.content_sha256, execution_config.role_content_sha256
     and the top-level role.content_sha256 must all agree with each other.

CANONICALISATION must match the gateway exactly: sorted keys, compact separators,
ensure_ascii=False, UTF-8, no trailing newline. note the markdown is hashed as
BYTES: the role text is ~189k characters but ~189.7k bytes because the em dashes
are multi-byte, so len(str) is not the byte count.

CLI
    python3 profile_context.py --claim-payload payload.json [--json]
"""
from __future__ import annotations

import hashlib
import json
import sys

SNAPSHOT_VERSION_SUPPORTED = 1


class ProfileHashMismatch(Exception):
    """Raised when a snapshot cannot be trusted. The runner must NOT execute."""

    def __init__(self, which: str, expected: str, actual: str, detail: str = ""):
        self.which = which
        self.expected = expected
        self.actual = actual
        self.detail = detail
        super().__init__("profile_hash_mismatch:%s expected=%s actual=%s %s"
                         % (which, expected[:16], actual[:16], detail))

    @property
    def code(self) -> str:
        return "profile_hash_mismatch"


class ProfileContextError(Exception):
    """The payload has no usable snapshot, or is structurally invalid."""

    @property
    def code(self) -> str:
        return "profile_snapshot_unusable"


def canonical_bytes(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def sha256_hex_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def sha256_hex_json(obj) -> str:
    return sha256_hex_bytes(canonical_bytes(obj))


def extract(payload: dict) -> dict:
    """Return the execution_profile block, or raise if the claim carried none."""
    if not isinstance(payload, dict):
        raise ProfileContextError("claim payload is not an object")
    block = payload.get("execution_profile")
    if not isinstance(block, dict):
        raise ProfileContextError(
            "claim payload has no execution_profile block - the row is not bound to an "
            "Active profile, so there is no snapshot to verify")
    snap = block.get("snapshot")
    if not isinstance(snap, dict):
        raise ProfileContextError("execution_profile.snapshot missing or not an object")
    ver = snap.get("snapshot_version")
    if ver != SNAPSHOT_VERSION_SUPPORTED:
        raise ProfileContextError("unsupported snapshot_version %r" % (ver,))
    if not isinstance(snap.get("role"), dict):
        raise ProfileContextError("snapshot.role missing")
    if not isinstance(snap.get("execution_config"), dict):
        raise ProfileContextError("snapshot.execution_config missing")
    return block


def verify(payload: dict) -> dict:
    """Verify the snapshot and return a context dict. Raises on any mismatch."""
    block = extract(payload)
    snap = block["snapshot"]
    ecfg = snap["execution_config"]
    role = snap["role"]

    markdown = role.get("content_markdown")
    if not isinstance(markdown, str):
        raise ProfileContextError("snapshot.role.content_markdown missing or not a string")

    checks = []

    # 1. profile_hash over the whole snapshot
    expected_profile = str(block.get("profile_hash") or "")
    actual_profile = sha256_hex_json(snap)
    checks.append(("profile_hash", expected_profile, actual_profile))
    if not expected_profile or expected_profile != actual_profile:
        raise ProfileHashMismatch("profile_hash", expected_profile, actual_profile)

    # 2. config_hash over execution_config only
    expected_config = str(block.get("config_hash") or "")
    actual_config = sha256_hex_json(ecfg)
    checks.append(("config_hash", expected_config, actual_config))
    if not expected_config or expected_config != actual_config:
        raise ProfileHashMismatch("config_hash", expected_config, actual_config)

    # 3. role content, hashed as BYTES
    markdown_sha = sha256_hex_bytes(markdown.encode("utf-8"))
    expected_role = str(role.get("content_sha256") or "")
    checks.append(("role_content_sha256", expected_role, markdown_sha))
    if not expected_role or expected_role != markdown_sha:
        raise ProfileHashMismatch("role_content_sha256", expected_role, markdown_sha)

    # 3b. the same role hash must agree everywhere it is asserted, or the snapshot
    # is internally inconsistent even though each individual hash is self-valid.
    ecfg_role = str(ecfg.get("role_content_sha256") or "")
    top_role = str((block.get("role") or {}).get("content_sha256") or "")
    for label, value in (("execution_config.role_content_sha256", ecfg_role),
                         ("execution_profile.role.content_sha256", top_role)):
        checks.append((label, markdown_sha, value))
        if value != markdown_sha:
            raise ProfileHashMismatch(label, markdown_sha, value,
                                      "snapshot is internally inconsistent")

    return {
        "profile_id": block.get("profile_id"),
        "slug": block.get("slug"),
        "version": block.get("version"),
        "profile_hash": expected_profile,
        "config_hash": expected_config,
        "role_content_sha256": markdown_sha,
        "role_alias": role.get("alias") or ecfg.get("role_alias"),
        "role_row_id": role.get("row_id"),
        "system_prompt": markdown,
        "router": {
            "routing_strategy": ecfg.get("routing_strategy"),
            "routing_ceiling_eur": ecfg.get("routing_ceiling_eur"),
            "exclude_providers": ecfg.get("exclude_providers") or [],
            "max_attempts": ecfg.get("max_attempts"),
            "require_human_review": ecfg.get("require_human_review"),
        },
        "resolved_at": block.get("resolved_at"),
        "markdown_bytes": len(markdown.encode("utf-8")),
        "markdown_chars": len(markdown),
        "checks": [{"name": n, "expected": e, "actual": a, "ok": e == a}
                   for (n, e, a) in checks],
        "verified": True,
    }


def audit_stamp(ctx: dict) -> str:
    """One-line provenance for machines_response. Never includes role text."""
    return ("execution_profile verified: profile=%s v%s hash=%s config=%s role=%s "
            "markdown_bytes=%d strategy=%s ceiling=%s"
            % (ctx["slug"], ctx["version"], ctx["profile_hash"][:16],
               ctx["config_hash"][:16], ctx["role_content_sha256"][:16],
               ctx["markdown_bytes"], ctx["router"]["routing_strategy"],
               ctx["router"]["routing_ceiling_eur"]))


def main(argv):
    if "--claim-payload" not in argv:
        print("usage: profile_context.py --claim-payload FILE [--json]", file=sys.stderr)
        return 2
    path = argv[argv.index("--claim-payload") + 1]
    with open(path, encoding="utf-8") as fh:
        payload = json.load(fh)
    try:
        ctx = verify(payload)
    except ProfileHashMismatch as exc:
        print("REFUSED: %s" % exc)
        return 3
    except ProfileContextError as exc:
        print("REFUSED: %s" % exc)
        return 4
    if "--json" in argv:
        out = dict(ctx)
        out["system_prompt"] = "<%d chars>" % ctx["markdown_chars"]
        print(json.dumps(out, indent=2))
    else:
        for c in ctx["checks"]:
            print("%-40s %s  %s" % (c["name"], "OK" if c["ok"] else "MISMATCH",
                                    c["expected"][:16]))
        print(audit_stamp(ctx))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
