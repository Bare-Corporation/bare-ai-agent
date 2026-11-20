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
import signal
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import taskbus_supervisor as sup


def group_alive(pgid):
    """True while any process is still in process group pgid."""
    try:
        os.killpg(pgid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def group_members(pgid):
    """PIDs whose process group is pgid, read from /proc. Empty on any problem."""
    found = []
    try:
        names = os.listdir("/proc")
    except OSError:
        return found
    for name in names:
        if not name.isdigit():
            continue
        try:
            with open("/proc/" + name + "/stat", "r") as fh:
                data = fh.read()
        except OSError:
            continue
        try:
            fields = data.rsplit(")", 1)[1].split()
            pgrp = int(fields[2])
        except (IndexError, ValueError):
            continue
        if pgrp == pgid:
            found.append(int(name))
    return found


def wait_for_no_members(pgid, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not group_members(pgid):
            return True
        time.sleep(0.05)
    return False


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

    def test_a_watch_interrupts_a_long_child(self):
        # Row 702: a lost claim must kill the child MID-PASS. Without a watch the
        # drain waits for the child to exit, which is how a 1000 s runner kept
        # running beside the retry.
        code = ("import sys, time; sys.stdout.write('early'); sys.stdout.flush(); "
                "time.sleep(5.0); sys.stdout.write('late'); sys.stdout.flush()")
        # start_new_session=True is REQUIRED here, not cosmetic: the test calls
        # terminate_process_group, which killpg()s this child's process group. A
        # child in the runner's own group would signal the test runner itself.
        proc = subprocess.Popen([sys.executable, "-u", "-c", code],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                stdin=subprocess.DEVNULL, start_new_session=True)
        started = time.monotonic()
        out, _err = sup.drain_streams(proc, watch=lambda: True, poll_interval=0.05)
        elapsed = time.monotonic() - started
        sup.terminate_process_group(proc, grace=0.5, kill_wait=2.0)
        self.assertLess(elapsed, 2.0)
        self.assertNotIn(b"late", out)

    def test_without_a_watch_the_child_is_drained_to_the_end(self):
        # CONTROL for the case above: the fast return there is the WATCH, not the
        # pumps. Here the same shape of child runs to completion and its final
        # write is captured.
        code = "import sys, time; time.sleep(1.0); sys.stdout.write('done')"
        proc = subprocess.Popen([sys.executable, "-u", "-c", code],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                stdin=subprocess.DEVNULL)
        started = time.monotonic()
        out, _err = sup.drain_streams(proc)
        elapsed = time.monotonic() - started
        proc.wait(timeout=30)
        self.assertIn(b"done", out)
        self.assertGreaterEqual(elapsed, 0.8)


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

    def test_a_fatal_status_is_lost_on_the_first_beat(self):
        # 409 means the nonce or the ownership is gone: retrying cannot help, so
        # the run is declared lost without waiting for max_failures.
        calls = []

        def refuse():
            calls.append(1)
            raise sup.HeartbeatLost("heartbeat HTTP 409 stale nonce", status=409)

        hb = sup.Heartbeat(0.02, refuse, max_failures=5).start()
        deadline = time.time() + 3.0
        while hb.lost_reason is None and time.time() < deadline:
            time.sleep(0.02)
        hb.stop()
        self.assertIsNotNone(hb.lost_reason)
        self.assertIn("409", hb.lost_reason)
        self.assertEqual(len(calls), 1)

    def test_a_transient_status_retries_until_max_failures(self):
        # CONTROL for the case above: the SAME exception class with a non-fatal
        # status is retried, and is only declared lost on the third strike.
        calls = []

        def refuse():
            calls.append(1)
            raise sup.HeartbeatLost("heartbeat HTTP 503 unavailable", status=503)

        hb = sup.Heartbeat(0.02, refuse, max_failures=3).start()
        deadline = time.time() + 3.0
        while hb.lost_reason is None and time.time() < deadline:
            time.sleep(0.02)
        hb.stop()
        self.assertIsNotNone(hb.lost_reason)
        self.assertEqual(len(calls), 3)

    def test_a_transport_error_retries_until_max_failures(self):
        # An error with no HTTP status at all is transient by construction.
        calls = []

        def refuse():
            calls.append(1)
            raise sup.HeartbeatLost("heartbeat transport error: timed out")

        hb = sup.Heartbeat(0.02, refuse, max_failures=2).start()
        deadline = time.time() + 3.0
        while hb.lost_reason is None and time.time() < deadline:
            time.sleep(0.02)
        hb.stop()
        self.assertIsNotNone(hb.lost_reason)
        self.assertEqual(len(calls), 2)

    def test_fatal_statuses_are_403_409_and_410(self):
        for status in (403, 409, 410):
            self.assertTrue(sup.HeartbeatLost("x", status=status).fatal)
        for status in (401, 500, 503, None):
            self.assertFalse(sup.HeartbeatLost("x", status=status).fatal)

    def test_on_lost_fires_exactly_once_with_the_reason(self):
        seen = []

        def refuse():
            raise sup.HeartbeatLost("heartbeat HTTP 410 gone", status=410)

        hb = sup.Heartbeat(0.02, refuse, max_failures=5,
                           on_lost=lambda reason: seen.append(reason)).start()
        deadline = time.time() + 3.0
        while hb.lost_reason is None and time.time() < deadline:
            time.sleep(0.02)
        hb.stop()
        self.assertEqual(len(seen), 1)
        self.assertIn("410", seen[0])

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

    def test_kills_a_group_whose_leader_already_exited(self):
        # ROW 711: the leader exits first and its group lives on. The old code
        # returned "already-exited" on proc.poll() and never signalled the group,
        # which is how a sleep survived a claim-loss kill.
        code = ("import os, sys, time" + chr(10) +
                "pid = os.fork()" + chr(10) +
                "if pid == 0:" + chr(10) +
                "    time.sleep(120)" + chr(10) +
                "    os._exit(0)" + chr(10) +
                "sys.exit(0)" + chr(10))
        proc = subprocess.Popen([sys.executable, "-u", "-c", code],
                                stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL,
                                start_new_session=True)
        pgid = os.getpgid(proc.pid)
        try:
            deadline = time.time() + 10
            while proc.poll() is None and time.time() < deadline:
                time.sleep(0.05)
            self.assertIsNotNone(proc.poll(), "the leader should have exited")
            self.assertTrue(group_alive(pgid), "the group should still be alive")

            outcome = sup.terminate_process_group(proc, grace=2.0, kill_wait=2.0)
            self.assertIn(outcome, ("sigterm", "sigkill"))
            self.assertTrue(wait_for_no_members(pgid),
                            "the group must be empty after the kill")
        finally:
            try:
                os.killpg(pgid, signal.SIGKILL)
            except OSError:
                pass

    def test_a_lost_claim_kills_the_child_and_its_grandchild(self):
        # End to end for the row-710 shape: a 409 arrives mid-drain, the watch
        # stops the drain, and the kill must take the whole group - the leader AND
        # the grandchild it forked - not just the leader's pid.
        code = ("import os, sys, time" + chr(10) +
                "pid = os.fork()" + chr(10) +
                "if pid == 0:" + chr(10) +
                "    time.sleep(120)" + chr(10) +
                "    os._exit(0)" + chr(10) +
                "time.sleep(120)" + chr(10))
        proc = subprocess.Popen([sys.executable, "-u", "-c", code],
                                stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE,
                                start_new_session=True)
        pgid = os.getpgid(proc.pid)
        lost = []

        def refuse():
            raise sup.HeartbeatLost("heartbeat HTTP 409 stale nonce", status=409)

        hb = sup.Heartbeat(0.02, refuse, max_failures=5,
                           on_lost=lambda reason: lost.append(reason)).start()
        try:
            sup.drain_streams(proc, watch=lambda: bool(lost), poll_interval=0.02)
            hb.stop()
            self.assertEqual(len(lost), 1, "the 409 should have fired once")
            self.assertIn("409", lost[0])
            self.assertTrue(group_alive(pgid),
                            "the child group should still be running before the kill")

            outcome = sup.terminate_process_group(proc, grace=2.0, kill_wait=2.0)
            self.assertIn(outcome, ("sigterm", "sigkill"))
            self.assertTrue(wait_for_no_members(pgid),
                            "neither the child nor the grandchild may survive")
            self.assertIsNotNone(proc.wait(timeout=5), "the leader must be reaped")
        finally:
            hb.stop()
            try:
                os.killpg(pgid, signal.SIGKILL)
            except OSError:
                pass

    def test_a_child_in_our_own_group_is_never_killed_by_group(self):
        # If the child shares OUR process group, killpg would take the runner and
        # every sibling job with it. The helper must fall back to pid-only.
        proc = subprocess.Popen(
            [sys.executable, "-u", "-c", "import time; time.sleep(60)"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL)
        try:
            time.sleep(0.1)
            self.assertEqual(os.getpgid(proc.pid), os.getpgid(0),
                             "precondition: the child shares our group")
            outcome = sup.terminate_process_group(proc, grace=1.0, kill_wait=2.0)
            self.assertIn(outcome, ("terminate", "kill"))
            self.assertIsNotNone(proc.wait(timeout=5))
        finally:
            if proc.poll() is None:
                proc.kill()
        # Getting here at all proves the runner itself was not signalled.


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


class CliStreamTests(unittest.TestCase):
    """The CLI writes its -o json envelope to STDERR, not STDOUT.

    Measured on this node 2026-11-19 with the installed bundle: stdout carried
    one tool-policy line (126 bytes, no JSON) and stderr carried the YOLO banners
    followed by the envelope. A supervisor that drains only stdout reports
    "no parseable JSON" on every real run.
    """

    STDOUT_REAL = "Tool policy locked by the caller for this execution: tools enabled.\n"
    STDERR_PREAMBLE = ("YOLO mode is enabled. All tool calls will be automatically approved.\n"
                       "YOLO mode is enabled. All tool calls will be automatically approved.\n")

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="supstream-")
        self.state_path = os.path.join(self.dir, ".taskbus-state-8.json")
        self.logs = []

    def tearDown(self):
        for name in os.listdir(self.dir):
            try:
                os.unlink(os.path.join(self.dir, name))
            except OSError:
                pass
        os.rmdir(self.dir)

    def _run(self, stdout, stderr, criteria=None):
        def run_cli(prompt, pass_no):
            return {"exit_code": 0, "stdout": stdout, "stderr": stderr, "evidence": []}

        return sup.run_supervised_loop(
            8, "task", self.state_path, run_cli=run_cli,
            criteria=criteria or [], max_passes=1, log=self.logs.append)

    def test_envelope_is_read_from_stderr(self):
        result = self._run(self.STDOUT_REAL,
                           self.STDERR_PREAMBLE + json.dumps(
                               {"session_id": "sess-from-stderr", "response": "work done"}))
        recorded = result["state"]["passes"][0]
        self.assertEqual(recorded["session_id"], "sess-from-stderr")
        self.assertIn("work done", recorded["response_excerpt"])
        self.assertTrue(any("envelope read from stderr" in line for line in self.logs))

    def test_envelope_on_stdout_still_works(self):
        # Control: a CLI that writes the envelope to stdout must keep working, and
        # must NOT be reported as a stderr read.
        result = self._run(self.STDOUT_REAL + json.dumps(
            {"session_id": "sess-from-stdout", "response": "stdout answer"}), "")
        recorded = result["state"]["passes"][0]
        self.assertEqual(recorded["session_id"], "sess-from-stdout")
        self.assertFalse(any("envelope read from stderr" in line for line in self.logs))

    def test_stdout_wins_when_both_carry_an_envelope(self):
        result = self._run(json.dumps({"session_id": "sess-stdout", "response": "a"}),
                           json.dumps({"session_id": "sess-stderr", "response": "b"}))
        self.assertEqual(result["state"]["passes"][0]["session_id"], "sess-stdout")

    def test_neither_stream_reports_both(self):
        self._run("just a notice\n", "just a banner\n")
        joined = " | ".join(self.logs)
        self.assertIn("no parseable JSON", joined)
        # Both streams are named, so the ticket shows what each carried.
        self.assertIn("stdout", joined)
        self.assertIn("stderr", joined)


class CliErrorEnvelopeTests(unittest.TestCase):
    """A failed turn carries an error object and no response.

    The .16 canary recorded a session_id, exit 1 and an EMPTY response_excerpt,
    because only envelope["response"] was read. The error is the only record of
    why the turn failed, so it belongs in the pass record.
    """

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="superr-")
        self.state_path = os.path.join(self.dir, ".taskbus-state-9.json")
        self.logs = []

    def tearDown(self):
        for name in os.listdir(self.dir):
            try:
                os.unlink(os.path.join(self.dir, name))
            except OSError:
                pass
        os.rmdir(self.dir)

    def _run(self, envelope_obj):
        def run_cli(prompt, pass_no):
            return {"exit_code": 1, "stdout": "", "stderr": json.dumps(envelope_obj),
                    "evidence": []}

        return sup.run_supervised_loop(9, "task", self.state_path, run_cli=run_cli,
                                       criteria=[], max_passes=1, log=self.logs.append)

    def test_error_envelope_becomes_the_excerpt(self):
        result = self._run({"session_id": "sid-err",
                            "error": {"type": "TypeError",
                                      "message": "fetch failed", "code": 1}})
        excerpt = result["state"]["passes"][0]["response_excerpt"]
        self.assertIn("TypeError", excerpt)
        self.assertIn("fetch failed", excerpt)
        self.assertIn("(code 1)", excerpt)
        # And it is in the pass log, not only in the state file.
        self.assertTrue(any("CLI error" in line for line in self.logs))

    def test_a_real_response_still_wins(self):
        result = self._run({"session_id": "s", "response": "done",
                            "error": {"message": "a warning"}})
        self.assertIn("done", result["state"]["passes"][0]["response_excerpt"])

    def test_string_error_is_surfaced(self):
        result = self._run({"session_id": "s", "error": "quota exhausted"})
        self.assertIn("quota exhausted", result["state"]["passes"][0]["response_excerpt"])

    def test_format_cli_error_is_total(self):
        # Runs on the failure path: it must never raise, whatever the shape.
        self.assertEqual(sup.format_cli_error(None), "")
        self.assertEqual(sup.format_cli_error({}), "")
        self.assertIn("boom", sup.format_cli_error({"message": "boom"}))
        self.assertIn("weird", sup.format_cli_error("weird"))
        self.assertIn("42", sup.format_cli_error(42))


if __name__ == "__main__":
    unittest.main(verbosity=2)


class SupervisedLoopTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="suploop-")
        self.state_path = os.path.join(self.dir, ".taskbus-state-7.json")
        self.logs = []

    def tearDown(self):
        for name in os.listdir(self.dir):
            try:
                os.unlink(os.path.join(self.dir, name))
            except OSError:
                pass
        os.rmdir(self.dir)

    def _cli(self, evidence_by_pass, response="working", session="sid", failed=False):
        def run_cli(prompt, pass_no):
            self.assertIn("SUPERVISOR STATE", prompt)
            return {
                "exit_code": 1 if failed else 0,
                "stdout": "preamble line\n" + json.dumps({"session_id": session, "response": response}),
                "stderr": "",
                "evidence": evidence_by_pass.get(pass_no, []),
            }
        return run_cli

    def test_verified_when_evidence_complete(self):
        result = sup.run_supervised_loop(
            7, "do the thing", self.state_path,
            run_cli=self._cli({1: [{"criterion": "build", "evidence": "npm test green"}]}),
            criteria=["build"], log=self.logs.append)
        self.assertEqual(result["outcome"], "verified")
        self.assertEqual(result["passes"], 1)
        self.assertTrue(sup.StateFile.all_criteria_evidenced(result["state"]))

    def _run_with_a_mid_pass_loss(self, evidence, max_passes=3):
        """Drive one pass that outlives the 409, so the loss lands MID-pass.

        run_cli waits for the loss instead of racing it, which is what makes the
        assertion deterministic: the loss is guaranteed to happen inside pass 1.
        """
        lost = []
        passes = []

        def run_cli(prompt, pass_no):
            passes.append(pass_no)
            deadline = time.time() + 3.0
            while time.time() < deadline and not lost:
                time.sleep(0.01)
            return {
                "exit_code": 0,
                "stdout": json.dumps({"session_id": "sid", "response": "partial"}),
                "stderr": "",
                "evidence": evidence,
            }

        def refuse():
            raise sup.HeartbeatLost("heartbeat HTTP 409 stale nonce", status=409)

        result = sup.run_supervised_loop(
            7, "task", self.state_path, run_cli=run_cli,
            heartbeat_call=refuse, heartbeat_interval=0.01,
            on_lost=lambda reason: lost.append(reason),
            criteria=["build"], max_passes=max_passes, log=self.logs.append)
        return result, passes, lost

    def test_a_mid_pass_loss_ends_the_run_as_lost(self):
        # ROW 711: the claim dies WHILE pass 1 runs. The run must stop as "lost" -
        # not proceed to another pass, and not report a completion.
        result, passes, lost = self._run_with_a_mid_pass_loss(evidence=[])
        self.assertEqual(result["outcome"], "lost")
        self.assertEqual(passes, [1], "no further pass may run after the loss")
        self.assertEqual(len(lost), 1)
        self.assertIn("lost", result["reason"])

    def test_a_mid_pass_loss_never_reports_verified(self):
        # Even with every criterion evidenced, a run whose claim died mid-pass must
        # not claim success: that is a completion outcome for work we no longer own.
        result, passes, lost = self._run_with_a_mid_pass_loss(
            evidence=[{"criterion": "build", "evidence": "green"}])
        self.assertEqual(result["outcome"], "lost")
        self.assertNotEqual(result["outcome"], "verified")
        self.assertEqual(passes, [1])
        self.assertEqual(len(lost), 1)

    def test_second_pass_completes_remaining_criteria(self):
        evidence = {2: [{"criterion": "docs", "evidence": "README updated"}]}
        result = sup.run_supervised_loop(
            7, "task", self.state_path, run_cli=self._cli(evidence),
            criteria=["docs"], log=self.logs.append)
        self.assertEqual(result["outcome"], "verified")
        self.assertEqual(result["passes"], 2)

    def test_budget_exhausted_when_evidence_never_arrives(self):
        result = sup.run_supervised_loop(
            7, "task", self.state_path, run_cli=self._cli({}),
            criteria=["build"], max_passes=2, log=self.logs.append)
        self.assertEqual(result["outcome"], "budget_exhausted")
        self.assertEqual(result["passes"], 2)
        self.assertIn("max_passes", result["reason"])

    def test_blocked_prefix_transitions_to_blocked(self):
        result = sup.run_supervised_loop(
            7, "task", self.state_path,
            run_cli=self._cli({}, response=sup.BLOCKED_PREFIX + " need production credentials"),
            criteria=["build"], log=self.logs.append)
        self.assertEqual(result["outcome"], "blocked")
        self.assertIn("need production credentials", result["reason"])

    def test_explicit_blocked_field(self):
        def run_cli(prompt, pass_no):
            return {"exit_code": 0, "stdout": "", "blocked": "repo is dirty"}
        result = sup.run_supervised_loop(7, "task", self.state_path, run_cli=run_cli,
                                         criteria=["build"], log=self.logs.append)
        self.assertEqual(result["outcome"], "blocked")
        self.assertIn("repo is dirty", result["reason"])

    def test_depth_refused_before_any_work(self):
        called = []
        result = sup.run_supervised_loop(
            7, "task", self.state_path,
            run_cli=lambda p, n: called.append(n) or {},
            criteria=["build"], spawn_depth=3, log=self.logs.append)
        self.assertEqual(result["outcome"], "depth_refused")
        self.assertEqual(called, [])
        self.assertFalse(os.path.exists(self.state_path))

    def test_lost_heartbeat_stops_the_run(self):
        def refuse():
            raise RuntimeError("409 nonce refused")

        def slow_cli(prompt, pass_no):
            # A real pass takes minutes; emulate enough duration for the
            # heartbeat to accrue its bounded failures.
            time.sleep(0.02)
            return {"exit_code": 0, "stdout": ""}

        result = sup.run_supervised_loop(
            7, "task", self.state_path, run_cli=slow_cli,
            criteria=["build"], heartbeat_call=refuse, heartbeat_interval=0.02,
            max_passes=50, log=self.logs.append)
        self.assertEqual(result["outcome"], "lost")
        self.assertIn("heartbeat", result["reason"])

    def test_path_violation_blocks(self):
        def run_cli(prompt, pass_no):
            return {"exit_code": 0, "stdout": "", "changed_paths": ["/etc/passwd"]}
        result = sup.run_supervised_loop(
            7, "task", self.state_path, run_cli=run_cli,
            allowed_paths=[self.dir], criteria=["build"], log=self.logs.append)
        self.assertEqual(result["outcome"], "blocked")
        self.assertIn("path violation", result["reason"])

    def test_protected_path_blocks_even_when_allowed(self):
        protected = os.path.join(self.dir, ".bare-ai", "config", "vault.env")

        def run_cli(prompt, pass_no):
            return {"exit_code": 0, "stdout": "", "changed_paths": [protected]}
        result = sup.run_supervised_loop(
            7, "task", self.state_path, run_cli=run_cli,
            allowed_paths=[self.dir], criteria=["build"], log=self.logs.append)
        self.assertEqual(result["outcome"], "blocked")
        self.assertIn("protected credential path", result["reason"])

    def test_unparseable_output_does_not_crash(self):
        def run_cli(prompt, pass_no):
            return {"exit_code": 0, "stdout": "no json here at all"}
        result = sup.run_supervised_loop(7, "task", self.state_path, run_cli=run_cli,
                                         criteria=["build"], max_passes=1, log=self.logs.append)
        self.assertEqual(result["outcome"], "budget_exhausted")
        self.assertEqual(len(result["state"]["passes"]), 1)

    def test_cli_exception_recorded_as_failed_pass(self):
        def boom(prompt, pass_no):
            raise RuntimeError("cli missing")
        result = sup.run_supervised_loop(7, "task", self.state_path, run_cli=boom,
                                         criteria=["build"], max_passes=1, log=self.logs.append)
        self.assertEqual(result["outcome"], "budget_exhausted")
        self.assertEqual(result["state"]["passes"][0]["exit_code"], -1)

    def test_reflection_only_on_verified(self):
        seen = []
        sup.run_supervised_loop(
            7, "task", self.state_path, run_cli=self._cli({}),
            criteria=["build"], max_passes=1,
            reflect=lambda text: seen.append(text), log=self.logs.append)
        self.assertEqual(seen, [])
        sup.run_supervised_loop(
            7, "task", self.state_path,
            run_cli=self._cli({1: [{"criterion": "build", "evidence": "green"}]}),
            criteria=["build"], reflect=lambda text: seen.append(text), log=self.logs.append)
        self.assertEqual(len(seen), 1)
        self.assertIn("build", seen[0])

    def test_reflection_failure_does_not_invalidate_verified_run(self):
        def bad_reflect(_text):
            raise RuntimeError("role.md unwritable")
        result = sup.run_supervised_loop(
            7, "task", self.state_path,
            run_cli=self._cli({1: [{"criterion": "build", "evidence": "green"}]}),
            criteria=["build"], reflect=bad_reflect, log=self.logs.append)
        self.assertEqual(result["outcome"], "verified")

    def test_execution_budget_clamped_to_horizon(self):
        ticks = iter([0, 0, 10 ** 6])
        result = sup.run_supervised_loop(
            7, "task", self.state_path, run_cli=self._cli({}),
            criteria=["build"], max_passes=5, max_execution_seconds=999999,
            clock=lambda: next(ticks, 10 ** 6), log=self.logs.append)
        self.assertEqual(result["outcome"], "budget_exhausted")
        self.assertIn("max_execution_seconds", result["reason"])
