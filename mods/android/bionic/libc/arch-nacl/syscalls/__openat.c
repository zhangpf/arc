// Copyright (C) 2014 The Android Open Source Project
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//      http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

#include <errno.h>

#include <irt_syscalls.h>
#include <nacl_fcntl.h>

#define STATIC_ASSERT(cond, name) \
  struct StaticAssert_ ## name { char name[(cond) ? 1 : -1]; }

int __openat(int dirfd, const char *filename, int flags, int mode) {
  // NaCl IRT does not provide openat.
  if (dirfd != AT_FDCWD) {
    errno = ENOSYS;
    return -1;
  }
  int newfd;
  int nacl_flags = 0;
  int result;

  switch ((flags & O_ACCMODE)) {
    case O_RDONLY:
      nacl_flags |= NACL_ABI_O_RDONLY;
      break;
    case O_WRONLY:
      nacl_flags |= NACL_ABI_O_WRONLY;
      break;
    case O_RDWR:
      nacl_flags |= NACL_ABI_O_RDWR;
      break;
    default:
      // |flags| has broken access mode so we want to set a broken value
      // for |nacl_flags| as well. With NaCl's ABI, NACL_ABI_O_ACCMODE (3)
      // is the broken value so we will set this value.
      nacl_flags |= NACL_ABI_O_ACCMODE;
  }
  if ((flags & O_CREAT))
    nacl_flags |= NACL_ABI_O_CREAT;
  if ((flags & O_TRUNC))
    nacl_flags |= NACL_ABI_O_TRUNC;
  if ((flags & O_APPEND))
    nacl_flags |= NACL_ABI_O_APPEND;
  if ((flags & O_EXCL))
    nacl_flags |= NACL_ABI_O_EXCL;
  if ((flags & O_NONBLOCK))
    nacl_flags |= NACL_ABI_O_NONBLOCK;
  if ((flags & O_NDELAY))
    nacl_flags |= NACL_ABI_O_NDELAY;
  if ((flags & O_SYNC))
    nacl_flags |= NACL_ABI_O_SYNC;
  // NaCl ABI does not define O_DIRECTORY but we need to pass this
  // flag to posix_translation. As ARC's IRT hooks passes through most
  // oflags, we set Bionic's O_DIRECTORY here. Note O_DIRECTORY does
  // not conflict with other NACL_ABI_O_*. The maximum value of
  // NACL_ABI_O_* is 0020000 and O_DIRECTORY is 0040000 on ARM and
  // 0200000 on other CPUs.
#if defined(__arm__)
  STATIC_ASSERT(O_DIRECTORY == 0040000, Value_of_O_DIRECTORY);
#else
  STATIC_ASSERT(O_DIRECTORY == 0200000, Value_of_O_DIRECTORY);
#endif
  if ((flags & O_DIRECTORY))
    nacl_flags |= O_DIRECTORY;
  // Bionic does not have O_ASYNC.

  result = __nacl_irt_open(filename, nacl_flags, mode, &newfd);
  if (result != 0) {
    errno = result;
    return -1;
  }
  return newfd;
}
