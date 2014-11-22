// Copyright 2014 The Chromium Authors. All rights reserved.
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

#ifndef POSIX_TRANSLATION_LIBC_DISPATCH_TABLE_H_
#define POSIX_TRANSLATION_LIBC_DISPATCH_TABLE_H_

#include <sys/stat.h>
#include <sys/types.h>

namespace arc {

// Actual libc function pointers for the corresponding functions.
struct LibcDispatchTable {
  int (*libc_close)(int fd);
  int (*libc_fstat)(int fd, struct stat* buf);
  off64_t (*libc_lseek)(int fd, off64_t offset, int whence);
  int (*libc_open)(const char* pathname, int flags, mode_t mode);
  ssize_t (*libc_read)(int fd, void* buf, size_t count);
  ssize_t (*libc_write)(int fd, const void* buf, size_t count);
};

extern const LibcDispatchTable g_libc_dispatch_table;

}  // namespace arc

#endif  // POSIX_TRANSLATION_LIBC_DISPATCH_TABLE_H_
