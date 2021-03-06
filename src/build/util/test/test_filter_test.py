# Copyright 2014 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

import unittest

from src.build.util.test import flags
from src.build.util.test import test_filter


class TestListFilterTest(unittest.TestCase):
  def test_default_behavior(self):
    # Check the default behavior.
    # should_include() returns True for any test names.
    instance = test_filter.TestListFilter()
    self.assertTrue(instance.should_include('test1'))
    self.assertTrue(instance.should_include('test2'))
    # Even an empty string should be included by default (corner case)
    self.assertTrue(instance.should_include(''))

  def test_include_pattern_list(self):
    # Check the include_pattern_list behavior.
    instance = test_filter.TestListFilter(
        include_pattern_list=['test1', 'test2-*'])
    # Exact match case.
    self.assertTrue(instance.should_include('test1'))

    # It can use glob pattern.
    self.assertTrue(instance.should_include('test2-suffix1'))
    self.assertTrue(instance.should_include('test2-suffix2'))

    # Reject the unknown test names.
    self.assertFalse(instance.should_include('unknown-test-name'))

    # Include pattern is not a prefix-match.
    self.assertFalse(instance.should_include('test1-suffix1'))

  def test_exclude_pattern_list(self):
    # Check the exclude_pattern_list behavior.
    instance = test_filter.TestListFilter(
        exclude_pattern_list=['test1', 'test2-*'])
    # Exact match case.
    self.assertFalse(instance.should_include('test1'))

    # It can use glob pattern.
    self.assertFalse(instance.should_include('test2-suffix1'))
    self.assertFalse(instance.should_include('test2-suffix2'))

    # Accept the unknown test names.
    self.assertTrue(instance.should_include('unknown-test-name'))

    # Exclude pattern is not a prefix-match.
    self.assertTrue(instance.should_include('test1-suffix1'))

  def test_both_pattern_list(self):
    # Check the both include_ and exclude_pattern_list behavior.
    instance = test_filter.TestListFilter(
        include_pattern_list=['test1', 'test2', 'test3-*'],
        exclude_pattern_list=['test2', 'test3-suffix2'])
    # Only matches with include_pattern.
    self.assertTrue(instance.should_include('test1'))
    self.assertTrue(instance.should_include('test3-suffix1'))

    # Matches with both include_pattern and exclude_pattern.
    self.assertFalse(instance.should_include('test2'))
    self.assertFalse(instance.should_include('test3-suffix2'))

    # Mathes with no patterns.
    self.assertFalse(instance.should_include('unknown-test-name'))


class TestRunFilterTest(unittest.TestCase):
  def test_should_run(self):
    # Check the default behavior.
    instance = test_filter.TestRunFilter()
    self.assertTrue(instance.should_run(flags.FlagSet(flags.PASS)))
    self.assertTrue(instance.should_run(flags.FlagSet(flags.FLAKY)))
    self.assertFalse(instance.should_run(flags.FlagSet(flags.FAIL)))
    self.assertFalse(instance.should_run(flags.FlagSet(flags.TIMEOUT)))
    self.assertFalse(instance.should_run(flags.FlagSet(flags.NOT_SUPPORTED)))
    self.assertFalse(instance.should_run(
        flags.FlagSet(flags.PASS | flags.LARGE)))

    instance = test_filter.TestRunFilter(include_fail=True)
    self.assertTrue(instance.should_run(flags.FlagSet(flags.FAIL)))

    instance = test_filter.TestRunFilter(include_large=True)
    self.assertTrue(instance.should_run(
        flags.FlagSet(flags.PASS | flags.LARGE)))

    instance = test_filter.TestRunFilter(include_timeout=True)
    self.assertTrue(instance.should_run(flags.FlagSet(flags.TIMEOUT)))


if __name__ == '__main__':
  unittest.main()
