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
        # The key is deliberately NOT exported: supervisor_env_prefix turns this map
        # into a REMOTE ssh command line, where a key would be visible in ps on the
        # target node. The runner sources its own instead.
        self.assertNotIn("TASKBUS_API_KEY", env)
        self.assertNotIn("bck-key", repr(env))
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

    # ---- the agent key must never be exported --------------------------
    def test_env_map_never_carries_the_api_key(self):
        os.environ["TASKBUS_SUPERVISOR"] = "1"
        env = self.p.supervisor_env_map({"nonce": "n-1"}, 12, api_key="bck-secret-value")
        self.assertNotIn("TASKBUS_API_KEY", env)
        self.assertNotIn("bck-secret-value", repr(env))

    def test_prefix_never_carries_the_api_key(self):
        os.environ["TASKBUS_SUPERVISOR"] = "1"
        env = self.p.supervisor_env_map({"nonce": "n-1"}, 12, api_key="bck-secret-value")
        prefix = self.p.supervisor_env_prefix(env)
        self.assertNotIn("TASKBUS_API_KEY", prefix)
        self.assertNotIn("bck-secret-value", prefix)
        # Control: the prefix is not empty, so the assertions above mean something.
        self.assertIn("TASKBUS_CLAIM_NONCE=n-1", prefix)

    def test_env_for_claim_never_carries_the_api_key(self):
        os.environ["TASKBUS_SUPERVISOR"] = "1"
        env = self.p.supervisor_env_for_claim({"nonce": "n-2"}, 13)
        self.assertNotIn("TASKBUS_API_KEY", env)
        self.assertEqual(env["TASKBUS_CLAIM_NONCE"], "n-2")

    # ---- TASKBUS_API_BASE resolves from TASKBUS_GATEWAY_BASE -----------
    # The module constants are read at import, so they are patched on the module
    # rather than through the environment: that is the only way to simulate a
    # process that started with TASKBUS_API_BASE unset.
    def _with_bases(self, api_base, gateway_base, fn):
        saved = (self.p.TASKBUS_API_BASE, self.p.GATEWAY_BASE)
        try:
            self.p.TASKBUS_API_BASE = api_base
            self.p.GATEWAY_BASE = gateway_base
            return fn()
        finally:
            self.p.TASKBUS_API_BASE, self.p.GATEWAY_BASE = saved

    def test_api_base_falls_back_to_gateway_base(self):
        os.environ["TASKBUS_SUPERVISOR"] = "1"
        env = self._with_bases(
            "", "http://gw-fallback:9000/",
            lambda: self.p.supervisor_env_map({"nonce": "n-3"}, 14))
        self.assertEqual(env["TASKBUS_API_BASE"], "http://gw-fallback:9000")

    def test_api_base_prefers_taskbus_api_base_over_gateway_base(self):
        os.environ["TASKBUS_SUPERVISOR"] = "1"
        env = self._with_bases(
            "http://api-base:81", "http://gw-fallback:9000",
            lambda: self.p.supervisor_env_map({"nonce": "n-4"}, 15))
        self.assertEqual(env["TASKBUS_API_BASE"], "http://api-base:81")

    def test_api_base_prefers_an_explicit_argument_over_both(self):
        os.environ["TASKBUS_SUPERVISOR"] = "1"
        env = self._with_bases(
            "http://api-base:81", "http://gw-fallback:9000",
            lambda: self.p.supervisor_env_map({"nonce": "n-5"}, 16,
                                              api_base="http://arg:82"))
        self.assertEqual(env["TASKBUS_API_BASE"], "http://arg:82")

    def test_api_base_omitted_when_no_source_is_set(self):
        # Control for the fallback: with all three unset there is no base at all.
        os.environ["TASKBUS_SUPERVISOR"] = "1"
        env = self._with_bases(
            "", "",
            lambda: self.p.supervisor_env_map({"nonce": "n-6"}, 17))
        self.assertNotIn("TASKBUS_API_BASE", env)

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
