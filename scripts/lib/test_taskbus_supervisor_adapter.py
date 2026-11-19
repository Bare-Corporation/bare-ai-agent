#!/usr/bin/env python3
"""test_taskbus_supervisor_adapter.py - the flag gate on the live runner.

The point of these tests is the DEFAULT: with TASKBUS_SUPERVISOR unset the runner
must behave exactly as before, and the supervisor path must not be reachable.
The job file has dashes in its name, so it is loaded by path.
"""
import importlib.util
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

JOB = os.path.join(HERE, "taskbus-agent-job.py")


def load_job():
    spec = importlib.util.spec_from_file_location("taskbus_agent_job_under_test", JOB)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SupervisorFlagTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.job = load_job()

    def setUp(self):
        self._saved = os.environ.pop("TASKBUS_SUPERVISOR", None)

    def tearDown(self):
        os.environ.pop("TASKBUS_SUPERVISOR", None)
        if self._saved is not None:
            os.environ["TASKBUS_SUPERVISOR"] = self._saved

    def test_default_is_off(self):
        self.assertFalse(self.job._supervisor_enabled())

    def test_empty_is_off(self):
        os.environ["TASKBUS_SUPERVISOR"] = ""
        self.assertFalse(self.job._supervisor_enabled())

    def test_zero_is_off(self):
        os.environ["TASKBUS_SUPERVISOR"] = "0"
        self.assertFalse(self.job._supervisor_enabled())

    def test_false_is_off(self):
        os.environ["TASKBUS_SUPERVISOR"] = "false"
        self.assertFalse(self.job._supervisor_enabled())

    def test_one_is_on(self):
        os.environ["TASKBUS_SUPERVISOR"] = "1"
        self.assertTrue(self.job._supervisor_enabled())

    def test_true_is_on(self):
        os.environ["TASKBUS_SUPERVISOR"] = "TRUE"
        self.assertTrue(self.job._supervisor_enabled())

    def test_dispatch_is_gated_inside_main(self):
        source = open(JOB, encoding="utf-8").read()
        # The supervisor call must sit behind the flag check, never before it.
        gate = source.index("if _supervisor_enabled():")
        window = source[gate:gate + 300]
        self.assertIn("_supervised_main(pf, cwd, task_path", window)
        self.assertIn("return supervised", window)
        # And the legacy loop must still be present for the flag-off path.
        self.assertIn("for attempt in range(1, MAX_PASSES + 1):", source)

    def test_allowed_paths_includes_workdir(self):
        paths = self.job._supervisor_allowed_paths(HERE)
        self.assertIn(HERE, paths)

    def test_criterion_spec_prefers_env(self):
        os.environ["TASKBUS_CRITERION"] = "exit_zero"
        try:
            self.assertEqual(self.job._supervisor_criterion_spec("irrelevant"), "exit_zero")
        finally:
            os.environ.pop("TASKBUS_CRITERION", None)


class HeartbeatApiKeyTests(unittest.TestCase):
    """The runner resolves its own heartbeat key: environment first, then Vault.

    The poller no longer exports TASKBUS_API_KEY (it would be rendered into a
    remote ssh command line), so this resolver is what keeps the heartbeat alive
    on a dispatched job. vault_api_key() is replaced with a counter so the tests
    stay hermetic - no Vault, no network.
    """

    @classmethod
    def setUpClass(cls):
        cls.job = load_job()

    def setUp(self):
        self._saved = os.environ.pop("TASKBUS_API_KEY", None)
        self.job._reset_heartbeat_api_key_cache()
        self.calls = []

    def tearDown(self):
        os.environ.pop("TASKBUS_API_KEY", None)
        if self._saved is not None:
            os.environ["TASKBUS_API_KEY"] = self._saved
        self.job._reset_heartbeat_api_key_cache()

    def _stub_vault(self, key, err):
        def fake():
            self.calls.append(1)
            return key, err
        self.job.vault_api_key = fake

    def test_env_key_wins_and_vault_is_never_called(self):
        os.environ["TASKBUS_API_KEY"] = "env-key"
        self._stub_vault("vault-key", "vault")
        key, source = self.job._heartbeat_api_key()
        self.assertEqual(key, "env-key")
        self.assertEqual(source, "env")
        self.assertEqual(self.calls, [])

    def test_falls_back_to_vault_when_env_is_absent(self):
        self._stub_vault("vault-key", "vault")
        key, source = self.job._heartbeat_api_key()
        self.assertEqual(key, "vault-key")
        self.assertEqual(source, "vault")
        self.assertEqual(len(self.calls), 1)

    def test_vault_key_is_cached_for_the_life_of_the_process(self):
        self._stub_vault("vault-key", "vault")
        self.job._heartbeat_api_key()
        key, source = self.job._heartbeat_api_key()
        self.assertEqual(key, "vault-key")
        self.assertEqual(source, "vault-cached")
        self.assertEqual(len(self.calls), 1)

    def test_a_failed_fetch_is_not_cached(self):
        # A transient Vault 403 must not poison the rest of the job.
        self._stub_vault(None, "vault secret-read HTTP 403")
        key, source = self.job._heartbeat_api_key()
        self.assertEqual(key, "")
        self.assertIn("403", source)
        self.job._heartbeat_api_key()
        self.assertEqual(len(self.calls), 2)

    def test_a_blank_vault_key_is_a_failure_not_a_credential(self):
        self._stub_vault("   ", "vault")
        key, _source = self.job._heartbeat_api_key()
        self.assertEqual(key, "")

    def test_heartbeat_call_uses_the_resolver(self):
        # Guards the wiring: the closure must not read the env var directly, or the
        # Vault fallback would never run on a dispatched job.
        source = open(JOB, encoding="utf-8").read()
        start = source.index("def heartbeat_call():")
        body = source[start:start + 1200]
        self.assertIn("_heartbeat_api_key()", body)
        self.assertNotIn('os.environ.get("TASKBUS_API_KEY")', body)


if __name__ == "__main__":
    unittest.main(verbosity=2)
