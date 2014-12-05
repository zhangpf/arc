# Copyright 2014 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

import unittest

from util.test import java_script_test_result_parser as result_parser


class MockCallback(object):
  def __init__(self):
    self.result = []

  def start_test(self, test_name):
    self.result.append(('start_test', test_name))

  def update(self, result_list):
    self.result.append(('update', result_list))


class JavaScriptTestResultParser(unittest.TestCase):
  def test_start(self):
    callback = MockCallback()
    parser = result_parser.JavaScriptTestResultParser('jstests.all', callback)
    parser.process_line(
        '[13748:13748:1202/184723:INFO:CONSOLE(136)] '
        '"INFO: [ RUN      ] BackgroundPageTest.SendCrashReportsFromRelease", '
        'source: chrome-extension://dummy_hash_code/chrome_test.js (136)')
    self.assertEqual(
        [('start_test',
          'jstests.all:BackgroundPageTest#SendCrashReportsFromRelease')],
        callback.result)
    self.assertFalse(parser.test_result)

  def test_success(self):
    callback = MockCallback()
    parser = result_parser.JavaScriptTestResultParser('jstests.all', callback)
    parser.process_line(
        '[13748:13748:1202/184723:INFO:CONSOLE(136)] '
        '"INFO: [       OK ] BackgroundPageTest.SendCrashReportsFromRelease '
        '(215ms)", source: chrome-extension://dummy_hash_code/chrome_test.js '
        '(136)')
    self.assertEqual(1, len(callback.result))
    self.assertEqual('update', callback.result[0][0])

    update_result = callback.result[0][1]
    self.assertEqual(1, len(update_result))
    self.assertEqual(
        'jstests.all:BackgroundPageTest#SendCrashReportsFromRelease',
        update_result[0].name)
    self.assertTrue(update_result[0].passed)
    self.assertAlmostEqual(0.215, update_result[0].duration)

    test_result = parser.test_result
    self.assertEqual(1, len(test_result))
    self.assertIn('jstests.all:BackgroundPageTest#SendCrashReportsFromRelease',
                  test_result)
    test_case_result = test_result[
        'jstests.all:BackgroundPageTest#SendCrashReportsFromRelease']
    self.assertEqual(
        'jstests.all:BackgroundPageTest#SendCrashReportsFromRelease',
        test_case_result.name)
    self.assertTrue(test_case_result.passed)
    self.assertAlmostEqual(0.215, test_case_result.duration)

  def test_fail(self):
    callback = MockCallback()
    parser = result_parser.JavaScriptTestResultParser('jstests.all', callback)
    parser.process_line(
        '[13748:13748:1202/184723:INFO:CONSOLE(136)] '
        '"INFO: [  FAILED  ] BackgroundPageTest.SendCrashReportsFromRelease '
        '(1.5s)", source: chrome-extension://dummy_hash_code/chrome_test.js '
        '(136)')
    self.assertEqual(1, len(callback.result))
    self.assertEqual('update', callback.result[0][0])

    update_result = callback.result[0][1]
    self.assertEquals(1, len(update_result))
    self.assertEqual(
        'jstests.all:BackgroundPageTest#SendCrashReportsFromRelease',
        update_result[0].name)
    self.assertTrue(update_result[0].failed)
    self.assertAlmostEqual(1.5, update_result[0].duration)

    test_result = parser.test_result
    self.assertEqual(1, len(test_result))
    self.assertIn('jstests.all:BackgroundPageTest#SendCrashReportsFromRelease',
                  test_result)
    test_case_result = test_result[
        'jstests.all:BackgroundPageTest#SendCrashReportsFromRelease']
    self.assertEqual(
        'jstests.all:BackgroundPageTest#SendCrashReportsFromRelease',
        test_case_result.name)
    self.assertTrue(test_case_result.failed)
    self.assertAlmostEqual(1.5, test_case_result.duration)