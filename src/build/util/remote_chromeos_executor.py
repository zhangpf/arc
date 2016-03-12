# Copyright 2014 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Utilities for running Chrome and dalvik tests on a remote ChromeOS device."""

import atexit
import errno
import os
import re
import subprocess
import sys
import traceback

from src.build import build_common
from src.build import toolchain
from src.build.build_options import OPTIONS
from src.build.util import launch_chrome_util
from src.build.util import remote_executor_util
from src.build.util.test import unittest_util

# The fake login name used when running Chrome remotely on a Chrome OS device.
_FAKE_TEST_USER = 'arc_fake_test_user@gmail.com'
# The environment variables that are set when running Chrome in the remote host.
_REMOTE_ENV = {
    'DISPLAY': ':0.0',
    # /var/tmp instead of /tmp is used here because /var/tmp uses the same
    # filesystem as that for /home/chronos, where user profile directory is
    # stored by default on Chrome OS. /tmp uses tmpfs and performance
    # characteristics might be different.
    'TMPDIR': '/var/tmp',
    'XAUTHORITY': '/home/chronos/.Xauthority',
    'XDG_RUNTIME_DIR': '/run/chrome',
}
# The existence of this file indicates the user is logged in.
_CHROMEOS_LOGGED_IN_FILE = '/var/run/state/logged-in'
# If this file exists, session_manager on Chrome OS does not restart Chrome when
# Chrome is killed.
_DISABLE_CHROME_RESTART_FILE = '/var/run/disable_chrome_restart'
# This holds the command line of Chrome browser process.
_CHROME_COMMAND_LINE_FILE = '/var/tmp/chrome_command_line'

_REMOTE_CHROME_EXE_BINARY = '/opt/google/chrome/chrome'
_REMOTE_NACL_HELPER_NONSFI_BINARY = '/opt/google/chrome/nacl_helper_nonsfi'
_CRYPTOHOME = '/usr/sbin/cryptohome'

_UNNEEDED_PARAM_PREFIXES = (
    '--enterprise',
    '--ppapi-flash',
    '--system-developer-mode',
    '--vmodule')


_EXEC_PATTERNS = [
    '{out}/target/{target}/bin',
]


def _create_remote_executor(parsed_args, enable_pseudo_tty=False,
                            attach_nacl_gdb_type=None,
                            nacl_helper_nonsfi_binary=None,
                            arc_dir_name=None, jdb_port=None, jdb_type=None):
  return remote_executor_util.RemoteExecutor(
      'root', parsed_args.remote, remote_env=_REMOTE_ENV,
      ssh_key=parsed_args.ssh_key, enable_pseudo_tty=enable_pseudo_tty,
      attach_nacl_gdb_type=attach_nacl_gdb_type,
      nacl_helper_nonsfi_binary=nacl_helper_nonsfi_binary,
      arc_dir_name=arc_dir_name,
      jdb_port=jdb_port, jdb_type=jdb_type)


def _get_param_name(param):
  m = re.match(r'--([^=]+).*', param)
  if not m:
    return None
  return m.group(1)


def _is_param_set(checked_param, params):
  """Check if a command line parameter is specified in the list of parameters.

  Return True if |checked_param| is included in |params| regardless of the
  value of |checked_param|. For example, the following expression returns True.

      _is_param_set('--log-level=0', ['--log-level=2', '--enable-logging'])
  """
  checked_param_name = _get_param_name(checked_param)
  assert checked_param_name, 'Invalid param: ' + checked_param
  for param in params:
    param_name = _get_param_name(param)
    if checked_param_name == param_name:
      return True
  return False


def _setup_remote_arc_root(executor, copied_files):
  # Copy specified files to the remote host.
  exec_paths = _get_exec_paths()
  executor.rsync(
      copied_files, executor.get_remote_arc_root(), exclude_paths=exec_paths)
  executor.rsync(exec_paths,
                 build_common.get_chromeos_arc_root_without_noexec())


def _get_exec_paths():
  """Returns the list of paths for executable files and directories.

  On ChromeOS, most files are copied to the directories that are mounted with
  noexec option, but the files in the list returned by this function, or
  contained in a directory in the list, are copied to the directory mounted
  without noexec option, so that executables can be executed directly for
  testing.
  """
  return sorted(
      [build_common.expand_path_placeholder(path) for path in _EXEC_PATTERNS] +
      [toolchain.get_adb_path_for_chromeos()] +
      unittest_util.get_nacl_tools() +
      unittest_util.get_test_executables(
          test for test in unittest_util.get_all_tests()
          if not unittest_util.is_bionic_fundamental_test(test)))


def _setup_remote_processes(executor):
  file_map = {
      'chrome_exe_file': _REMOTE_CHROME_EXE_BINARY,
      'command_line_file': _CHROME_COMMAND_LINE_FILE,
      'logged_in_file': _CHROMEOS_LOGGED_IN_FILE,
      'restart_file': _DISABLE_CHROME_RESTART_FILE,
  }
  commands = [
      # Force logging out if a user is logged in or
      # _DISABLE_CHROME_RESTART_FILE is left, which indicates the last session
      # did not finish cleanly.
      # TODO(mazda): Change this to a more reliable way.
      ('if [ -f %(logged_in_file)s -o -f %(restart_file)s ]; then '
       '  rm -f %(restart_file)s; restart ui; sleep 1s; '
       'fi'),
      # Disallow session_manager to restart Chrome automatically.
      'touch %(restart_file)s',
      # Remove the Chrome command line file in case it is left for any reason.
      'rm -f %(command_line_file)s',
      # Search the command line of browser process, which does not include
      # --type=[..] flag, then save the command line into a file.
      ('ps -a -x -oargs | '
       'grep "^%(chrome_exe_file)s " | '
       'grep --invert-match type= > %(command_line_file)s'),
      # Kill all the chrome processes launched by session_manager.
      'pkill -9 -P `pgrep session_manager` chrome$',
      # Mount Cryptohome as guest. Otherwise Chrome crashes during NSS
      # initialization. See crbug.com/401061 for more details.
      'cryptohome --action=mount_guest',
  ]
  executor.run_commands([command % file_map for command in commands])

  # Recover the chrome processes after the command ends
  atexit.register(lambda: _restore_remote_processes(executor))


def _setup_remote_profile(executor, user):
  """Mounts the cryptohome for |user|. """
  # To abort launch with showing error messages on failure, need to grep the
  # output line since cryptohome's status code is 0 even if mount fails due to
  # incorrect password or unknown users.
  executor.run_commands([
      'echo -n "Enter the password for <%s>: "' % user,
      '%s --action=mount --user=%s | grep "Mount succeeded."' %
      (_CRYPTOHOME, user)])


def _restore_remote_processes(executor):
  try:
    print 'Restarting remote UI...'
  except IOError:
    # Still try to restart the remote UI even if stdout has closed.
    pass
  # Allow session_manager to restart Chrome and do restart.
  executor.run('rm -f %s %s && restart ui' % (_DISABLE_CHROME_RESTART_FILE,
                                              _CHROME_COMMAND_LINE_FILE))


def _setup_remote_environment(parsed_args, executor, copied_files):
  try:
    _setup_remote_arc_root(executor, copied_files)
    if parsed_args.remote_machine_setup:
      _setup_remote_processes(executor)
  except subprocess.CalledProcessError as e:
    # Print the stack trace if any preliminary command fails so that the
    # failing command can be examined easily, and rethrow the exception to pass
    # the exit code.
    traceback.print_exc()
    raise e

  # To disable password echo, temporary enable pseudo tty.
  original_pseudo_tty = executor.set_enable_pseudo_tty(True)
  try:
    if parsed_args.login_user:
      _setup_remote_profile(executor, parsed_args.login_user)
  except subprocess.CalledProcessError as e:
    print 'Mounting cryptohome failed.'
    print 'Please check your username and password.'
    print ('Also note that you need to login to the device with the specified '
           'username at least once beforehand.')
    traceback.print_exc()
    raise e
  finally:
    executor.set_enable_pseudo_tty(original_pseudo_tty)


def get_chrome_exe_path():
  return _REMOTE_CHROME_EXE_BINARY


def launch_remote_chrome(parsed_args, argv):
  try:
    attach_nacl_gdb_type = None
    nacl_helper_nonsfi_binary = None
    need_copy_nacl_helper_nonsfi_binary = False
    jdb_port = None
    jdb_type = None

    if 'plugin' in parsed_args.gdb:
      attach_nacl_gdb_type = parsed_args.gdb_type
      if OPTIONS.is_bare_metal_build():
        nacl_helper_nonsfi_binary = parsed_args.nacl_helper_nonsfi_binary
        if not nacl_helper_nonsfi_binary:
          # We decide the path here, but the binary can be copied after
          # RemoteExecutor is constructed.
          nacl_helper_nonsfi_binary = os.path.join(
              remote_executor_util.get_remote_binaries_dir(),
              os.path.basename(_REMOTE_NACL_HELPER_NONSFI_BINARY))
          need_copy_nacl_helper_nonsfi_binary = True

    if 'jdb_port' in vars(parsed_args) and 'jdb_type' in vars(parsed_args):
      jdb_port = parsed_args.jdb_port
      jdb_type = parsed_args.jdb_type

    executor = _create_remote_executor(
        parsed_args, attach_nacl_gdb_type=attach_nacl_gdb_type,
        nacl_helper_nonsfi_binary=nacl_helper_nonsfi_binary,
        arc_dir_name=parsed_args.remote_arc_dir_name,
        jdb_port=jdb_port, jdb_type=jdb_type)

    copied_files = remote_executor_util.get_launch_chrome_deps(parsed_args)
    _setup_remote_environment(parsed_args, executor, copied_files)

    if need_copy_nacl_helper_nonsfi_binary:
      remote_binaries = [_REMOTE_NACL_HELPER_NONSFI_BINARY]
      executor.copy_remote_files(remote_binaries,
                                 remote_executor_util.get_remote_binaries_dir())

    if nacl_helper_nonsfi_binary:
      # This should not happen, but for just in case.
      assert os.path.exists(nacl_helper_nonsfi_binary)
      # -v: show the killed process, -w: wait for the killed process to die.
      executor.run('sudo killall -vw gdbserver', ignore_failure=True)

    command = ' '.join(
        ['sudo', '-u', 'chronos',
         executor.get_remote_env()] +
        launch_chrome_util.get_launch_chrome_command(
            remote_executor_util.create_launch_remote_chrome_param(argv)))
    executor.run_with_filter(command)
  except subprocess.CalledProcessError as e:
    sys.exit(e.returncode)


def _get_user_hash(user):
  """Returns the user hash.

  The returned hash is used for launching Chrome with mounted cryptohome
  directory.
  """
  return subprocess.check_output([_CRYPTOHOME, '--action=obfuscate_user',
                                  '--user=%s' % user]).strip()


def extend_chrome_params(parsed_args, params):
  # Do not show the New Tab Page because showing NTP during perftest makes the
  # benchmark score look unnecessarily bad especially on ARM Chromebooks where
  # CPU resource is very limited.
  # TODO(yusukes): Use this option on Windows/Mac/Linux too. We might need to
  # use --keep-alive-for-test then.
  params.append('--no-startup-window')

  if parsed_args.login_user:
    user = parsed_args.login_user
    params.append('--login-user=%s' % user)
    params.append('--login-profile=%s' % _get_user_hash(user))
  else:
    # Login as a fake test user when login_user is not specified.
    params.append('--login-user=' + _FAKE_TEST_USER)

  if OPTIONS.is_arm() and parsed_args.mode in ('atftest', 'system'):
    # On ARM Chromebooks, there is a bug (crbug.com/270064) that causes X server
    # to hang when multiple ash host windows are displayed in the size of the
    # screen, which is the default ash host window size on Chrome OS. In order
    # to workaround this issue, show the ash host window in the size 1 pixel
    # wider than the original screen size.
    # Newer Chromebooks run without X and don't need this workaround.
    # TODO(crbug.com/314050): Remove the workaround once the upstream issue is
    # fixed, or all supported Chromebooks stop using X.

    try:
      output = subprocess.check_output(['xdpyinfo', '-display', ':0.0'])
      m = re.search(r'dimensions: +([0-9]+)x([0-9]+) pixels', output)
      if not m:
        raise Exception('Cannot get the screen size')
      width, height = int(m.group(1)) + 1, int(m.group(2))
      params.append('--ash-host-window-bounds=0+0-%dx%d' % (width, height))
    except OSError as e:
      # Ignore the error on missing xdpyinfo assuming the Chromebook runs
      # without X.
      if e.errno != errno.ENOENT:
        raise
    except subprocess.CalledProcessError:
      # Ignore the error on calling xdpyinfo assuming the Chromebook runs
      # without X.
      pass

  assert os.path.exists(_CHROME_COMMAND_LINE_FILE), (
      '%s does not exist.' % _CHROME_COMMAND_LINE_FILE)
  with open(_CHROME_COMMAND_LINE_FILE) as f:
    chrome_command_line = f.read().rstrip()
  params_str = re.sub('^%s ' % _REMOTE_CHROME_EXE_BINARY, '',
                      chrome_command_line)
  # Use ' -' instead of ' ' to split the command line flags because the flag
  # values can contain spaces.
  new_params = params_str.split(' -')
  new_params[1:] = ['-' + param for param in new_params[1:]]

  # Check if _UNNEEDED_PARAM_PREFIXES is up to date.
  for unneeded_param in _UNNEEDED_PARAM_PREFIXES:
    if not any(p.startswith(unneeded_param) for p in new_params):
      print 'WARNING: _UNNEEDED_PARAM_PREFIXES is outdated. Remove %s.' % (
          unneeded_param)

  # Append the flags that are not set by our scripts.
  for new_param in new_params:
    if not _is_param_set(new_param, params) and _is_param_needed(new_param):
      params.append(new_param)


def _is_param_needed(param):
  if (param.startswith(_UNNEEDED_PARAM_PREFIXES) or
      # Do not show login screen
      param == '--login-manager'):
    return False
  return True


def run_remote_unittest(parsed_args):
  copied_files = remote_executor_util.get_unittest_deps(parsed_args)
  try:
    executor = _create_remote_executor(parsed_args)
    _setup_remote_environment(parsed_args, executor, copied_files)

    verbose = ['--verbose'] if parsed_args.verbose else []
    command = ' '.join(
        [executor.get_remote_env(), 'python',
         'src/build/run_python',
         remote_executor_util.RUN_UNITTEST] + verbose +
        parsed_args.tests)
    executor.run(command)
    return 0
  except subprocess.CalledProcessError as e:
    return e.returncode


def run_remote_integration_tests(parsed_args, argv,
                                 configs_for_integration_tests):
  try:
    executor = _create_remote_executor(
        parsed_args, enable_pseudo_tty=parsed_args.ansi)
    copied_files = (
        remote_executor_util.get_integration_test_deps() +
        configs_for_integration_tests)
    _setup_remote_environment(parsed_args, executor, copied_files)
    command = ' '.join(
        ['sudo', '-u', 'chronos', executor.get_remote_env(),
         '/bin/sh', './run_integration_tests'] +
        remote_executor_util.create_launch_remote_chrome_param(argv))
    executor.run_with_filter(command)
    return 0
  except subprocess.CalledProcessError as e:
    return e.returncode


def cleanup_remote_files(parsed_args):
  executor = _create_remote_executor(parsed_args)
  removed_patterns = [
      # ARC root directory in the remote host.
      executor.get_remote_arc_root(),
      # The directory executables are temporarily copied to.
      build_common.get_chromeos_arc_root_without_noexec(),
      # Temporary Chrome profile directories created for integration tests.
      # These sometimes remain after the tests finish for some reasons.
      os.path.join(executor.get_remote_tmpdir(),
                   build_common.CHROME_USER_DATA_DIR_PREFIX + '-*'),
  ]
  executor.run(' '.join(['rm', '-rf'] + removed_patterns), cwd='.')
