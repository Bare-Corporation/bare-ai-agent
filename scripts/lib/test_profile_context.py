#!/usr/bin/env python3
"""test_profile_context.py - behavioural tests for the T4 snapshot verifier.

Uses a SYNTHETIC claim payload as the happy-path fixture, then
tampers with it. The interesting cases are the ones that isolate a single check:

  - mutating execution_config alone also changes profile_hash, so the profile check
    would fire first. To prove config_hash has teeth on its own, the test mutates
    execution_config and RECOMPUTES profile_hash, so only the config check can fail.
  - likewise for a role hash that disagrees in one place but not another.
"""
import copy
import hashlib
import io
import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import profile_context as pc  # noqa: E402

FIXTURE = os.path.join(HERE, "tests", "fixtures", "t4-claim-payload.json")


def load_fixture():
    with io.open(FIXTURE, encoding="utf-8") as fh:
        return json.load(fh)


class TestHappyPath(unittest.TestCase):
    def test_real_payload_verifies(self):
        ctx = pc.verify(load_fixture())
        self.assertTrue(ctx["verified"])
        self.assertEqual(ctx["slug"], "example-profile")
        self.assertTrue(all(c["ok"] for c in ctx["checks"]))
        self.assertGreater(ctx["markdown_bytes"], 1000)
        self.assertEqual(ctx["markdown_bytes"],
                         len(ctx["system_prompt"].encode("utf-8")))
        # byte length must exceed char length: the role text has multi-byte chars
        self.assertGreater(ctx["markdown_bytes"], ctx["markdown_chars"])

    def test_system_prompt_is_the_snapshot_markdown(self):
        payload = load_fixture()
        ctx = pc.verify(payload)
        self.assertEqual(ctx["system_prompt"],
                         payload["execution_profile"]["snapshot"]["role"]["content_markdown"])

    def test_router_params_extracted(self):
        ctx = pc.verify(load_fixture())
        self.assertIn(ctx["router"]["routing_strategy"],
                      ("cheapest_overall", "cheapest_frontier", "cheapest_flash",
                       "cheapest_lite", "cheapest_vision", "cheapest_under_ceiling"))


class TestFailClosed(unittest.TestCase):
    def test_tampered_role_markdown_is_refused(self):
        payload = copy.deepcopy(load_fixture())
        snap = payload["execution_profile"]["snapshot"]
        snap["role"]["content_markdown"] = snap["role"]["content_markdown"] + " injected"
        # recompute profile_hash so ONLY the role hash can catch this
        payload["execution_profile"]["profile_hash"] = pc.sha256_hex_json(snap)
        with self.assertRaises(pc.ProfileHashMismatch) as cm:
            pc.verify(payload)
        self.assertEqual(cm.exception.which, "role_content_sha256")

    def test_tampered_execution_config_is_refused(self):
        payload = copy.deepcopy(load_fixture())
        snap = payload["execution_profile"]["snapshot"]
        snap["execution_config"]["routing_strategy"] = "cheapest_vision"
        # recompute profile_hash so the profile check passes and config must catch it
        payload["execution_profile"]["profile_hash"] = pc.sha256_hex_json(snap)
        with self.assertRaises(pc.ProfileHashMismatch) as cm:
            pc.verify(payload)
        self.assertEqual(cm.exception.which, "config_hash")

    def test_tampered_snapshot_is_refused_by_profile_hash(self):
        payload = copy.deepcopy(load_fixture())
        payload["execution_profile"]["snapshot"]["profile"]["version"] = 99
        with self.assertRaises(pc.ProfileHashMismatch) as cm:
            pc.verify(payload)
        self.assertEqual(cm.exception.which, "profile_hash")

    def test_internally_inconsistent_role_hash_is_refused(self):
        payload = copy.deepcopy(load_fixture())
        block = payload["execution_profile"]
        # make execution_config assert a different role hash, then keep the other
        # hashes self-consistent so only the cross-check can catch it
        block["snapshot"]["execution_config"]["role_content_sha256"] = "0" * 64
        block["profile_hash"] = pc.sha256_hex_json(block["snapshot"])
        block["config_hash"] = pc.sha256_hex_json(block["snapshot"]["execution_config"])
        with self.assertRaises(pc.ProfileHashMismatch) as cm:
            pc.verify(payload)
        self.assertIn("execution_config.role_content_sha256", cm.exception.which)

    def test_missing_snapshot_block_is_refused(self):
        payload = load_fixture()
        payload.pop("execution_profile", None)
        with self.assertRaises(pc.ProfileContextError):
            pc.verify(payload)

    def test_unsupported_snapshot_version_is_refused(self):
        payload = copy.deepcopy(load_fixture())
        payload["execution_profile"]["snapshot"]["snapshot_version"] = 2
        with self.assertRaises(pc.ProfileContextError):
            pc.verify(payload)


class TestAuditStamp(unittest.TestCase):
    def test_stamp_contains_all_three_hashes_and_no_role_text(self):
        ctx = pc.verify(load_fixture())
        stamp = pc.audit_stamp(ctx)
        self.assertIn(ctx["profile_hash"][:16], stamp)
        self.assertIn(ctx["config_hash"][:16], stamp)
        self.assertIn(ctx["role_content_sha256"][:16], stamp)
        self.assertNotIn("BARE-AI", stamp)


if __name__ == "__main__":
    unittest.main(verbosity=2)
