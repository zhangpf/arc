#!src/build/run_python

# Copyright 2014 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.


import glob
import os
import subprocess
import sys
import time

from src.build.util import platform_util


_KILL_PROCESSES_LINUX = [
    'adb',
    'chrome',
    # It seems sometimes the name of a chrome process can be
    # this, though their /proc/<pid>/cmdline are still Chrome.
    # See also: http://crbug.com/235893
    'dconf worker',
    'firefox',
    # gnome-panel consumes huge memory when running long time on buildbot.
    # See http://crbug.com/341724 for details.
    'gnome-panel',
    'nacl_helper',
]

_KILL_PROCESSES_CYGWIN = [
    'adb.exe',
    'chrome.exe',
    'nacl64.exe',
    'tail.exe',
]


def _get_processes_to_kill():
  if (platform_util.is_running_on_linux() and
      not platform_util.is_running_on_chromeos()):
    return _KILL_PROCESSES_LINUX
  elif platform_util.is_running_on_cygwin():
    return _KILL_PROCESSES_CYGWIN
  # TODO(mazda): Add the case for Mac.
  return []


def _cleanup_temporary_outputs():
  files = glob.glob('out/integration-test-temp-output*')
  for filename in files:
    print 'BEGIN: ' + filename
    with open(filename, 'r') as f:
      for line in f.readlines():
        print '    ' + line,
    print 'END: ' + filename
    os.unlink(filename)


def _kill_process(process):
  if platform_util.is_running_on_cygwin():
    kill_command = ['taskkill', '/F', '/IM', process]
  else:
    kill_command = ['killall', '-v', '-9', process]
  return subprocess.Popen(kill_command,
                          stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT)


def _cleanup_processes():
  for process in _get_processes_to_kill():
    pipe = _kill_process(process)
    stdout, _ = pipe.communicate()
    if pipe.returncode == 0:
      for line in stdout.splitlines():
        print '@@@STEP_TEXT@%s<br/>@@@' % line.strip()


if __name__ == '__main__':
  _cleanup_temporary_outputs()
  if '--buildbot' in sys.argv[1:]:
    _cleanup_processes()
    # Workaround crbug.com/610723
    time.sleep(60)
