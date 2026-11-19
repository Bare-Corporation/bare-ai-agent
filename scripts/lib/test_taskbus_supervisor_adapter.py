#!/usr/bin/env python3
"""test_taskbus_supervisor_adapter.py - the flag gate on the live runner.

The point of these tests is the DEFAULT: with TASKBUS_SUPERVISOR unset the runner
must behave exactly as before, and the supervisor path must not be reachable.
The job file has dashes in its name, so it is loaded by path.
"""
import contextlib
import importlib.util
import io
import os
import shutil
import sys
import tempfile
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


# A real task-bus prompt envelope, as the poller writes it. The canary crashed on
# exactly this input, so the tests below use the real text rather than a stub.
REAL_ENVELOPE = """TASK-BUS JOB - row 694 (dispatched by the task-bus poller)
SHORT DESCRIPTION: Stage 1 canary on the worker node
EXECUTION PROFILE: canary
"""


class SupervisorCriterionSeamTests(unittest.TestCase):
    """The seam between the PARSED checks and the supervisor's LABEL.

    Row 694 died at the criterion line because taskbus_ticket_meta() returns a
    (row_id, title) TUPLE and the code called .get() on it. One defect deeper,
    _supervised_main also handed that label STRING to _execute_criterion(), which
    wants the parsed list of kind/args dicts - silently, because that call sits in
    a bare except. Both are covered here against the real envelope.
    """

    @classmethod
    def setUpClass(cls):
        cls.job = load_job()

    def setUp(self):
        self._saved = {}
        for key in ("TASKBUS_CRITERION", "TASKBUS_ROW_ID", "TASKBUS_CLAIM_NONCE",
                    "TASKBUS_SUPERVISOR"):
            self._saved[key] = os.environ.pop(key, None)
        self.tmp = tempfile.mkdtemp(prefix="criterion-seam-")

    def tearDown(self):
        for key, value in self._saved.items():
            os.environ.pop(key, None)
            if value is not None:
                os.environ[key] = value
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ---- the envelope contract -------------------------------------------
    def test_ticket_meta_really_returns_a_two_tuple(self):
        meta = self.job.taskbus_ticket_meta(REAL_ENVELOPE)
        self.assertIsInstance(meta, tuple)
        self.assertEqual(len(meta), 2)
        self.assertEqual(meta[0], "694")
        # The point of the regression: it is NOT a mapping, so .get() cannot work.
        self.assertFalse(hasattr(meta, "get"))

    def test_criterion_spec_survives_the_ticket_envelope(self):
        # Regression: this raised AttributeError - tuple has no attribute get.
        self.assertEqual(self.job._supervisor_criterion_spec(REAL_ENVELOPE), "")

    # ---- label derivation -------------------------------------------------
    def test_criterion_spec_labels_the_parsed_checks(self):
        parsed = [{"kind": "file_exists", "args": ["/tmp/x"]},
                  {"kind": "http_status", "args": ["https://example.invalid", "200"]}]
        self.assertEqual(self.job._supervisor_criterion_spec(REAL_ENVELOPE, parsed),
                         "file_exists:/tmp/x ; http_status:https://example.invalid;200")

    def test_env_criterion_still_wins_over_the_parsed_spec(self):
        os.environ["TASKBUS_CRITERION"] = "exit_zero"
        parsed = [{"kind": "file_exists", "args": ["/tmp/x"]}]
        self.assertEqual(self.job._supervisor_criterion_spec(REAL_ENVELOPE, parsed),
                         "exit_zero")

    def test_label_ignores_unusable_entries(self):
        parsed = ["not-a-dict", {"args": ["x"]}, {"kind": "file_exists", "args": []}]
        self.assertEqual(self.job._criterion_label(parsed), "file_exists")

    def test_label_is_empty_without_checks(self):
        self.assertEqual(self.job._criterion_label(None), "")
        self.assertEqual(self.job._criterion_label([]), "")

    # ---- _supervised_main entry setup ------------------------------------
    def _fake_supervisor(self, captured):
        class FakeSupervisor:
            DEFAULT_MAX_EXECUTION_SECONDS = 600

            @staticmethod
            def reflect_to_role_diary(*args, **kwargs):
                return None

            @staticmethod
            def run_supervised_loop(**kwargs):
                captured.update(kwargs)
                return {"outcome": "budget_exhausted", "reason": "seam test",
                        "passes": 1, "state_path": kwargs.get("state_path")}

        return FakeSupervisor()

    def test_supervised_main_entry_setup_registers_the_parsed_criterion(self):
        task_path = os.path.join(self.tmp, "task.md")
        with open(task_path, "w", encoding="utf-8") as fh:
            fh.write(REAL_ENVELOPE)
        criterion = [{"kind": "file_exists", "args": ["/tmp/probe"]}]
        captured = {}
        original = self.job._supervisor_module
        self.job._supervisor_module = lambda: self._fake_supervisor(captured)
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                rc = self.job._supervised_main(None, self.tmp, task_path, REAL_ENVELOPE,
                                               dict(os.environ), 2, 60,
                                               criterion_spec=criterion)
        finally:
            self.job._supervisor_module = original
        # Reached the loop at all: the canary died before this line.
        self.assertEqual(captured.get("criteria"), ["file_exists:/tmp/probe"])
        self.assertEqual(rc, 1)          # not verified, so not a success code

    def test_supervised_main_entry_setup_without_a_criterion_registers_none(self):
        task_path = os.path.join(self.tmp, "task.md")
        with open(task_path, "w", encoding="utf-8") as fh:
            fh.write(REAL_ENVELOPE)
        captured = {}
        original = self.job._supervisor_module
        self.job._supervisor_module = lambda: self._fake_supervisor(captured)
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                self.job._supervised_main(None, self.tmp, task_path, REAL_ENVELOPE,
                                          dict(os.environ), 1, 30)
        finally:
            self.job._supervisor_module = original
        self.assertEqual(captured.get("criteria"), [])

    # ---- the executor takes the parsed list, never the label -------------
    def test_execute_criterion_accepts_the_parsed_list(self):
        probe = os.path.join(self.tmp, "present.txt")
        open(probe, "w").close()
        ok, report = self.job._execute_criterion([{"kind": "file_exists", "args": [probe]}])
        self.assertTrue(ok)
        self.assertIn("PASS", report[0])

    def test_execute_criterion_rejects_the_label_form(self):
        # Negative control for the old call shape: a label STRING raises instead of
        # quietly reporting "no checks executed".
        with self.assertRaises(AttributeError):
            self.job._execute_criterion("file_exists:/tmp/x")


class SupervisedChildEnvTests(unittest.TestCase):
    """What the supervised CLI child actually receives.

    _supervised_main is given the env main() resolved (profile, then
    model-credentials.env, then Vault, then the catalogue route). These tests
    capture the env handed to subprocess.Popen, so "the credentials reach the
    child" is measured rather than assumed.
    """

    @classmethod
    def setUpClass(cls):
        cls.job = load_job()

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="child-env-")
        self.task_path = os.path.join(self.tmp, "task.md")
        with open(self.task_path, "w", encoding="utf-8") as fh:
            fh.write(REAL_ENVELOPE)
        self.captured = {}
        self._saved = {}
        for key in ("TASKBUS_CRITERION", "TASKBUS_ROW_ID", "TASKBUS_CLAIM_NONCE"):
            self._saved[key] = os.environ.pop(key, None)

    def tearDown(self):
        for key, value in self._saved.items():
            os.environ.pop(key, None)
            if value is not None:
                os.environ[key] = value
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _fake_proc(self):
        class FakeProc:
            returncode = 0

            def poll(self):
                return 0

            def wait(self, timeout=None):
                return 0

        return FakeProc()

    def _drive(self, env, criterion_spec=None):
        captured = self.captured

        def popen(cmd, env=None, **kwargs):
            captured["cmd"] = cmd
            captured["env"] = dict(env or {})
            return self._fake_proc()

        job = self.job
        real_popen = job.subprocess.Popen
        job.subprocess.Popen = popen

        class FakeSupervisor:
            DEFAULT_MAX_EXECUTION_SECONDS = 600

            @staticmethod
            def reflect_to_role_diary(*a, **k):
                return None

            @staticmethod
            def drain_streams(proc):
                return b"", b""

            @staticmethod
            def terminate_process_group(proc):
                return None

            @staticmethod
            def run_supervised_loop(**kwargs):
                captured["run_cli"] = kwargs["run_cli"]
                captured["criteria"] = kwargs.get("criteria")
                kwargs["run_cli"]("prompt", 1)
                return {"outcome": "budget_exhausted", "reason": "seam test",
                        "passes": 1, "state_path": kwargs.get("state_path")}

        real_module = job._supervisor_module
        job._supervisor_module = lambda: FakeSupervisor()
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                rc = job._supervised_main(None, self.tmp, self.task_path, REAL_ENVELOPE,
                                          env, 1, 30, criterion_spec=criterion_spec)
        finally:
            job.subprocess.Popen = real_popen
            job._supervisor_module = real_module
        return rc

    def test_resolved_credentials_reach_the_cli_child(self):
        env = {
            "BARE_AI_API_KEY": "test-key-not-a-secret",
            "BARE_AI_ENDPOINT": "https://example.invalid/v1",
            "BARE_AI_MODEL": "test-model",
            "PATH": os.environ.get("PATH", ""),
        }
        self._drive(env)
        child = self.captured.get("env") or {}
        self.assertEqual(child.get("BARE_AI_API_KEY"), "test-key-not-a-secret")
        self.assertEqual(child.get("BARE_AI_ENDPOINT"), "https://example.invalid/v1")
        self.assertEqual(child.get("BARE_AI_MODEL"), "test-model")
        # And the child command is the json-mode invocation the supervisor needs.
        self.assertIn("-o", self.captured["cmd"])
        self.assertIn("json", self.captured["cmd"])

    def test_endpoint_only_child_is_allowed(self):
        # A local engine needs no key: the CLI's own contract accepts an endpoint.
        env = {"BARE_AI_ENDPOINT": "http://127.0.0.1:8000/v1", "PATH": ""}
        self._drive(env)
        child = self.captured.get("env") or {}
        self.assertEqual(child.get("BARE_AI_ENDPOINT"), "http://127.0.0.1:8000/v1")
        self.assertNotIn("BARE_AI_API_KEY", child)

    def test_refuses_when_the_child_env_carries_no_credentials(self):
        rc = self._drive({"PATH": os.environ.get("PATH", "")})
        self.assertEqual(rc, 75)                      # main()'s TEMPFAIL convention
        self.assertNotIn("env", self.captured)        # the CLI was never spawned


if __name__ == "__main__":
    unittest.main(verbosity=2)
