#!/usr/bin/env python3
"""test_taskbus_supervisor_bridge.py - the poller side of the supervisor contract.

Hermetic: no gateway, no network, no claim. The DEFAULT matters most - with
TASKBUS_SUPERVISOR unset every bridge helper must be inert so the legacy
dispatch and close behaviour is bit-for-bit unchanged.
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
    spec = importlib.util.spec_from_file_location("taskbus_poller_under_test", POLLER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SupervisorBridgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.p = load_poller()

    def setUp(self):
        self._saved = os.environ.pop("TASKBUS_SUPERVISOR", None)

    def tearDown(self):
        os.environ.pop("TASKBUS_SUPERVISOR", None)
        if self._saved is not None:
            os.environ["TASKBUS_SUPERVISOR"] = self._saved

    # ---- flag OFF: everything inert -------------------------------------
    def test_flag_off_is_disabled(self):
        self.assertFalse(self.p.supervisor_enabled())

    def test_flag_off_env_map_empty(self):
        self.assertEqual(self.p.supervisor_env_map({"nonce": "abc"}, 42, "k", "http://x"), {})

    def test_flag_off_prefix_empty(self):
        self.assertEqual(self.p.supervisor_env_prefix({}), "")

    def test_flag_off_for_claim_empty(self):
        self.assertEqual(self.p.supervisor_env_for_claim({"nonce": "abc"}, 42), {})

    # ---- flag ON: env export -------------------------------------------
    def test_flag_on_exports_everything(self):
        os.environ["TASKBUS_SUPERVISOR"] = "1"
        env = self.p.supervisor_env_map({"nonce": "n-123"}, 691, api_key="bck-key", api_base="http://gw:80/")
        self.assertEqual(env["TASKBUS_SUPERVISOR"], "1")
        self.assertEqual(env["TASKBUS_CLAIM_NONCE"], "n-123")
        self.assertEqual(env["TASKBUS_ROW_ID"], "691")
        self.assertEqual(env["TASKBUS_API_KEY"], "bck-key")
        self.assertEqual(env["TASKBUS_API_BASE"], "http://gw:80")

    def test_flag_on_omits_missing_nonce(self):
        os.environ["TASKBUS_SUPERVISOR"] = "1"
        env = self.p.supervisor_env_map({}, 7, api_key="k", api_base="http://gw")
        self.assertNotIn("TASKBUS_CLAIM_NONCE", env)
        self.assertEqual(env["TASKBUS_ROW_ID"], "7")

    def test_prefix_quotes_values(self):
        os.environ["TASKBUS_SUPERVISOR"] = "1"
        prefix = self.p.supervisor_env_prefix({"B": "two words", "A": "1"})
        self.assertTrue(prefix.startswith("A=1 B="))
        self.assertIn("'two words'", prefix)
        self.assertTrue(prefix.endswith(" "))

    # ---- SUPERVISOR_RESULT parsing -------------------------------------
    def test_parses_envelope_amid_noise(self):
        payload = {"outcome": "verified", "passes": 2, "status_hint": "HumanReviewNeeded"}
        text = "target-job: pass 1\nnoise line\nSUPERVISOR_RESULT " + json.dumps(payload) + "\ntrailing"
        parsed = self.p.parse_supervisor_result(text)
        self.assertEqual(parsed["outcome"], "verified")
        self.assertEqual(parsed["passes"], 2)

    def test_returns_none_without_envelope(self):
        self.assertIsNone(self.p.parse_supervisor_result("nothing here"))

    def test_returns_none_on_malformed_json(self):
        self.assertIsNone(self.p.parse_supervisor_result("SUPERVISOR_RESULT {broken"))

    def test_returns_none_on_non_object(self):
        self.assertIsNone(self.p.parse_supervisor_result("SUPERVISOR_RESULT [1,2,3]"))

    # ---- close body mapping --------------------------------------------
    def test_verified_maps_to_human_review_not_completed(self):
        body = self.p.supervisor_close_body({"outcome": "verified", "passes": 1,
                                             "status_hint": "HumanReviewNeeded"})
        self.assertEqual(body["status"], "HumanReviewNeeded")
        self.assertNotEqual(body["status"], "Completed")

    def test_blocked_hint_carries_reason(self):
        body = self.p.supervisor_close_body({"outcome": "blocked", "status_hint": "Blocked",
                                             "reason": "needs a credential"})
        self.assertEqual(body["status"], "Blocked")
        self.assertIn("needs a credential", body["re_opened_or_retry_reason"])

    def test_unknown_hint_downgrades_to_human_review(self):
        body = self.p.supervisor_close_body({"outcome": "verified", "status_hint": "Completed"})
        self.assertEqual(body["status"], "HumanReviewNeeded")

    def test_budget_exhaustion_is_human_review(self):
        body = self.p.supervisor_close_body({"outcome": "budget_exhausted", "passes": 5,
                                             "status_hint": "HumanReviewNeeded"})
        self.assertEqual(body["status"], "HumanReviewNeeded")

    def test_none_input_returns_none(self):
        self.assertIsNone(self.p.supervisor_close_body(None))

    def test_body_includes_log_excerpt(self):
        body = self.p.supervisor_close_body({"outcome": "verified"}, log_excerpt="last words here")
        self.assertIn("last words here", body["machines_response"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
