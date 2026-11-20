#!/usr/bin/env python3
"""Regression test for the file_changed verifier in bare-taskbus-reviewer.py.

Bug (measured 2026-11-20, row 720): file_changed was evaluated in the same branch as
file_md5 and only checked that the file EXISTED. The recorded BEFORE sha256 was never
compared, so a ticket whose file was byte-identical to its starting state graded
Passed/Completed. That released dependent row 721 against an API surface that did not exist.

Expected semantics for file_changed:/p=BEFORE
  PASS only when the current sha256 DIFFERS from BEFORE.
  FAIL when the file is missing (fail-closed).
  FAIL when BEFORE is absent or unparseable.
  FAIL when current sha256 equals BEFORE (zero net change).
"""
import importlib.util
import os
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
MOD = os.path.join(HERE, 'bare-taskbus-reviewer.py')

spec = importlib.util.spec_from_file_location('reviewer_under_test', MOD)
rev = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rev)

BEFORE = 'c80af8286bd022ae281b4500245a7fff8c22a151b4ee2c753ebee2f59f20283e'
AFTER = '110d679ccd8200ef' + '0' * 48
PATH = '/home/bare-ai/bare-council-api/taskbus_gateway.py'
# A neutral host: the probe is monkeypatched, and a public repo must not carry
# fleet addresses. Any host that is not this node's own works here.
HOST_ROW = {'claimed_by_machine': 'executor@review-target.invalid'}


def verdict_with(stat, criterion=None):
    """Grade a row whose remote probe returns the supplied stat dict."""
    rev.remote_stat = lambda host, path: dict(stat)
    row = dict(HOST_ROW)
    row['success_criterion'] = criterion or ('file_changed:%s=%s' % (PATH, BEFORE))
    return rev.verify(row)[0]


class TestFileChanged(unittest.TestCase):

    def test_unchanged_file_fails(self):
        """THE REGRESSION: after equals before must FAIL, not pass."""
        v = verdict_with({'exists': True, 'sha256': BEFORE, 'size': 158562})
        self.assertEqual(
            v, 'fail',
            'file_changed passed with after sha256 equal to before sha256; '
            'this is the row-720 false green')

    def test_changed_file_passes(self):
        v = verdict_with({'exists': True, 'sha256': AFTER, 'size': 999})
        self.assertEqual(v, 'pass', 'a genuine content change must pass')

    def test_missing_file_fails_closed(self):
        v = verdict_with({'exists': False})
        self.assertEqual(v, 'fail', 'file_changed must fail closed when the file is gone')

    def test_case_insensitive_hash_compare(self):
        v = verdict_with({'exists': True, 'sha256': BEFORE.upper(), 'size': 1})
        self.assertEqual(v, 'fail', 'hash comparison must be case-insensitive')

    def test_no_before_hash_fails(self):
        v = verdict_with({'exists': True, 'sha256': AFTER, 'size': 1},
                         criterion='file_changed:%s' % PATH)
        self.assertEqual(v, 'fail', 'file_changed without a BEFORE hash cannot be verified')

    def test_verdicts_discriminate(self):
        """Guard against the two arms collapsing into one another again."""
        same = verdict_with({'exists': True, 'sha256': BEFORE, 'size': 1})
        diff = verdict_with({'exists': True, 'sha256': AFTER, 'size': 1})
        self.assertNotEqual(same, diff, 'verdicts must discriminate changed vs unchanged')


if __name__ == '__main__':
    unittest.main(verbosity=2)
