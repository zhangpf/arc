// Copyright (C) 2015 The Android Open Source Project
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
#include <sys/socket.h>

#include <irt_syscalls.h>

int socketpair(int domain, int type, int protocol, int sv[2]) {
  int result = __nacl_irt_socketpair(domain, type, protocol, sv);
  if (result != 0) {
    errno = result;
    return -1;
  }
  return 0;
}
