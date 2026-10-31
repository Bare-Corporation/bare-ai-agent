#!/usr/bin/env python3
"""Unit tests for the taskbus poller's scheduling gate (ticket 76).

Stdlib only, no network, no bus: the gate is exercised through the PURE
entry point schedule_verdict_at(), which is the single rule the poller uses
for both the cycle snapshot and the fresh re-read.

Run: python3 scripts/lib/test_taskbus_poller.py -v
"""

import datetime
import importlib.util
import pathlib
import unittest

HERE = pathlib.Path(__file__).resolve().parent
POLLER_PATH = HERE / "taskbus-poller.py"
UTC = datetime.timezone.utc


def load_poller():
    """Import taskbus-poller.py, whose filename is not a valid module name."""
    spec = importlib.util.spec_from_file_location("taskbus_poller_under_test", POLLER_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ScheduleVerdictTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.m = load_poller()
        # 14:30 UTC is OUTSIDE GLOBAL_AVOID_WINDOWS (00:00-14:00), so the peak
        # gate does not interfere with the start_task_at assertions.
        cls.offpeak = datetime.datetime(2026, 10, 31, 14, 30, tzinfo=UTC)
        cls.peak = datetime.datetime(2026, 10, 31, 6, 0, tzinfo=UTC)

    def verdict(self, row, now=None, policy="avoid_peak"):
        return self.m.schedule_verdict_at(
            row, now or self.offpeak, policy, self.m.GLOBAL_AVOID_WINDOWS)

    def test_released_row_is_immediately_claimable(self):
        """ticket 76: a release clears start_task_at, so it must not suppress.

        The poller scans rows once per cycle. If the claim on a row is released
        after that scan, the snapshot still holds the pre-release stamp while the
        gateway has already cleared it. Evaluating the gate on the snapshot would
        suppress a row that is claimable right now.
        """
        snapshot = {"id": 3, "start_task_at": "2026-12-25T00:00:00Z"}
        fresh = {"id": 3, "start_task_at": None}

        stale_ok, stale_kind, _ = self.verdict(snapshot)
        fresh_ok, fresh_kind, _ = self.verdict(fresh)

        # The control: the stale copy really would have suppressed the row.
        self.assertFalse(stale_ok)
        self.assertEqual(stale_kind, "not-yet-due")

        # The requirement: the released row is claimable at once.
        self.assertTrue(fresh_ok, "a released row must be immediately claimable")
        self.assertEqual(fresh_kind, "ok")

    def test_absent_or_unparseable_stamp_is_claimable(self):
        """No stamp, blank stamp or garbage stamp must never suppress."""
        for value in (None, "", "   ", "not-a-timestamp"):
            with self.subTest(start_task_at=value):
                ok, kind, _ = self.verdict({"id": 4, "start_task_at": value})
                self.assertTrue(ok)
                self.assertEqual(kind, "ok")

    def test_future_stamp_on_current_row_still_defers(self):
        """Control: the gate has not simply been disabled."""
        ok, kind, _ = self.verdict({"id": 5, "start_task_at": "2026-12-25T00:00:00Z"})
        self.assertFalse(ok)
        self.assertEqual(kind, "not-yet-due")

    def test_past_stamp_does_not_suppress(self):
        ok, kind, _ = self.verdict({"id": 6, "start_task_at": "2026-01-01T00:00:00Z"})
        self.assertTrue(ok)
        self.assertEqual(kind, "ok")

    def test_peak_window_defers_and_high_priority_bypasses(self):
        ok, kind, _ = self.verdict({"id": 7}, now=self.peak)
        self.assertFalse(ok)
        self.assertEqual(kind, "peak-defer")

        high = {"id": 8, "priority": {"id": 1, "value": "High"}}
        ok_high, kind_high, _ = self.verdict(high, now=self.peak)
        self.assertTrue(ok_high)
        self.assertEqual(kind_high, "ok")

    def test_policy_none_ignores_the_peak_window(self):
        ok, kind, _ = self.verdict({"id": 9}, now=self.peak, policy="none")
        self.assertTrue(ok)
        self.assertEqual(kind, "ok")


if __name__ == "__main__":
    unittest.main(verbosity=2)
