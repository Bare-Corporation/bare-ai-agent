#!/usr/bin/env python3
"""test_taskbus_supervisor.py - unit tests for the Stage 0 supervisor primitives.

Hermetic: no gateway, no database, no model, no network. The important cases are
the NEGATIVE ones - a preamble before the JSON must not be mistaken for failure,
a JSON-looking preamble must not be parsed, a cyclic parent chain must terminate,
and a protected credential path must be refused even when it sits inside
allowed_paths.
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import taskbus_supervisor as sup


class ParseCliJsonTests(unittest.TestCase):
    def test_preamble_then_json(self):
        stdout = "\n".join([
            "Tool policy locked by the caller for this execution: tools enabled",
            "YOLO mode is enabled. All tool calls will be automatically approved.",
            "",
            json.dumps({"session_id": "abc", "response": "OK", "stats": {}}, indent=2),
        ])
        parsed = sup.parse_cli_json(stdout)
        self.assertEqual(parsed["session_id"], "abc")
        self.assertEqual(parsed["response"], "OK")

    def test_no_json_raises(self):
        with self.assertRaises(sup.CliOutputError):
            sup.parse_cli_json("just noise\nand more noise\n")

    def test_empty_raises(self):
        with self.assertRaises(sup.CliOutputError):
            sup.parse_cli_json("")

    def test_malformed_json_raises(self):
        with self.assertRaises(sup.CliOutputError):
            sup.parse_cli_json("preamble\n{\n  \"broken\": \n")

    def test_non_string_raises(self):
        with self.assertRaises(sup.CliOutputError):
            sup.parse_cli_json(None)

    def test_brace_inside_preamble_is_not_json(self):
        # A preamble line containing a brace must not be treated as the object start.
        stdout = "note {not json}\n" + json.dumps({"response": "fine"})
        parsed = sup.parse_cli_json(stdout)
        self.assertEqual(parsed["response"], "fine")


class SpawnDepthGuardTests(unittest.TestCase):
    def test_root_allowed(self):
        self.assertEqual(sup.spawn_depth_guard(0), (True, ""))

    def test_below_limit_allowed(self):
        self.assertEqual(sup.spawn_depth_guard(2), (True, ""))

    def test_at_limit_refused(self):
        ok, reason = sup.spawn_depth_guard(3)
        self.assertFalse(ok)
        self.assertIn("limit", reason)

    def test_above_limit_refused(self):
        self.assertFalse(sup.spawn_depth_guard(9)[0])

    def test_negative_refused(self):
        self.assertFalse(sup.spawn_depth_guard(-1)[0])

    def test_non_integer_refused(self):
        self.assertFalse(sup.spawn_depth_guard("deep")[0])


class ParentChainDepthTests(unittest.TestCase):
    def _fetch(self, rows):
        return lambda rid: rows.get(int(rid))

    def test_no_parent_is_zero(self):
        depth, problem = sup.depth_from_parent_chain(10, self._fetch({10: {"parent_task": []}}))
        self.assertEqual(depth, 0)
        self.assertEqual(problem, "")

    def test_two_level_chain(self):
        rows = {1: {"parent_task": []}, 2: {"parent_task": [{"id": 1}]}, 3: {"parent_task": [2]}}
        depth, problem = sup.depth_from_parent_chain(3, self._fetch(rows))
        self.assertEqual(depth, 2)
        self.assertEqual(problem, "")

    def test_cycle_terminates(self):
        rows = {1: {"parent_task": [2]}, 2: {"parent_task": [1]}}
        depth, problem = sup.depth_from_parent_chain(1, self._fetch(rows))
        self.assertIn("cyclic", problem)
        self.assertLess(depth, 5)

    def test_multiple_parents_refused(self):
        rows = {5: {"parent_task": [1, 2]}}
        depth, problem = sup.depth_from_parent_chain(5, self._fetch(rows))
        self.assertIn("linear", problem)

    def test_missing_row_ends_chain(self):
        depth, problem = sup.depth_from_parent_chain(7, self._fetch({}))
        self.assertEqual(depth, 0)
        self.assertEqual(problem, "")

    def test_fetch_failure_reported(self):
        def boom(_rid):
            raise RuntimeError("gateway down")
        depth, problem = sup.depth_from_parent_chain(4, boom)
        self.assertIn("could not read", problem)


class StateFileTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="supstate-")
        self.path = os.path.join(self.dir, ".taskbus-state-99.json")
        self.state = sup.StateFile(self.path, row_id=99, spawn_depth=1, parent_row_id=42)

    def tearDown(self):
        for name in os.listdir(self.dir):
            try:
                os.unlink(os.path.join(self.dir, name))
            except OSError:
                pass
        os.rmdir(self.dir)

    def test_load_missing_returns_skeleton(self):
        state = self.state.load()
        self.assertEqual(state["row_id"], 99)
        self.assertEqual(state["spawn_depth"], 1)
        self.assertEqual(state["passes"], [])
        self.assertEqual(state["criteria"], [])

    def test_save_and_reload_roundtrip(self):
        state = self.state.load()
        sup.StateFile.register_criteria(state, ["exit_zero:build", "file_exists:/tmp/x"])
        sup.StateFile.record_pass(state, 1, 0, "sid-1", "did work", {"calls": 2})
        self.state.save(state)
        again = self.state.load()
        self.assertEqual(len(again["passes"]), 1)
        self.assertEqual(again["passes"][0]["session_id"], "sid-1")
        self.assertEqual(len(again["criteria"]), 2)

    def test_save_is_atomic_no_temp_left_behind(self):
        state = self.state.load()
        self.state.save(state)
        leftovers = [n for n in os.listdir(self.dir) if n.startswith(".taskbus-state.")]
        self.assertEqual(leftovers, [])

    def test_corrupt_file_raises(self):
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write("{not valid json")
        with self.assertRaises(sup.SupervisorError):
            self.state.load()

    def test_add_evidence_marks_evidenced(self):
        state = self.state.load()
        sup.StateFile.register_criteria(state, ["build passes"])
        sup.StateFile.add_evidence(state, "build passes", "ran npm test, 42 passed")
        item = state["criteria"][0]
        self.assertEqual(item["status"], "evidenced")
        self.assertEqual(len(item["evidence"]), 1)

    def test_add_evidence_unknown_criterion_creates_it(self):
        state = self.state.load()
        sup.StateFile.add_evidence(state, "new criterion", "proof")
        self.assertEqual(state["criteria"][0]["criterion"], "new criterion")
        self.assertEqual(state["criteria"][0]["status"], "evidenced")

    def test_add_evidence_rejects_empty(self):
        state = self.state.load()
        with self.assertRaises(sup.SupervisorError):
            sup.StateFile.add_evidence(state, "crit", "   ")
        with self.assertRaises(sup.SupervisorError):
            sup.StateFile.add_evidence(state, "", "proof")

    def test_all_criteria_evidenced_false_when_empty(self):
        self.assertFalse(sup.StateFile.all_criteria_evidenced(self.state.load()))

    def test_all_criteria_evidenced_false_when_pending(self):
        state = self.state.load()
        sup.StateFile.register_criteria(state, ["a", "b"])
        sup.StateFile.add_evidence(state, "a", "proof a")
        self.assertFalse(sup.StateFile.all_criteria_evidenced(state))

    def test_all_criteria_evidenced_true_when_complete(self):
        state = self.state.load()
        sup.StateFile.register_criteria(state, ["a", "b"])
        sup.StateFile.add_evidence(state, "a", "proof a")
        sup.StateFile.add_evidence(state, "b", "proof b")
        self.assertTrue(sup.StateFile.all_criteria_evidenced(state))

    def test_render_brief_lists_criteria_and_blocked(self):
        state = self.state.load()
        sup.StateFile.register_criteria(state, ["build passes"])
        sup.StateFile.add_evidence(state, "build passes", "ran it")
        state["blocked_reason"] = "needs a credential"
        brief = sup.StateFile.render_brief(state)
        self.assertIn("build passes", brief)
        self.assertIn("EVIDENCED", brief)
        self.assertIn("BLOCKED: needs a credential", brief)
        self.assertIn(sup.BLOCKED_PREFIX, brief)


class DrainStreamsTests(unittest.TestCase):
    def test_both_streams_drained_without_deadlock(self):
        # 200KB on each stream would deadlock a naive sequential PIPE reader.
        code = ("import sys\n"
                "sys.stdout.write('o' * 200000); sys.stdout.flush()\n"
                "sys.stderr.write('e' * 200000); sys.stderr.flush()\n")
        proc = subprocess.Popen([sys.executable, "-c", code],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                stdin=subprocess.DEVNULL)
        out, err = sup.drain_streams(proc)
        proc.wait(timeout=30)
        self.assertEqual(len(out), 200000)
        self.assertEqual(len(err), 200000)

    def test_missing_streams_tolerated(self):
        proc = subprocess.Popen([sys.executable, "-c", "print('hi')"],
                                stdout=subprocess.PIPE, stderr=None,
                                stdin=subprocess.DEVNULL)
        out, err = sup.drain_streams(proc)
        proc.wait(timeout=30)
        self.assertIn(b"hi", out)
        self.assertEqual(err, b"")


class HeartbeatTests(unittest.TestCase):
    def test_successful_beats(self):
        calls = []
        hb = sup.Heartbeat(0.05, lambda: calls.append(1)).start()
        time.sleep(0.3)
        hb.stop()
        self.assertGreaterEqual(len(calls), 2)
        self.assertIsNone(hb.lost_reason)
        self.assertFalse(hb.alive)

    def test_failures_mark_run_lost(self):
        def refuse():
            raise RuntimeError("403 ownership lost")
        hb = sup.Heartbeat(0.02, refuse, max_failures=2).start()
        deadline = time.time() + 2.0
        while hb.lost_reason is None and time.time() < deadline:
            time.sleep(0.02)
        self.assertIsNotNone(hb.lost_reason)
        self.assertIn("ownership lost", hb.lost_reason)
        self.assertFalse(hb.alive)
        hb.stop()

    def test_stop_before_start_is_safe(self):
        hb = sup.Heartbeat(0.05, lambda: None)
        hb.stop()
        self.assertFalse(hb.alive)


class TerminateProcessGroupTests(unittest.TestCase):
    def test_terminates_live_group(self):
        proc = subprocess.Popen(["sleep", "30"], start_new_session=True,
                                stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(0.05)
        outcome = sup.terminate_process_group(proc, grace=0.5, kill_wait=2.0)
        self.assertIn(outcome, ("sigterm", "sigkill"))
        self.assertIsNotNone(proc.poll())

    def test_already_exited_reported(self):
        proc = subprocess.Popen(["true"], start_new_session=True,
                                stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        proc.wait(timeout=10)
        self.assertEqual(sup.terminate_process_group(proc), "already-exited")

    def test_none_process_reported(self):
        self.assertEqual(sup.terminate_process_group(None), "no-process")


class AllowedPathTests(unittest.TestCase):
    def setUp(self):
        self.base = tempfile.mkdtemp(prefix="supwork-")

    def tearDown(self):
        try:
            os.rmdir(self.base)
        except OSError:
            pass

    def test_inside_allowed_is_clean(self):
        target = os.path.join(self.base, "src", "app.py")
        self.assertEqual(sup.enforce_allowed_paths([target], [self.base]), [])

    def test_outside_allowed_is_violation(self):
        violations = sup.enforce_allowed_paths(["/etc/passwd"], [self.base])
        self.assertEqual(len(violations), 1)
        self.assertIn("outside allowed_paths", violations[0]["reason"])

    def test_protected_path_refused_even_when_allowed(self):
        # Same directory tree as allowed_paths, but it is a credential file.
        protected = os.path.join(self.base, ".bare-ai", "config", "vault.env")
        violations = sup.enforce_allowed_paths([protected], [self.base])
        self.assertEqual(len(violations), 1)
        self.assertIn("protected credential path", violations[0]["reason"])

    def test_empty_inputs_are_clean(self):
        self.assertEqual(sup.enforce_allowed_paths([], [self.base]), [])
        self.assertEqual(sup.enforce_allowed_paths(["/tmp/anything"], []), [])


class ReflectionTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="supreflect-")
        self.role = os.path.join(self.dir, "role.md")
        self.diary = os.path.join(self.dir, "diary.md")

    def tearDown(self):
        for name in os.listdir(self.dir):
            path = os.path.join(self.dir, name)
            try:
                os.unlink(path)
            except OSError:
                pass
        try:
            os.rmdir(self.dir)
        except OSError:
            pass

    def test_appends_to_both_files(self):
        ok = sup.reflect_to_role_diary(self.role, self.diary, "measured fact: X")
        self.assertTrue(ok)
        for path in (self.role, self.diary):
            body = open(path, encoding="utf-8").read()
            self.assertIn("measured fact: X", body)
            self.assertIn("Learned Constraints", body)

    def test_preserves_existing_content(self):
        with open(self.role, "w", encoding="utf-8") as fh:
            fh.write("# existing role\n")
        sup.reflect_to_role_diary(self.role, self.diary, "new fact")
        body = open(self.role, encoding="utf-8").read()
        self.assertTrue(body.startswith("# existing role"))
        self.assertIn("new fact", body)

    def test_truncates_long_findings(self):
        sup.reflect_to_role_diary(self.role, self.diary, "z" * 900, max_chars=100)
        body = open(self.role, encoding="utf-8").read()
        self.assertIn("truncated at 100 chars", body)
        self.assertLess(len(body), 900)

    def test_empty_findings_not_written(self):
        self.assertFalse(sup.reflect_to_role_diary(self.role, self.diary, "   "))
        self.assertFalse(os.path.exists(self.role))

    def test_failure_returns_false_without_raising(self):
        # A regular file used as a directory component forces an OSError.
        blocker = os.path.join(self.dir, "blocker")
        with open(blocker, "w", encoding="utf-8") as fh:
            fh.write("x")
        ok = sup.reflect_to_role_diary(os.path.join(blocker, "sub", "role.md"), None, "fact")
        self.assertFalse(ok)


if __name__ == "__main__":
    unittest.main(verbosity=2)
