#!src/build/run_python

# Copyright 2014 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

import datetime
import os
import re
import string
import sys

from src.build import analyze_diffs

_MAXIMUM_COPYRIGHT_PATTERN_LINES = 7

_CHROMIUM_PATTERN = r"""
.* Copyright( \(c\))? (20\d\d) The Chromium (.*)Authors. All rights reserved.
.* Use of this source code is governed by a BSD-style license that can be
.* found in the LICENSE file..*
""".lstrip()

_CHROMIUM_CANONICAL = """
 Copyright $year The Chromium Authors. All rights reserved.
 Use of this source code is governed by a BSD-style license that can be
 found in the LICENSE file.""".lstrip('\n')

_ANDROID_PATTERN = r"""
.* Copyright \(C\) 20\d\d.* The Android Open Source Project
.*
.* Licensed under the Apache License, Version 2.0 \(the "License"\);
.* you may not use this file except in compliance with the License.
.* You may obtain a copy of the License at
.*
.*      http://www.apache.org/licenses/LICENSE-2.0
""".lstrip()

_ANDROID_CANONICAL = """
 Copyright (C) $year The Android Open Source Project

 Licensed under the Apache License, Version 2.0 (the "License");
 you may not use this file except in compliance with the License.
 You may obtain a copy of the License at

      http://www.apache.org/licenses/LICENSE-2.0

 Unless required by applicable law or agreed to in writing, software
 distributed under the License is distributed on an "AS IS" BASIS,
 WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 See the License for the specific language governing permissions and
 limitations under the License""".lstrip('\n')


def _expand_canonical(canonical, ext, long_slash_star):
  canonical = string.Template(canonical).substitute(
      {'year': datetime.date.today().year})
  lines = canonical.splitlines()
  outlines = []
  if ext in ['.c', '.cpp', '.cc', '.h', '.java', '.js', '.S']:
    if long_slash_star or ext == '.S':
      outlines = ['/*'] + [' *' + l for l in lines] + [' */']
    else:
      outlines = ['//' + l for l in lines]
  elif ext in ['.gypi', '.py']:
    outlines = ['#' + l for l in lines]
  elif ext in ['.css', '.html']:
    outlines = (['<!--' + lines[0]] + ['    ' + l for l in lines[1:-1]] +
                ['    ' + lines[-1] + ' -->'])
  else:
    sys.exit('Unknown comment style for file with ext ' + ext)
  return ''.join([l + '\n' for l in outlines])


def main():
  for file_name in sys.argv[1:]:
    basename = os.path.basename(file_name)
    name, ext = os.path.splitext(file_name)

    long_slash_star = False
    if (basename == '__init__.py'):
      # __init__.py files do not need copyrights headers
      return 0
    if (basename == 'config.py' or
        file_name.startswith('canned/') or
        file_name.startswith('mods/android/external/chromium_org/') or
        file_name.startswith('mods/chromium-ppapi/') or
        file_name.startswith('src/')):
      pattern = _CHROMIUM_PATTERN
      canonical = _CHROMIUM_CANONICAL
    elif (file_name.startswith('mods/android/') or
          file_name.startswith('mods/graphics_translation/')):
      pattern = _ANDROID_PATTERN
      canonical = _ANDROID_CANONICAL
      long_slash_star = True
    elif (file_name.startswith('third_party/examples/') or
          file_name.startswith('mods/examples/')):
      # Ignore this directory since we will not be open sourcing it.
      return 0
    elif (file_name.startswith('third_party/android/bionic-aosp/') or
          file_name.startswith('third_party/freebsd/') or
          file_name.startswith('third_party/openbsd/')):
      # They are used for Bionic and only <20 reviewed files exist.
      # TODO(crbug.com/406226): Remove this whitelist once ARC is
      # rebased to L.
      return 0
    else:
      print 'Unknown license pattern for', file_name
      return 1

    with open(file_name, 'r') as f:
      lines = f.readlines()
      if analyze_diffs.compute_tracking_path(None, file_name, lines):
        # We assume copyrights in tracked files are correct.
        return 0
      headers = []
      for line in lines:
        if not headers and 'opyright' not in line:
          continue
        headers.append(line)
        if len(headers) == _MAXIMUM_COPYRIGHT_PATTERN_LINES:
          break
      header = ''.join(headers)
      if not headers:
        print '%s: does not have a copyright notice at all' % file_name
        print '\nSuggested:\n%s' % _expand_canonical(canonical, ext,
                                                     long_slash_star)
        return 1
      m = re.search(pattern, header)
      if not m:
        print '%s: has an incorrect copyright header:\n\n%s' % (
            file_name, header)
        print 'Suggested:\n%s' % _expand_canonical(canonical, ext,
                                                   long_slash_star)
        return 1
      if pattern == _CHROMIUM_PATTERN:
        # For Chromium copyright, make sure (c) is not used after 2014.
        has_pseudo_c_symbol = m.group(1)
        is_2014_or_newer = int(m.group(2)) >= 2014
        is_chromium_os = m.group(3).find('OS') != -1
        if has_pseudo_c_symbol and is_2014_or_newer and not is_chromium_os:
          print ('(c) should not be put in new copyright headers:\n\n%s' %
                 header)
          return 1
  return 0

if __name__ == '__main__':
  sys.exit(main())
