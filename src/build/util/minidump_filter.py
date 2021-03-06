# Copyright 2014 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""A filter class which extracts minidumps from the log output."""

import base64
import os
import re
import subprocess

from src.build.util import concurrent_subprocess


class MinidumpFilter(concurrent_subprocess.DelegateOutputHandlerBase):
  # The format of the output must match what is output from
  # crash_reporter.js printMiniDump_
  _MINIDUMP_RE = re.compile(r'@@@Minidump generated@@@(.+)@@@(.+)@@@')

  def handle_stdout(self, line):
    if self.handle_common_line(line):
      return
    super(MinidumpFilter, self).handle_stdout(line)

  def handle_stderr(self, line):
    if self.handle_common_line(line):
      return
    super(MinidumpFilter, self).handle_stderr(line)

  def handle_common_line(self, line):
    # Use search instead of match to extract only minidump data from the line.
    m = MinidumpFilter._MINIDUMP_RE.search(line)
    if not m:
      return False
    minidump_name = m.group(1) + '.dmp'
    minidump_data = m.group(2)
    output_path = os.path.join('out', minidump_name)
    # TODO(crbug.com/326438): Add a way to automatically clean up old minidumps.
    with open(output_path, 'wb') as f:
      f.write(base64.b64decode(minidump_data))
    # Fork a background process that outputs crash dump info.
    subprocess.Popen(['src/build/breakpad.py', 'stackwalk', output_path])
    # Extract new line characters. Note that '\r\n' are used when launching
    # chrome remotely via SSH.
    newline = '\r\n' if line.endswith('\r\n') else '\n'
    super(MinidumpFilter, self).handle_stderr(
        'Minidump generated: %s%s' % (output_path, newline))
    # Do not output the line with dump data.
    return True
