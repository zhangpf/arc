# Copyright 2014 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Defines UiAutomatorSuiteRunner which is used to run uiAutomator test types.

UiAutomator tests consist of an apk and a jar file. The jar file contains tests
that use the uiautomator api to test the apk. The apk is installed on the
device and then the jar file is pushed onto the device and executed using the
uiautomator shell command.
"""

import os
import tempfile

from src.build import prep_launch_chrome
from src.build.util.test import atf_instrumentation_result_parser as result_parser  # NOQA
from src.build.util.test import atf_instrumentation_scoreboard_updater as scoreboard_updater  # NOQA
from src.build.util.test import suite_runner
from src.build.util.test import system_mode


# uiautomator expects jar files to be in /data/local/tmp by default.
_DEVICE_TMP_PATH = '/data/local/tmp'


# Controls how many times the suite runner will poll for the InputDispatcher to
# become enabled after the suite runner has told the ActivityManager to start
# the application.
_MAX_DISPATCHENABLED_POLL_RETRIES = 30


class UiAutomatorSuiteRunner(suite_runner.SuiteRunnerBase):
  def __init__(self, name, apk_path, jar_path, main_activity,
               base_expectation_map,
               additional_launch_chrome_opts=None, additional_metadata=None,
               **kwargs):
    super(UiAutomatorSuiteRunner, self).__init__(
        name, base_expectation_map, **kwargs)

    self._apk_path = apk_path
    self._jar_path = jar_path
    self._main_activity = main_activity
    self._additional_launch_chrome_opts = additional_launch_chrome_opts
    self._additional_metadata = additional_metadata

    self._result_parser = None
    self._scoreboard_updater = None

  def _get_uiautomator_args(self, test_methods_to_run):
    jar_name = os.path.basename(self._jar_path)
    args = ['uiautomator', 'runtest', jar_name]
    for test_method in test_methods_to_run:
      args.extend(['-c', test_method])
    return args

  def handle_output(self, line):
    # Since some of our output comes from ADB we might have \r that would
    # normally be removed by the adb command.
    if line.endswith('\r\n'):
      line = line[:-2] + '\n'
    self._result_parser.process_line(line)
    self._scoreboard_updater.update(self._result_parser)

  def setUp(self, test_methods_to_run):
    self._result_parser = result_parser.AtfInstrumentationResultParser()

  def prepare(self, test_methods_to_run):
    args = self.get_system_mode_launch_chrome_command(
        self._name,
        additional_args=self._additional_launch_chrome_opts,
        additional_metadata=self._additional_metadata)
    prep_launch_chrome.prepare_crx_with_raw_args(args)

  def _push_file_using_adb(self, arc, file_path, directory=_DEVICE_TMP_PATH):
    self._logger.write(
        'Pushing host file "%s" to device directory "%s".\n' % (
            file_path, directory))
    arc.run_adb(['push', file_path, directory])
    return os.path.join(directory, os.path.basename(file_path))

  def _install_apk(self, arc):
    # TODO(crbug.com/421544) Change to just |adb install| when bug is fixed.
    path_on_device = self._push_file_using_adb(arc, self._apk_path)
    arc.run_adb(['shell', 'pm', 'install', path_on_device])

  def _launch_main_activity(self, arc):
    arc.run_adb(['shell', 'am', 'start', self._main_activity])

  def _push_shell_file_using_adb(self, arc, test_methods_to_run):
    args = ' '.join(self._get_uiautomator_args(test_methods_to_run))
    with tempfile.NamedTemporaryFile() as tmp_file:
      tmp_file.write(args)
      tmp_file.flush()
      return self._push_file_using_adb(arc, tmp_file.name)

  def _run_shell_file_using_adb(self, arc, file_path):
    args = ['shell', 'sh', file_path]
    return arc.run_adb(args)

  def run(self, test_methods_to_run, scoreboard):
    self._scoreboard_updater = (
        scoreboard_updater.AtfInstrumentationScoreboardUpdater(scoreboard))

    self._logger.write('Running %d uiautomator tests of suite %s\n' %
                       (len(test_methods_to_run), self._name))

    with system_mode.SystemMode(
        self,
        additional_launch_chrome_opts=self._additional_launch_chrome_opts
    ) as arc:
      self._install_apk(arc)
      self._launch_main_activity(arc)
      self._push_file_using_adb(arc, self._jar_path)
      # Android adb has a low line length limit for commands sent over via the
      # client (see https://code.google.com/p/android/issues/detail?id=23351).
      # To get around the low limit we push a shell script on the device and
      # then run the file using sh.
      file_name = self._push_shell_file_using_adb(arc, test_methods_to_run)
      self._run_shell_file_using_adb(arc, file_name)

    if arc.has_error():
      self._logger.write(
          'An error occurred in system mode. '
          'Please see system mode output below for details.\n')
