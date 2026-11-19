#!/usr/bin/env python3
"""test_taskbus_supervisor_close_path.py - the poller CLOSE path, end to end.

Hermetic: no gateway, no network, no claim. It drives Poller.report_outcome with
a stub bus and a stubbed job log, so the assertions are about the close body the
poller would send, not about transport.

What it pins down:
  1. TASKBUS_SUPERVISOR unset  -> the legacy close body, even when the runner
     emitted a SUPERVISOR_RESULT (the flag is the gate, not the envelope).
  2. TASKBUS_SUPERVISOR=1 with a SUPERVISOR_RESULT -> the supervisor mapping
     decides the close body (status + summary + retry reason when Blocked).
  3. TASKBUS_SUPERVISOR=1 with NO envelope -> legacy close body (fallback).
  4. A supervisor hint of Completed can never reach the board as Completed.
"""
import importlib.util
import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

POLLER = os.path.join(HERE, "taskbus-poller.py")


def load_poller():
    spec = importlib.util.spec_from_file_location("taskbus_poller_close_under_test", POLLER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeBus:
    def __init__(self, row):
        self.row = row
        self.dry_run = False
        self.patches = []

    def get_row(self, rid):
        return self.row

    def patch(self, rid, payload, why, allow_reclaim=True):
        self.patches.append(
            {"rid": rid, "payload": payload, "why": why, "allow_reclaim": allow_reclaim}
        )
        return {}


def envelope(**kw):
    payload = {
        "outcome": "verified",
        "passes": 2,
        "reason": "criterion satisfied",
        "state_path": "/tmp/supervisor/901.json",
        "status_hint": "HumanReviewNeeded",
    }
    payload.update(kw)
    return "target-job: pass 1" + chr(10) + "noise" + chr(10) + "SUPERVISOR_RESULT " + json.dumps(payload) + chr(10) + "trailing line"


class SupervisorClosePathTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.p = load_poller()

    def setUp(self):
        self._saved = os.environ.pop("TASKBUS_SUPERVISOR", None)
        self._saved_tail = self.p.job_log_tail
        self._saved_guard = self.p.completion_guard

    def tearDown(self):
        os.environ.pop("TASKBUS_SUPERVISOR", None)
        if self._saved is not None:
            os.environ["TASKBUS_SUPERVISOR"] = self._saved
        self.p.job_log_tail = self._saved_tail
        self.p.completion_guard = self._saved_guard

    # ---- helpers -------------------------------------------------------
    def _row(self):
        return {
            "id": 901,
            "status": {"value": "InProgress"},
            "machines_response": "prior note",
            "corresponding_machine_task_id": "job-1",
        }

    def _run(self, tail_text, allowed=True, exit_code=0):
        self.p.job_log_tail = lambda *a, **k: tail_text
        self.p.completion_guard = lambda row, node=None, exit_code=None: (
            allowed,
            [] if allowed else ["criterion file missing"],
            ["evidence: file_exists ok"] if allowed else [],
        )
        bus = FakeBus(self._row())
        poller = self.p.Poller(bus)
        poller.report_outcome(self._row(), {"status": "done", "exit_code": exit_code}, None)
        self.assertEqual(len(bus.patches), 1, "expected exactly one close write")
        return bus.patches[0]

    # ---- 1. flag OFF wins over the envelope ----------------------------
    def test_flag_off_ignores_supervisor_envelope(self):
        patch = self._run(envelope(status_hint="Blocked"))
        payload = patch["payload"]
        self.assertEqual(payload["status"], "HumanReviewNeeded")
        self.assertIn("job finished (exit 0)", payload["machines_response"])
        self.assertNotIn("supervisor outcome=", payload["machines_response"])

    # ---- 2. flag ON, Blocked hint --------------------------------------
    def test_flag_on_blocked_hint_drives_the_close(self):
        os.environ["TASKBUS_SUPERVISOR"] = "1"
        patch = self._run(envelope(status_hint="Blocked", reason="passes exhausted"))
        payload = patch["payload"]
        self.assertEqual(payload["status"], "Blocked")
        self.assertIn("supervisor outcome=verified passes=2", payload["machines_response"])
        self.assertIn("reason: passes exhausted", payload["machines_response"])
        self.assertIn("/tmp/supervisor/901.json", payload["machines_response"])
        self.assertEqual(payload["re_opened_or_retry_reason"], "passes exhausted")
        self.assertIn("--- executor output (tail) ---", payload["machines_response"])
        self.assertIn("SUPERVISOR_RESULT", payload["machines_response"])
        self.assertFalse(patch["allow_reclaim"], "a close must never re-claim")

    # ---- 2b. flag ON, verified review ----------------------------------
    def test_flag_on_verified_hint_reviews_never_completes(self):
        os.environ["TASKBUS_SUPERVISOR"] = "1"
        patch = self._run(envelope(status_hint="HumanReviewNeeded"))
        payload = patch["payload"]
        self.assertEqual(payload["status"], "HumanReviewNeeded")
        self.assertIn("supervisor outcome=verified", payload["machines_response"])
        self.assertNotIn("re_opened_or_retry_reason", payload)

    # ---- 3. flag ON but no envelope -> legacy --------------------------
    def test_flag_on_without_envelope_falls_back_to_legacy(self):
        os.environ["TASKBUS_SUPERVISOR"] = "1"
        patch = self._run("plain job output, no envelope")
        payload = patch["payload"]
        self.assertEqual(payload["status"], "HumanReviewNeeded")
        self.assertIn("job finished (exit 0)", payload["machines_response"])
        self.assertNotIn("supervisor outcome=", payload["machines_response"])

    # ---- 4. Completed can never be written -----------------------------
    def test_supervisor_cannot_ask_for_completed(self):
        os.environ["TASKBUS_SUPERVISOR"] = "1"
        patch = self._run(envelope(status_hint="Completed"))
        self.assertEqual(patch["payload"]["status"], "HumanReviewNeeded")

    # ---- 5. a failed job with an envelope also uses the mapping --------
    def test_flag_on_failed_job_uses_envelope(self):
        os.environ["TASKBUS_SUPERVISOR"] = "1"
        patch = self._run(envelope(status_hint="Blocked", outcome="not_verified"), exit_code=1)
        payload = patch["payload"]
        self.assertEqual(payload["status"], "Blocked")
        self.assertIn("supervisor outcome=not_verified", payload["machines_response"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
