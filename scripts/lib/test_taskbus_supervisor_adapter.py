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


if __name__ == "__main__":
    unittest.main(verbosity=2)
