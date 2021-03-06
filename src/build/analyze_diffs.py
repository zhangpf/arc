#!src/build/run_python

# Copyright 2014 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

# ARC MOD IGNORE - Keep from running on ourself since this file will
# contain a lot of grepbait that will confuse itself.

"""Analyze differences in code from Android upstream.

There are two main goals:
 1) enforce a pattern in the source code to demarkate diffs.
 2) summarize the numbers of patches and patched files from upstream.
"""

import argparse
import cPickle
import os
import re
import subprocess
import sys

from src.build import notices
from src.build import open_source
from src.build import staging

_args = None
FILE_IGNORE_TAG = 'ARC MOD IGNORE'
FILE_TRACK_TAG = 'ARC MOD TRACK'
FORK_BASE_PATH = 'mods/fork/'
MAX_ALLOWED_COMMON_LINES_IN_REGION = 20
MAX_ARC_TRACK_SEARCH_LINES = 2
REGION_END_TAG = 'ARC MOD END'
REGION_FORK_TAG = 'FORK'
REGION_START_TAG = 'ARC MOD BEGIN'
REGION_UPSTREAM_TAG = 'UPSTREAM'
UPSTREAM_BASE_PATH = 'mods/upstream/'

VALID_TAGS = ('',
              REGION_FORK_TAG,
              REGION_UPSTREAM_TAG,
              REGION_UPSTREAM_TAG + ' ' + REGION_FORK_TAG)
IGNORE_SUBDIRECORIES = (FORK_BASE_PATH,
                        UPSTREAM_BASE_PATH)


def show_error(stats, error):
  if not stats['display_errors']:
    stats['errors'] = True
    return

  if not stats['errors']:
    sys.stderr.write('Errors found in file ' + stats['our_path'] + ':\n\n')
    if stats['tracking_path'] is not None:
      sys.stderr.write('(Use following to verify:\ndiff -u %s \\\n%s\n)\n\n' %
                       (stats['tracking_path'], stats['our_path']))
  stats['errors'] = True
  sys.stderr.write('Line %d: %s\n' % (stats['lineno'], error))


def _read_all_lines(path):
  with open(path) as f:
    return f.readlines()


def construct_stats(our_path, tracking_path=None, display_errors=True):
  return {
      'added_lines': 0,
      'current_region': None,
      'display_errors': display_errors,
      'errors': False,
      'lineno': 0,
      'our_path': our_path,
      'tracking_path': tracking_path,
      'removed_lines': 0}


def analyze_new_file(stats, our_lines):
  lineno = 1
  for line in our_lines:
    stats['lineno'] = lineno
    if FILE_IGNORE_TAG in line:
      break
    if REGION_START_TAG in line or REGION_END_TAG in line:
      show_error(stats,
                 'Mod regions found in file that is not tracking any file\n')
    lineno += 1
  stats['added_lines'] = len(our_lines)


def diff_files(our_path, tracking_path):
  cmd = ['diff', '--unified=0', tracking_path, our_path]
  process = subprocess.Popen(cmd, stdout=subprocess.PIPE)
  os.waitpid(process.pid, 1)
  output = process.communicate()[0]
  return output.splitlines()


def extract_tag_from_line(line, is_diff):
  # Special hack to handle XML (i.e. <!-- ARC MOD BEGIN -->).
  if line.endswith('-->'):
    line = line[:-len('-->')]
  if is_diff:
    m = re.match(r'^\+\W+([\w\s\-]+)\W*$', line)
  else:
    m = re.match(r'^\W+([\w\s\-]+)\W*$', line)
  if not m:
    return None
  tag = m.group(1).strip()
  if tag.startswith(REGION_START_TAG):
    tag = tag[len(REGION_START_TAG):]
  elif tag.startswith(REGION_END_TAG):
    tag = tag[len(REGION_END_TAG):]
  else:
    return None
  if not tag:
    return tag
  if len(tag) < 2 or tag[0] != ' ':
    return None
  return tag[1:]


class ModStats(object):
  def __init__(self):
    self.mod_count = 0
    self.line_count = 0


def _extract_tag_and_ref_name_from_line(line, is_diff):
  tag = extract_tag_from_line(line, is_diff)
  name = ''
  # Look for a trailing name which can be using any combination of lowercase
  # letters numbers, underscores, or dashes. We do not include uppercase letters
  # since the tags otherwise use uppercase letters.
  m = re.match(r'(.*?)\b([\-_a-z0-9]+)$', tag or '')
  if m:
    tag = m.group(1).strip()
    name = m.group(2)

  return tag, name


def get_file_mod_stats_for_upstream_refs(file_name, mod_stats_map):
  """Updates stats inside mod_stats_map with data gathered from the file."""
  with open(file_name) as f:
    lines = f.readlines()
    upstream_ref = None
    upstream_start_line = None
    for line_number, line in enumerate(lines):
      if REGION_START_TAG in line:
        tag, ref_name = _extract_tag_and_ref_name_from_line(line, False)
        if REGION_UPSTREAM_TAG in tag:
          upstream_ref = ref_name
          upstream_start_line = line_number
      elif REGION_END_TAG in line and upstream_ref:
        mod_stats = mod_stats_map[upstream_ref]
        mod_stats.mod_count += 1
        mod_stats.line_count += line_number - upstream_start_line - 1
        upstream_ref = None
        upstream_start_line = None


class _AnalyzeDiffState(object):
  def __init__(self, stats, our_path, tracking_path):
    self._stats = stats
    self._our_path = our_path
    self._tracking_path = tracking_path
    self._current_tag_specifier = None

    # It is bad to have '+' lines inserted outside of an ARC
    # mod region.  It is not as clear with '-' lines.  These can
    # be part of changes like so:
    # -DoSomething();
    # +// ARC MOD BEGIN
    # +DoSomethingElse()
    # +// ARC MOD END
    #
    # In this case _potentially_deleted_line is set to the
    # 'DoSomething();' line indicating that we need to skip past -
    # sequences and look for a '+' that has a begin tag. Otherwise if
    # there are empty or '@' lines (indicating intervening unchanged
    # context), we emit an error.
    self._potentially_deleted_line = None
    # Skip the +++ / --- left and right file listing lines.
    self._got_left_right_files = False
    # Line number of new file the next time through the loop.
    self._next_lineno = 1
    # Set to true if the FILE_IGNORE_TAG is observed.
    self._file_ignored = False

  def _consider_potentially_deleted_lines_deleted(self):
    if self._potentially_deleted_line is not None:
      show_error(self._stats, 'Line removed outside mod region:\n' +
                 self._potentially_deleted_line)
      self._potentially_deleted_line = None

  def _handle_hunk_header(self, line):
    self._consider_potentially_deleted_lines_deleted()
    if line[0] == '@':
      matches = re.search(r'\+(\d+)', line)
      if not matches:
        show_error(self._stats, 'Problem reading diff output: ' + line)
      else:
        self._next_lineno = int(matches.group(1))
    if line[0] == ' ':
      self._next_lineno += 1

  def _handle_region_start(self, line):
    self._current_tag_specifier = self._verify_tag_line(line, True)
    if self._stats['current_region']:
      show_error(self._stats, 'Nested region: ' + line)
    self._stats['current_region'] = {
        'added_lines': 0,
        'is_fork': False,
        'start_lineno': self._stats['lineno']}
    if REGION_FORK_TAG in line:
      self._stats['current_region']['is_fork'] = True

  def _handle_region_end(self, line):
    specifier = self._verify_tag_line(line, False)
    if specifier != self._current_tag_specifier:
      show_error(self._stats, 'End tag does not match the start:\n' + line[1:])
    is_fork = REGION_FORK_TAG in line
    if not self._stats['current_region']:
      show_error(self._stats, 'Unmatched region end tag:\n' + line[1:])
    elif self._stats['current_region']['is_fork'] != is_fork:
      show_error(self._stats,
                 'Mismatched fork/non-fork tags:\n' + line[1:])
    else:
      # Check that most of the lines in this region differed or otherwise
      # we could have made the region smaller.  The exception is for
      # regions marked as being stubbed functions.  These regions are
      # going to have a lot of common code (for function prototypes)
      # but putting in ARC MOD BEGIN/END in every function
      # seems excessive.
      total_lines = (self._stats['lineno'] -
                     self._stats['current_region']['start_lineno'])
      added_lines = self._stats['current_region']['added_lines']
      common_lines = total_lines - added_lines
      if (not self._stats['current_region']['is_fork'] and
          common_lines > MAX_ALLOWED_COMMON_LINES_IN_REGION):
        show_error(self._stats,
                   ('Region starting on line %d has too much common code '
                    '(%d lines in common of %d) use %s to suppress:\n'
                    '%s') %
                   (self._stats['current_region']['start_lineno'],
                    common_lines,
                    total_lines,
                    REGION_FORK_TAG,
                    line[1:]))
    self._stats['current_region'] = None

  def _verify_mod_description_file(self, desc_file, msg):
    if (_args and _args.under_test and
        self._our_path.startswith(staging.TESTS_MODS_PATH)):
      desc_file = os.path.join(staging.TESTS_BASE_PATH, desc_file)

    if not os.path.isfile(desc_file):
      # In open source repo we have no upstream files (except when running
      # tests) but if in internal repo, we verify the file exists.
      if (_args and _args.under_test) or not open_source.is_open_source_repo():
        show_error(self._stats, msg + desc_file)
    return True

  def _verify_tag_line(self, line, is_begin_tag):
    tag, ref_name = _extract_tag_and_ref_name_from_line(line, True)
    if tag is None:
      show_error(self._stats, 'Invalid tag line:\n' + line[1:])

    if tag not in VALID_TAGS:
      show_error(self._stats, 'Invalid tag line:\n' + line[1:])

    if is_begin_tag and REGION_UPSTREAM_TAG in tag and not ref_name:
      show_error(self._stats, 'Upstream missing identifier:\n' + line[1:])

    if is_begin_tag and ref_name:
      if REGION_UPSTREAM_TAG in tag:
        desc_file = os.path.join(UPSTREAM_BASE_PATH, ref_name)
        msg = 'Upstream description file does not exist: '
      else:
        desc_file = os.path.join(FORK_BASE_PATH, ref_name)
        msg = 'Modification description file does not exist: '

      self._verify_mod_description_file(desc_file, msg)

    return tag

  def _handle_source_difference(self, line):
    # Allow base tags to be added on any line without declaring
    # a region around them.
    if (not self._stats['current_region'] and
        FILE_TRACK_TAG not in line):
      show_error(self._stats,
                 'Line added outside mod region:\n' + line[1:])
    if self._stats['current_region']:
      self._stats['current_region']['added_lines'] += 1

  def _handle_addition(self, line):
    # If code is added it must be added inside a region
    self._next_lineno += 1
    if FILE_IGNORE_TAG in line:
      # Ignore this entire file by breaking out of for ... diff_lines loop
      self._file_ignored = True
      return
    if REGION_START_TAG in line:
      self._handle_region_start(line)

    if self._potentially_deleted_line is not None:
      # Did we get the needed region begin?
      if not self._stats['current_region']:
        self._consider_potentially_deleted_lines_deleted()
      else:
        # We did so clear the requirement.
        self._potentially_deleted_line = None

    self._stats['added_lines'] += 1

    if REGION_END_TAG in line:
      self._handle_region_end(line)
    else:
      self._handle_source_difference(line)

  def _handle_removal(self, line):
    self._stats['removed_lines'] += 1
    if not self._stats['current_region']:
      # It may be OK to have removed lines in the case of changed
      # lines.  There must be a sequence of '-' lines followed by
      # a '+' line that contains ARC MOD BEGIN.
      self._potentially_deleted_line = line[1:]

  def _process_diff_format_lines(self, diff_lines):
    for line in diff_lines:
      if self._file_ignored:
        return

      self._stats['lineno'] = self._next_lineno

      if not self._got_left_right_files:
        if line.startswith('+++'):
          self._got_left_right_files = True
      else:
        if line[0] == '@' or line[0] == ' ':
          self._handle_hunk_header(line)
        elif line[0] == '+':
          self._handle_addition(line)
        elif line[0] == '-':
          self._handle_removal(line)
    self._consider_potentially_deleted_lines_deleted()

  def run(self):
    diff_lines = diff_files(self._our_path, self._tracking_path)

    # Don't analyze binary files.  This can be used to override images, for
    # example, to cut down image size.
    if (len(diff_lines) == 1 and diff_lines[0].startswith('Binary files ') and
        diff_lines[0].endswith(' differ')):
      return

    self._process_diff_format_lines(diff_lines)

    if not self._file_ignored:
      if self._stats['current_region']:
        show_error(self._stats, 'Unmatched end tag starting on line %d\n' %
                   self._stats['current_region']['start_lineno'])

      if (self._stats['added_lines'] == 0 and
          self._stats['removed_lines'] == 0):
        show_error(self._stats, 'No lines were changed, remove this file\n\n')


def analyze_diff(stats, our_path, tracking_path):
  _AnalyzeDiffState(stats, our_path, tracking_path).run()


def compute_tracking_path(stats, our_path, our_lines, do_lint_check=False,
                          check_exist=True, check_uses_tags=False):
  """Find the tracking file for the given file.

  Returns the last path mentioned in the file via a tracking tag or
  the equivalent third-party path given the file's path.  If there is
  no file in the default path and no files mentioned within the file
  exist, returns None.

  Normally the third-party path must exist. Passing |check_exist|=False will
  bypass this check when it is not desired.

  An additional check is enabled by passing |check_uses_tag|=True. In this case
  the given file MUST use either a file track tag or another modification tag,
  before a tracking_path is returned.

  stats is a variable for keeping track of the status of the analyzer,
  which can be None."""
  tracking_path = staging.get_default_tracking_path(our_path)
  base_matcher = re.compile(re.escape(FILE_TRACK_TAG) + r' "([^\"]+)"')
  tag_matcher = re.compile(re.escape(REGION_START_TAG))
  uses_any_tags = False
  next_lineno = 1
  for line in our_lines:
    if stats:
      stats['lineno'] = next_lineno
    match = base_matcher.search(line)
    if match:
      tracking_path = match.group(1)
      if not os.path.exists(tracking_path) and stats:
        show_error(stats, 'Mod tracking path does not exist:\n' + line)
      if next_lineno > MAX_ARC_TRACK_SEARCH_LINES:
        show_error(stats, 'Tracking not allowed on line > %d' %
                   MAX_ARC_TRACK_SEARCH_LINES)
      uses_any_tags = True
      break
    elif not uses_any_tags and tag_matcher.search(line):
      uses_any_tags = True
    next_lineno += 1
    if (not do_lint_check and (uses_any_tags or not check_uses_tags) and
        next_lineno > MAX_ARC_TRACK_SEARCH_LINES):
      break

  if not tracking_path:
    return None

  if check_uses_tags and not uses_any_tags:
    return None

  if check_exist and not os.path.exists(tracking_path):
    return None

  return tracking_path


def get_tracking_path(path, check_exist=True, check_uses_tags=False):
  with open(path) as source_file:
    return compute_tracking_path(
        None, path, source_file, check_exist=check_exist,
        check_uses_tags=check_uses_tags)


def is_tracking_an_upstream_file(path):
  return get_tracking_path(path) is not None


def _check_any_license(stats, our_path, tracking_path, default_tracking):
  # No need to require notices for other metadata files, and top directory
  # shortcuts.
  if (os.path.basename(our_path) in ['OPEN_SOURCE', 'OWNERS'] or
      any(our_path.startswith(p) for p in IGNORE_SUBDIRECORIES) or
      our_path in ('.gitmodules', '.gitignore',
                   'configure', 'launch_chrome', 'run_integration_tests')):
    return
  staged_notices = notices.Notices()
  staged_notices.add_sources([our_path])

  if tracking_path:
    # our_path will be a pre-staging path, and so we need to additionally
    # allow its tracking path to have a license file.
    staged_notices.add_sources([tracking_path])
  elif default_tracking:
    # For newly added files (no tracking path) we just assume the default
    # tracking path.
    staged_notices.add_sources([default_tracking])
  elif our_path.startswith('third_party/'):
    # For third_party files, the NOTICE file may be added via mods/ directory.
    # Check the directory, too.
    staged_notices.add_sources(
        [os.path.join('mods', os.path.relpath(our_path, 'third_party'))])

  if not staged_notices.has_proper_metadata():
    show_error(stats, 'File has no notice/license information.')


def _count_directory_levels_in_license_root(notice):
  license_roots = notice.get_license_roots()
  if not license_roots:
    return 0
  assert len(license_roots) == 1
  return next(iter(license_roots)).count('/')


def _compute_staged_notices(mods_path, third_party_path):
  """Compute the notices object as if the two paths were properly staged.

  analyze_diffs needs to be independent of staging.  Staging might not have
  been run, or might be out of date from when analyze_diffs is run.  So
  we make a best attempt to reconstruct the notices that would have occurred
  post-staging."""
  mods_notices = notices.Notices()
  if mods_path:
    mods_notices.add_sources([mods_path])
  third_party_notices = notices.Notices()
  if third_party_path:
    third_party_notices.add_sources([third_party_path])
  # If there are mods and third_party notices, pick the one that is more
  # specific to the file, which is the one that has a deeper path.
  if (_count_directory_levels_in_license_root(third_party_notices) >
      _count_directory_levels_in_license_root(mods_notices)):
    return third_party_notices
  else:
    return mods_notices


def _check_less_restrictive_tracking_license(stats, our_path,
                                             tracking_path,
                                             default_tracking):
  containing_directory_notices = _compute_staged_notices(our_path,
                                                         default_tracking)
  if tracking_path.startswith('third_party/'):
    mods_path = os.path.join('mods',
                             os.path.relpath(tracking_path, 'third_party'))
  elif (_args.under_test and
        tracking_path.startswith(staging.TESTS_THIRD_PARTY_PATH)):
    mods_path = os.path.join(staging.TESTS_MODS_PATH,
                             os.path.relpath(tracking_path,
                                             staging.TESTS_THIRD_PARTY_PATH))
  else:
    mods_path = tracking_path
    tracking_path = None
  tracking_directory_notices = _compute_staged_notices(
      mods_path, tracking_path)
  containing_license = (containing_directory_notices.
                        get_most_restrictive_license_kind())
  tracking_license = (tracking_directory_notices.
                      get_most_restrictive_license_kind())
  if not tracking_directory_notices.has_proper_metadata():
    show_error(stats, ('File %s tracked by %s has no license metadata' %
                       (tracking_path, our_path)))
  if (notices.Notices.is_more_restrictive(
          tracking_license, containing_license)):
    show_error(stats, ('File %s (%s) tracks a file with a more restrictive '
                       'license %s (%s)' % (our_path, containing_license,
                                            tracking_path, tracking_license)))


def _check_notices(stats, our_path, tracking_path):
  # No need to require notices for other metadata files.
  basename = os.path.basename(our_path)
  if (basename in ['OWNERS', 'NOTICE'] or
      basename.startswith('MODULE_LICENSE_') or
      any(our_path.startswith(p) for p in IGNORE_SUBDIRECORIES)):
    return
  default_tracking = staging.get_default_tracking_path(our_path)
  _check_any_license(stats, our_path, tracking_path, default_tracking)

  # If this file tracks a file, make sure the file it tracks is no more
  # restrictive than this file.  See tests/analyze_diffs/mods/mpl-license/foo.c
  # for an example.
  if tracking_path and (not default_tracking or
                        tracking_path != default_tracking):
    _check_less_restrictive_tracking_license(stats, our_path, tracking_path,
                                             default_tracking)


def analyze_file(raw_our_path, output_file):
  # Code expects first relative directory names to start immediately.
  # Convert ./mods/... to mods/....
  our_path = os.path.relpath(raw_our_path)
  our_lines = _read_all_lines(our_path)
  stats = construct_stats(our_path)
  tracking_path = compute_tracking_path(stats, our_path, our_lines,
                                        do_lint_check=True)
  _check_notices(stats, our_path, tracking_path)
  if tracking_path is None:
    analyze_new_file(stats, our_lines)
  else:
    stats['tracking_path'] = tracking_path
    analyze_diff(stats, our_path, tracking_path)
  if stats['errors']:
    return 1

  if not output_file:
    return 0

  with open(output_file, 'wb') as f:
    cPickle.dump(stats, f)
  with open(output_file + '.d', 'wt') as f:
    f.write(output_file + ': \\\n')
    f.write(' ' + our_path)
    if tracking_path is not None:
      f.write(' \\\n'
              ' %s\n' % tracking_path)
  return 0


def main():
  parser = argparse.ArgumentParser()
  parser.add_argument('--under_test', action='store_true',
                      help='internal flag indicating analyze_diffs is being '
                      'tested')
  parser.add_argument('source_file', help='the file to analyze.')
  parser.add_argument('result_file', nargs='?', default=None,
                      help='intermediate results file.')
  global _args
  _args = parser.parse_args()
  return analyze_file(_args.source_file, _args.result_file)

if __name__ == '__main__':
  sys.exit(main())
