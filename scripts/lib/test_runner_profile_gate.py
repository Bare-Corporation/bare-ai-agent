#!/usr/bin/env python3
"""test_runner_profile_gate.py - behavioural tests for the T4 runner gate.

The runner file has dashes in its name, so it cannot be imported normally; it is
loaded by path. Every test clears the env vars the gate reads, so the suite is
hermetic and does not depend on the ambient shell.

The important cases are the NEGATIVE ones: a corrupted snapshot must be refused,
and a profile-bound row with no snapshot must be refused rather than silently
falling back to the node's local role.
"""
import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

RUNNER = os.path.join(HERE, "taskbus-agent-job.py")
FIXTURE = os.path.join(HERE, "tests", "fixtures", "t4-claim-payload.json")

ENV_KEYS = ("TASKBUS_CLAIM_PAYLOAD", "TASKBUS_CLAIM_JSON",
            "TASKBUS_PROFILE_REQUIRED")


def load_runner():
    spec = importlib.util.spec_from_file_location("taskbus_agent_job", RUNNER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def load_fixture():
    with io.open(FIXTURE, encoding="utf-8") as fh:
        return json.load(fh)


class GateTestBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.runner = load_runner()

    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in ENV_KEYS}
        for k in ENV_KEYS:
            os.environ.pop(k, None)
        self._tmp = []

    def tearDown(self):
        for k in ENV_KEYS:
            os.environ.pop(k, None)
            if self._saved.get(k) is not None:
                os.environ[k] = self._saved[k]
        for p in self._tmp:
            try:
                os.unlink(p)
            except OSError:
                pass

    def write_payload(self, payload):
        fd, path = tempfile.mkstemp(suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False)
        self._tmp.append(path)
        return path


class TestPositive(GateTestBase):
    def test_valid_claim_payload_verifies(self):
        os.environ["TASKBUS_CLAIM_PAYLOAD"] = FIXTURE
        ctx = self.runner.profile_gate()
        self.assertIsNotNone(ctx)
        self.assertEqual(ctx["slug"], "example-profile")
        self.assertTrue(all(c["ok"] for c in ctx["checks"]))
        self.assertGreater(ctx["markdown_bytes"], 1000)

    def test_inline_json_is_read(self):
        os.environ["TASKBUS_CLAIM_JSON"] = json.dumps(load_fixture())
        ctx = self.runner.profile_gate()
        self.assertIsNotNone(ctx)
        self.assertTrue(ctx["verified"])

    def test_audit_stamp_is_produced(self):
        os.environ["TASKBUS_CLAIM_PAYLOAD"] = FIXTURE
        ctx = self.runner.profile_gate()
        stamp = self.runner.profile_gate_stamp(ctx)
        self.assertIn("execution_profile verified", stamp)
        self.assertIn(ctx["profile_hash"][:16], stamp)

    def test_gate_json_reports_applied(self):
        os.environ["TASKBUS_CLAIM_PAYLOAD"] = FIXTURE
        ctx = self.runner.profile_gate()
        line = self.runner.profile_gate_json(ctx=ctx)
        self.assertTrue(line.startswith("PROFILE_GATE_JSON: "))
        body = json.loads(line.split(":", 1)[1].strip())
        self.assertTrue(body["ok"])
        self.assertTrue(body["applied"])
        self.assertEqual(body["slug"], "example-profile")


class TestFailClosed(GateTestBase):
    def test_corrupted_markdown_is_refused(self):
        payload = load_fixture()
        block = payload["execution_profile"]
        block["snapshot"]["role"]["content_markdown"] += " INJECTED"
        # recompute profile_hash so ONLY the role hash can catch it
        pc = importlib.util.spec_from_file_location(
            "pc_for_test", os.path.join(HERE, "profile_context.py"))
        mod = importlib.util.module_from_spec(pc)
        pc.loader.exec_module(mod)
        block["profile_hash"] = mod.sha256_hex_json(block["snapshot"])
        os.environ["TASKBUS_CLAIM_PAYLOAD"] = self.write_payload(payload)
        with self.assertRaises(self.runner.ProfileGateFailure) as cm:
            self.runner.profile_gate()
        self.assertEqual(cm.exception.reason, "profile_hash_mismatch")
        self.assertEqual(cm.exception.which, "role_content_sha256")

    def test_unsupported_snapshot_version_is_unusable(self):
        payload = load_fixture()
        payload["execution_profile"]["snapshot"]["snapshot_version"] = 2
        os.environ["TASKBUS_CLAIM_PAYLOAD"] = self.write_payload(payload)
        with self.assertRaises(self.runner.ProfileGateUnusable):
            self.runner.profile_gate()

    def test_missing_block_is_unusable(self):
        payload = load_fixture()
        payload.pop("execution_profile", None)
        os.environ["TASKBUS_CLAIM_PAYLOAD"] = self.write_payload(payload)
        with self.assertRaises(self.runner.ProfileGateUnusable):
            self.runner.profile_gate()

    def test_unreadable_payload_is_unusable(self):
        os.environ["TASKBUS_CLAIM_PAYLOAD"] = "/nonexistent/claim.json"
        with self.assertRaises(self.runner.ProfileGateUnusable):
            self.runner.profile_gate()

    def test_absent_payload_when_required_is_refused(self):
        """A profile-bound row must NOT silently fall back to the local role."""
        with self.assertRaises(self.runner.ProfileGateUnusable) as cm:
            self.runner.profile_gate(required=True)
        self.assertEqual(cm.exception.reason, "profile_snapshot_unusable")
        self.assertEqual(cm.exception.which, "snapshot_absent")

    def test_failure_json_is_not_ok(self):
        try:
            self.runner.profile_gate(required=True)
        except self.runner.ProfileGateFailure as exc:
            line = self.runner.profile_gate_json(failure=exc)
            body = json.loads(line.split(":", 1)[1].strip())
            self.assertFalse(body["ok"])
            self.assertEqual(body["reason"], "profile_snapshot_unusable")
        else:
            self.fail("gate did not refuse an absent required payload")


class TestInertWhenUnbound(GateTestBase):
    def test_absent_payload_not_required_is_inert(self):
        self.assertIsNone(self.runner.profile_gate())

    def test_absent_payload_report_is_not_applied(self):
        line = self.runner.profile_gate_json(ctx=None)
        body = json.loads(line.split(":", 1)[1].strip())
        self.assertTrue(body["ok"])
        self.assertFalse(body["applied"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
