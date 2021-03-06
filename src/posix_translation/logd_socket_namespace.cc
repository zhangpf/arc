// Copyright 2015 The Chromium Authors. All rights reserved.
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

#include "posix_translation/logd_socket_namespace.h"

#include <errno.h>

namespace posix_translation {

namespace {

bool IsNameAccepted(const std::string& name) {
  // Note, we have to support original logd names and names used in
  // posix_translation integration test because logd tests in posix_translation
  // will fail when trying to use original names.
  return (name == "/dev/socket/logd" ||
          name == "/dev/socket/logdr" ||
          name == "/dev/socket/logdw" ||
          name == "/dev/socket/testlogd" ||
          name == "/dev/socket/testlogdr" ||
          name == "/dev/socket/testlogdw");
}

}  // namespace

int LogdSocketNamespace::Bind(const std::string& name, LocalSocket* stream) {
  mutex_->AssertAcquired();

  if (!IsNameAccepted(name)) {
    errno = EOPNOTSUPP;
    return -1;
  }

  if (stream && map_.find(name) != map_.end()) {
    errno = EADDRINUSE;
    return -1;
  }

  map_[name] = stream;
  return 0;
}

scoped_refptr<LocalSocket> LogdSocketNamespace::GetByName(
    const std::string& name) {
  mutex_->AssertAcquired();
  Map::const_iterator it = map_.find(name);
  if (it == map_.end())
    return NULL;
  return it->second;
}

}  // namespace posix_translation
