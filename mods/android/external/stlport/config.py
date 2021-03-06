# Copyright 2014 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from src.build import make_to_ninja
from src.build import ninja_generator
from src.build.build_options import OPTIONS

_STLPORT_ROOT = 'android/external/stlport'

_EXCLUDE_FILES = [
    # Target duplication. There is string_header_test.cpp.
    'string_header_test.c',

    # Link fails on NaCl i686 and NaCl x86-64.
    # Need to add "-fexceptions" for cxx flag for compile.
    'exception_test.cpp',

    # Compile fails on GCC >= 4.7 due to extra unqualified
    # lookups. (http://gcc.gnu.org/gcc-4.7/porting_to.html)
    'rope_test.cpp',

    # Link fails on NaCl x86-64.
    'cmath_test.cpp',
    'codecvt_test.cpp',
]

_EXCLUDE_TESTS = [
    # Float precision error due to sprinf for formatting.
    'NumPutGetTest::num_put_float',
    'NumPutGetTest::num_get_float',
    'NumPutGetTest::custom_numpunct',

    # This test is broken: it tries to parse several strings that result in an
    # overflow, which causes the fail bit to set, and then asserts either
    # !stream.fail() or that the value is -1 instead of the max value.
    'NumPutGetTest::num_get_integer',

    # /dev/null is not allowd on NaCl host.
    'FstreamTest::null_stream',

    # wchar_t is not available on bionic.
    'IOStreamTest::in_avail',
    'LimitTest::test',

    # Opening directory with open(2) on NaCl fails.
    # This should not be a problem on production ARC because
    # file opening is managed with posix_translation.
    'FstreamTest::input',

    # istr.imbue hang up. Seems locale is not suppoteded.
    'CodecvtTest::variable_encoding',

    # Tests are failed on qemu but not on real devices.
    'StringTest::mt',
    'HashTest::hmmap2'
]


def generate_ninjas():
  def _filter(vars):
    # Android uses two different C++ libraries: bionic's libstdc++ (instead of
    # GCC's libstdc++) and clang's libc++ (only for ART and a few other
    # libraries). STLport does not have compiler dependent functions (functions
    # which are called from code generated by compiler), so use bionic's and
    # link it into libstlport.so for simplicity.
    vars.get_sources().extend([
        'android/bionic/libc/bionic/new.cpp',
        'android/bionic/libc/bionic/__cxa_guard.cpp',
        'android/bionic/libc/bionic/__cxa_pure_virtual.cpp',
        # Needed for __libc_fatal.
        'android/bionic/libc/bionic/libc_logging.cpp'])
    vars.get_includes().append('android/bionic/libc')
    vars.get_includes().remove('android/bionic/libstdc++/include')
    vars.get_sys_includes().remove('android/bionic/libstdc++/include')
    # This is necessary to use atomic operations in bionic. 1 indicates
    # compilation for symmetric multi-processor (0 for uniprocessor).
    vars.get_cflags().append('-DANDROID_SMP=1')
    if vars.is_shared():
      # This is for not emitting syscall wrappers.
      vars.get_shared_deps().extend(['libc', 'libm'])
      # Note: libstlport.so must be a system library because other system
      # libraries such as libchromium_ppapi.so and libposix_translation.so
      # depend on it. We already have some ARC MODs against STLPort to make
      # it work without --wrap, and the cost of maintaining the MODs seems
      # very low because STLPort is not under active development anymore.
      vars.get_generator_args()['is_system_library'] = True
      # TODO(crbug.com/364344): Once Renderscript is built from source, this
      # canned install can be removed.
      if not OPTIONS.is_arm():
        vars.set_canned_arm(True)
    return True
  make_to_ninja.MakefileNinjaTranslator(_STLPORT_ROOT).generate(_filter)


def generate_test_ninjas():
  n = ninja_generator.TestNinjaGenerator(
      'stlport_unittest',
      base_path=_STLPORT_ROOT + '/test/unit')

  n.add_c_flags('-Werror')
  n.add_cxx_flags('-Werror')

  # For avoiding compile failure on min_test.cpp.
  n.add_cxx_flags('-Wno-sequence-point')

  # For avoiding compile failure on sstream_test.cpp and time_facets_test.cpp.
  n.add_cxx_flags('-Wno-uninitialized')

  if n.is_clang_enabled():
    # For avoiding compile failure on string_test.cpp.
    n.add_cxx_flags('-Wno-unused-comparison')

    # For avoiding compile failure on resolve_name.cpp.
    n.add_cxx_flags('-Wno-unused-function')

    # For avoiding compile failure on fstream_test.cpp and sstream_test.cpp.
    n.add_cxx_flags('-Wno-unused-private-field')

    # For avoiding compile failure on slist_test.cpp, list_test.cpp and
    # type_traits_test.cpp.
    n.add_cxx_flags('-Wno-unused-variable')
  else:
    # For avoiding compile failure on --disable-debug-code.
    n.add_cxx_flags('-Wno-maybe-uninitialized')

  # Define STLPORT so that cppunit_proxy.h does not define
  # CPPUNIT_MINI_USE_EXCEPTIONS
  n.add_cxx_flags('-DSTLPORT')

  n.build_default(
      n.find_all_files(_STLPORT_ROOT + '/test/unit',
                       ['.cpp', '.c'],
                       include_tests=True,
                       exclude=_EXCLUDE_FILES),
      base_path=None)

  argv = '-x=%s' % ','.join(_EXCLUDE_TESTS)
  n.run(n.link(), argv=argv, enable_valgrind=OPTIONS.enable_valgrind(),
        rule='run_test')
