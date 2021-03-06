#!src/build/run_python

# Copyright 2014 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

import argparse
import cPickle
import collections
import glob
import itertools
import json
import logging
import os
import re
import shlex
import subprocess
import sys
import tempfile

from src.build import analyze_diffs
from src.build import build_common
from src.build import open_source
from src.build.util import file_util
from src.build.util import logging_util

_GROUP_ASM = 'Assembly'
_GROUP_CPP = 'C/C++'
_GROUP_CSS = 'CSS'
_GROUP_HTML = 'HTML'
_GROUP_JAVA = 'Java'
_GROUP_JS = 'Javascript'
_GROUP_PY = 'Python'

# For the report, statistics will be totaled for all files under these
# directories.
_PATH_PREFIX_GROUPS = [
    'internal/',
    'mods/',
    'mods/android/',
    'mods/android/bionic/',
    'mods/android/frameworks/',
    'mods/chromium-ppapi/',
    'src/',
]

_DEFAULT_LINT_TARGET_DIR_LIST = [
    'canned',
    'mods',
    'internal/build',
    'internal/integration_tests',
    'internal/mods',
    'src',
    # TODO(crbug.com/374475): This subtree should be removed from linting.
    # There should be no ARC MOD sections in third_party, but there are in
    # some NDK directories.
    'third_party/examples',
]


class FileStatistics:
  def __init__(self, filename=None):
    self.filename = filename
    self.stats_dict = collections.defaultdict(int)

  def accumulate(self, source):
    for key, value in source.stats_dict.iteritems():
      self.stats_dict[key] += value


class Linter(object):
  """Interface (and the default implementation) to run lint.

  Provides following methods.
  - should_run(path): Returns True if the linter should run for the file.
    By default, True is returned, which means the linter should run for any
    files. Subclasses can override if necessary.
  - run(path): Applies the lint to the file. Returns True on success, otherwise
    False. All subclasses must override this method.
  """

  def __init__(self, name, target_groups=None,
               ignore_mods=False, ignore_upstream_tracking_file=True):
    """Initializes the basic linter instance.

    - name: Name of the linter. Used for the name based ignoring check whose
      rule is written in the ignore file.
    - target_groups: List of groups. Used for the file extension based
      ignoring check.
    - ignore_mods: If True, the linter will not be applied to files under
      mods/. By default: False.
    - ignore_upstream_tracking_file: If True, the linter will not be applied
      to files tracking an upstream file. By default: True.

    Please see also LinterRunner for the common ignoring rule implementation.
    """
    self._name = name
    # Use tuple as a frozen list.
    self._target_groups = tuple(target_groups) if target_groups else None
    self._ignore_mods = ignore_mods
    self._ignore_upstream_tracking_file = ignore_upstream_tracking_file

  @property
  def name(self):
    return self._name

  @property
  def target_groups(self):
    return self._target_groups

  @property
  def ignore_mods(self):
    return self._ignore_mods

  @property
  def ignore_upstream_tracking_file(self):
    return self._ignore_upstream_tracking_file

  def should_run(self, path):
    """Returns True if this linter should be applied to the file at |path|."""
    # Returns True, by default, which means this linter will be applied to
    # any files.
    return True

  def run(self, path):
    """Applies the linter to the file at |path|."""
    # All subclasses must override this function.
    raise NotImplementedError()


class CommandLineLinterBase(Linter):
  """Abstract Linter implementation to run a linter child process."""

  def __init__(self, name, error_line_filter=None, *args, **kwargs):
    """Initialize the linter.

    - error_line_filter: On subprocess error, it logs subprocess's output, but
      it sometimes contains unuseful information. This is the regexp to filter
      it.
    - remaining args are just redirected to the the base class __init__.
    """
    super(CommandLineLinterBase, self).__init__(name, *args, **kwargs)
    self._error_line_filter = (
        re.compile(error_line_filter, re.M) if error_line_filter else None)

  def run(self, path):
    command = self._build_command(path)
    env = self._build_env()
    try:
      subprocess.check_output(command, stderr=subprocess.STDOUT, env=env)
      return True
    except OSError:
      logging.exception('Unable to invoke %s', command)
      return False
    except subprocess.CalledProcessError as e:
      if self._error_line_filter:
        output = '\n'.join(self._error_line_filter.findall(e.output))
      else:
        output = e.output
      logging.error('Lint output errors:\n%s', output)
      return False

  def _build_command(self, path):
    """Builds the commandline to run a subprocess, and returns it."""
    # All subclasses must implement this.
    raise NotImplementedError()

  def _build_env(self):
    """Builds the env dict for a subprocess, and returns it.

    By default returns None, which means to use the current os.environ
    as is.
    """
    # Subclass can override this to set up environment variable dict for
    # the subprocess.
    return None


class CppLinter(CommandLineLinterBase):
  """Linter for C/C++ source/header files."""

  def __init__(self):
    super(CppLinter, self).__init__(
        'cpplint', target_groups=[_GROUP_CPP], ignore_mods=True,
        # Strip less information lines.
        error_line_filter='^(?:(?!Done processing|Total errors found:))(.*)')

  def _build_command(self, path):
    return ['third_party/tools/depot_tools/cpplint.py', '--root=src', path]


class JsLinter(CommandLineLinterBase):
  """Linter for JavaScript files."""

  def __init__(self):
    super(JsLinter, self).__init__(
        'gjslint', target_groups=[_GROUP_JS],
        # Strip the path to the arc root directory.
        error_line_filter=(
            '^' + re.escape(build_common.get_arc_root()) + '/(.*)'))

  def _build_command(self, path):
    # gjslint is run with the following options:
    #
    #  --unix_mode
    #      Lists the filename with each error line, which is what most linters
    #      here do.
    #
    #  --jslint_error=all
    #      Includes all the extra error checks. Some of these are debatable, but
    #      it seemed easiest to enable everything, and then disable the ones we
    #      do not find useful.
    #
    #  --disable=<error numbers>
    #      Disable specific checks by error number. The ones we disable are:
    #
    #      * 210 "Missing docs for parameter" (no @param doc comment)
    #      * 213 "Missing type in @param tag"
    #      * 217 "Missing @return JsDoc in function with non-trivial return"
    #
    #  --custom_jsdoc_tags=<tags>
    #      Indicates extra jsdoc tags that should be allowed, and not have an
    #      error generated for them. By default closure does NOT support the
    #      full set of jsdoc tags, including "@public". This is how we can use
    #      them without gjslint complaining.
    return ['src/build/gjslint', '--unix_mode', '--jslint_error=all',
            '--disable=210,213,217', '--custom_jsdoc_tags=public,namespace',
            path]


class PyLinter(CommandLineLinterBase):
  """Linter for python."""

  _DISABLED_LINT_LIST = [
      # In ARC, we use two space indent rule, instead of four.
      'E111',  # indentation is not a multiple of four
      'E114',  # indentation is not a multiple of four (comment)

      # In if-statement's condition, we align multi-line conditions to the
      # beginning of open paren at the first line.
      'E125',  # continuation line with same indent as next logical line
      'E129',  # visually indented line with same indent as next logical line

      # TODO(crbug.com/298621): The common case is "import some module after
      # sys.path modification". Currently it cannot be easily removed.
      # As a part of src/build reorganization effort, it can be reduced.
      'E402',  # module level import not at top of file
  ]

  def __init__(self):
    super(PyLinter, self).__init__('flake8', target_groups=[_GROUP_PY])

  def should_run(self, path):
    # Do not run Python linter for the third_party library, which is not managed
    # by us.
    return not path.startswith('third_party/')

  def _build_command(self, path):
    return ['src/build/flake8',
            '--ignore=' + ','.join(PyLinter._DISABLED_LINT_LIST),
            '--max-line-length=80',
            path]


class TestConfigLinter(CommandLineLinterBase):
  """Linter for src/integration_tests/expectations/"""
  # This list must be sync'ed with suite_runner_config's evaluation context.
  # Please see also suite_runner_config._read_test_config().
  _BUILTIN_VARS = [
      # Expectation flags.
      'PASS', 'FAIL', 'TIMEOUT', 'NOT_SUPPORTED', 'LARGE', 'FLAKY',

      # OPTIONS is commonly used in the conditions.
      'OPTIONS',

      # Variables which can be used to check runtime configurations.
      'ON_BOT',
      'USE_GPU',
      'USE_NDK_DIRECT_EXECUTION',

      # Platform information of the machine on which the test runs.
      'ON_CYGWIN', 'ON_MAC', 'ON_CHROMEOS',
  ]

  # These are not the test config files.
  _META_FILE_LIST = ['OPEN_SOURCE', 'OWNERS']

  def __init__(self):
    super(TestConfigLinter, self).__init__('testconfig')

  def should_run(self, path):
    return (path.startswith('src/integration_tests/expectations/') and
            os.path.basename(path) not in TestConfigLinter._META_FILE_LIST)

  def _build_command(self, path):
    # E501: line too long.
    # We do not limit the line length, considering some test names are very
    # long.
    return ['src/build/flake8', '--ignore=E501', path]

  def _build_env(self):
    env = os.environ.copy()
    env['PYFLAKES_BUILTINS'] = ','.join(TestConfigLinter._BUILTIN_VARS)
    return env


class CopyrightLinter(CommandLineLinterBase):
  """Linter to check copyright notice."""

  def __init__(self):
    super(CopyrightLinter, self).__init__(
        'copyright',
        target_groups=[_GROUP_ASM, _GROUP_CPP, _GROUP_CSS, _GROUP_HTML,
                       _GROUP_JAVA, _GROUP_JS, _GROUP_PY])

  def should_run(self, path):
    # TODO(crbug.com/411195): Clean up all copyrights so we can turn this on
    # everywhere.  Currently our priority is to have the open sourced
    # copyrights all be consistent.
    return path.startswith('src/') or open_source.is_open_sourced(path)

  def _build_command(self, path):
    return ['src/build/check_copyright.py', path]


class UpstreamLinter(Linter):
  """Linter to check the contents of upstream note in mods/upstream."""

  _VAR_PATTERN = re.compile(r'^\s*([A-Z_]+)\s*=(.*)$')

  def __init__(self):
    super(UpstreamLinter, self).__init__('upstreamlint')

  def should_run(self, path):
    # mods/upstream directory is not yet included in open source so we cannot
    # run this linter.
    if open_source.is_open_source_repo():
      return False
    if os.path.basename(path) == 'OWNERS':
      return False
    return path.startswith(analyze_diffs.UPSTREAM_BASE_PATH)

  def run(self, path):
    with open(path) as f:
      lines = f.read().splitlines()

    # The description is leading lines before variable definitions.
    description = list(itertools.takewhile(
        lambda line: not UpstreamLinter._VAR_PATTERN.search(line), lines))

    # Parse variables from the trailing lines.
    var_map = {}
    for line in lines[len(description):]:
      m = UpstreamLinter._VAR_PATTERN.search(line)
      if not m:
        continue
      var_map[m.group(1)] = m.group(2).strip()

    # If ARC_COMMIT is present, it must not be empty.
    if var_map.get('ARC_COMMIT') == '':
      logging.error('Upstream file has empty commit info: %s', path)
      return False

    # 'UPSTREAM' var must be contained.
    if 'UPSTREAM' not in var_map:
      logging.error('Upstream file has no upstream info: %s', path)
      return False

    # If 'UPSTREAM' var is empty, there must be (non-empty) descriptions.
    if (not var_map['UPSTREAM'] and
        sum(1 for line in description if line.strip()) == 0):
      logging.error(
          'Upstream file has no upstream URL and no description: %s', path)
      return False
    return True


class LicenseLinter(Linter):
  """Linter to check MODULE_LICENSE_TODO files."""

  def __init__(self):
    super(LicenseLinter, self).__init__('licenselint')

  def should_run(self, path):
    # Accept only MODULE_LICENSE_TODO file.
    return os.path.basename(path) == 'MODULE_LICENSE_TODO'

  def run(self, path):
    with open(path) as f:
      content = f.read()
    if not content.startswith('crbug.com/'):
      logging.error('MODULE_LICENSE_TODO must contain a crbug.com link for '
                    'resolving the todo: %s', path)
      return False
    return True


class OpenSourceLinter(Linter):
  """Linter to check OPEN_SOURCE files."""

  def __init__(self):
    super(OpenSourceLinter, self).__init__('opensourcelint')

  def should_run(self, path):
    # Accept only OPEN_SOURCE file.
    return (os.path.basename(path) == 'OPEN_SOURCE' and
            # tests/open_source/ contains bad OPEN_SOURCE files for
            # testing open_source.py itself. Skip them.
            not path.startswith('src/build/tests/open_source/'))

  def run(self, path):
    dirname = os.path.dirname(path)
    for line in file_util.read_metadata_file(path):
      # Checks that each line matches at least one file in the directory.
      if line.startswith('!'):
        pattern = os.path.join(dirname, line[1:])
      else:
        pattern = os.path.join(dirname, line)
      if not glob.glob(pattern):
        logging.error('\'%s\' in %s does not match any file in %s/.' % (
            line, path, dirname))
        return False
    return True


class DiffLinter(CommandLineLinterBase):
  """Linter to apply analyze_diffs.py."""

  def __init__(self, output_dir):
    """Create DiffLinter instance.

    - output_dir: If specified (i.e it is not None), analyze_diffs.py
      generates the output file under the directory.
      Note that it is the caller's responsibility to remove the generated
      files.
    """
    super(DiffLinter, self).__init__(
        'analyze_diffs', ignore_upstream_tracking_file=False)
    self._output_dir = output_dir

  def _build_command(self, path):
    command = ['src/build/analyze_diffs.py', path]
    if self._output_dir:
      # Create a tempfile as a placeholder of the output.
      # In latter phase, the file is processed by iterating the files in the
      # |output_dir|. It is caller's responsibility to remove the file.
      # Note that analyze_diffs.py creates .d file automatically if output_file
      # is specified. To figure out it, we use '.out' extension.
      with tempfile.NamedTemporaryFile(
          prefix=os.path.basename(path), suffix='.out',
          dir=self._output_dir, delete=False) as output_file:
        command.append(output_file.name)
    return command


class LinterRunner(object):
  """Takes a list of Linters, and runs them."""

  _EXTENSION_GROUP_MAP = {
      '.asm': _GROUP_ASM,
      '.c': _GROUP_CPP,
      '.cc': _GROUP_CPP,
      '.cpp': _GROUP_CPP,
      '.css': _GROUP_CSS,
      '.h': _GROUP_CPP,
      '.hpp': _GROUP_CPP,
      '.html': _GROUP_HTML,
      '.java': _GROUP_JAVA,
      '.js': _GROUP_JS,
      '.py': _GROUP_PY,
      '.s': _GROUP_ASM,
  }

  def __init__(self, linter_list, ignore_rule=None):
    self._linter_list = linter_list
    self._ignore_rule = ignore_rule or {}

  def run(self, path):
    # In is_tracking_an_upstream_file, the path is opened.
    # To avoid invoking it many times for a file, we cache the result, and
    # pass it to Linter.should_run() method via an argument.
    is_tracking_upstream = analyze_diffs.is_tracking_an_upstream_file(path)
    group = LinterRunner._EXTENSION_GROUP_MAP.get(
        os.path.splitext(path)[1].lower())
    result = True
    for linter in self._linter_list:
      # Common rule to check if linter should be applied to the file.
      if (linter.name in self._ignore_rule.get(path, []) or
          (linter.target_groups and group not in linter.target_groups) or
          (linter.ignore_upstream_tracking_file and is_tracking_upstream) or
          (linter.ignore_mods and path.startswith('mods/'))):
        continue
      # Also, check each linter specific rule.
      if not linter.should_run(path):
        continue

      logging.info('%- 10s: %s', linter.name, path)
      result &= linter.run(path)

    if not result:
      logging.error('%s: has lint errors', path)
    return result


def _run_lint(target_file_list, ignore_rule, output_dir):
  """Applies all linters to the target_file_list.

  - target_file_list: List of the target files' paths.
  - ignore_rule: Linter ignoring rule map. Can be None.
  - output_dir: Directory to store the analyze_diffs.py's output data.
    If specified, it is callers' responsibility to remove the generated
    files, if necessary.
  """
  runner = LinterRunner(
      [CppLinter(), JsLinter(), PyLinter(), TestConfigLinter(),
       CopyrightLinter(), UpstreamLinter(), LicenseLinter(),
       OpenSourceLinter(), DiffLinter(output_dir)],
      ignore_rule)
  result = True
  for path in target_file_list:
    result &= runner.run(path)
  return result


def _process_analyze_diffs_output(output_dir):
  """Processes analyze_diffs.py's output, and returns a list of FileStatistics.
  """
  result = []
  for path in os.listdir(output_dir):
    if os.path.splitext(path)[1] != '.out':
      continue
    with open(os.path.join(output_dir, path), mode='rb') as stream:
      output = cPickle.load(stream)
    source_path = output['our_path']
    added = output['added_lines']
    removed = output['removed_lines']

    file_statistics = FileStatistics(source_path)
    stats_dict = file_statistics.stats_dict
    stats_dict['Files'] = 1
    if output['tracking_path'] is None:
      assert removed == 0, repr(output)
      stats_dict['New files'] = 1
      stats_dict['New lines'] = added
    else:
      stats_dict['Patched files'] = 1
      stats_dict['Patched lines added'] = added
      stats_dict['Patched lines removed'] = removed
    result.append(file_statistics)
  return result


def _walk(path):
  """List all files under |path|, including subdirectories."""
  return build_common.find_all_files(
      path, use_staging=False, include_tests=True)


def _should_ignore(filename):
  """Returns True if any linter should not apply to the file."""
  extension = os.path.splitext(filename)[1]
  basename = os.path.basename(filename)
  if os.path.isdir(filename):
    return True
  if os.path.islink(filename):
    return True
  if build_common.is_common_editor_tmp_file(basename):
    return True
  if extension == '.pyc':
    return True
  if filename.startswith('src/build/tests/analyze_diffs/'):
    return True
  if filename.startswith('docs/'):
    return True
  return False


def _filter_files(files):
  if not files:
    files = []  # In case of None.
    for lint_target_dir in _DEFAULT_LINT_TARGET_DIR_LIST:
      files.extend(_walk(lint_target_dir))
  return [x for x in files if not _should_ignore(x)]


def get_all_files_to_check():
  return _filter_files(None)


def _expand_path_list(path_list):
  result = []
  for path in path_list:
    result.extend(_walk(path) if os.path.isdir(path) else [path])
  return result


def _read_ignore_rule(path):
  """Reads the mapping of paths to lint checks to ignore from a file.

  The ignore file is expected to define a simple mapping between file paths
  and the lint rules to ignore (the <List Class>.NAME attributes). Hash
  characters ('#') can be used for comments, as well as blank lines for
  readability.

  A typical # filter in the file should look like:

    # Exclude src/xyzzy.cpp from the checks "gnusto" and "rezrov"
    "src/xyzzy.cpp": ["gnusto", "rezrov"]
  """
  if not path:
    return {}

  result = json.loads('\n'.join(file_util.read_metadata_file(path)))

  # Quick verification.
  # Make sure everything exists in the non-open source repo.  (We do
  # not run this check on the open source repo since not all files are
  # currently open sourced.)
  if not open_source.is_open_source_repo():
    unknown_path_list = [key for key in result if not os.path.exists(key)]
    assert not unknown_path_list, (
        'The key in \'%s\' contains unknown files: %s' % (
            path, unknown_path_list))
  return result


def process(target_path_list, ignore_file=None, output_file=None):
  target_file_list = _expand_path_list(target_path_list)
  ignore_rule = _read_ignore_rule(ignore_file)
  target_file_list = _filter_files(target_file_list)

  # Create a temporary directory as the output dir of the analyze_diffs.py,
  # iff |output_file| is specified.
  output_dir = tempfile.mkdtemp(dir='out') if output_file else None
  try:
    if not _run_lint(target_file_list, ignore_rule, output_dir):
      return 1

    if output_file:
      statistic_list = _process_analyze_diffs_output(output_dir)
      with open(output_file, 'wb') as stream:
        cPickle.dump(statistic_list, stream)
  finally:
    if output_dir:
      file_util.rmtree(output_dir)
  return 0


def _all_file_statistics(files):
  for filename in files:
    with open(filename) as f:
      for file_statistics in cPickle.load(f):
        yield file_statistics


def _all_groups_for_filename(filename):
  # Output groups based on what the path starts with.
  for prefix in _PATH_PREFIX_GROUPS:
    if filename.startswith(prefix):
      yield 'Under:' + prefix + '*'


def _report_stats_for_group(group_name, stats, output_file):
  output_file.write(group_name + '\n')
  for key in sorted(stats.stats_dict.keys()):
    output_file.write('    {0:<30} {1:10,d}\n'.format(
        key, stats.stats_dict[key]))
  output_file.write('-' * 60 + '\n')


def _report_stats(top_stats, grouped_stats, output_file):
  for group_name in sorted(grouped_stats.keys()):
    _report_stats_for_group(group_name, grouped_stats[group_name], output_file)
  _report_stats_for_group('Project Total', top_stats, output_file)


def merge_results(files, output_file):
  top_stats = FileStatistics()
  grouped_stats = collections.defaultdict(FileStatistics)

  for file_statistics in _all_file_statistics(files):
    filename = file_statistics.filename
    top_stats.accumulate(file_statistics)
    for group in _all_groups_for_filename(filename):
      grouped_stats[group].accumulate(file_statistics)

  _report_stats(top_stats, grouped_stats, sys.stdout)
  with open(output_file, 'w') as output_file:
    _report_stats(top_stats, grouped_stats, output_file)


class ResponseFileArgumentParser(argparse.ArgumentParser):
  def __init__(self, *args, **kwargs):
    # Automatically set fromfile_prefix_chars to '@', if the caller did not
    # specify a value for it.
    kwargs.setdefault('fromfile_prefix_chars', '@')
    super(ResponseFileArgumentParser, self).__init__(*args, **kwargs)

  def convert_arg_line_to_args(self, arg_line):
    """Called by the base class when reading arguments from a file."""
    # Ninja will quote filenames written to the response file as if they were
    # passed via the command line. As a result, we need to use shlex.split() to
    # properly dequote them. This then handles cases like the filename itself
    # containing quotes.
    # As a useful side effect, this also handles multiple space separated
    # arguments on a single line, since spaces would otherwise need to be
    # quoted.
    return shlex.split(arg_line)


def main():
  parser = ResponseFileArgumentParser()
  parser.add_argument('files', nargs='*',
                      help='The list of files to lint.  If no files provided, '
                      'will lint all files.')
  parser.add_argument('--ignore', '-i', dest='ignore_file',
                      help='A text file containting list of files to ignore.')
  parser.add_argument('--merge', action='store_true', help='Merge results.')
  parser.add_argument('--output', '-o', help='Output file for storing results.')
  parser.add_argument('--verbose', '-v', action='store_true',
                      help='Prints additional output.')
  args = parser.parse_args()
  logging_util.setup(level=logging.DEBUG if args.verbose else logging.WARNING)

  if not args.ignore_file and not args.files:
    args.ignore_file = os.path.join(
        os.path.dirname(__file__), 'lint_ignore.txt')

  if args.merge:
    return merge_results(args.files, args.output)
  else:
    return process(args.files, args.ignore_file, args.output)

if __name__ == '__main__':
  sys.exit(main())
