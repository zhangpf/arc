/*
 * Copyright (C) 2013 The Android Open Source Project
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *      http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

#ifndef KERNEL_ARGUMENT_BLOCK_H
#define KERNEL_ARGUMENT_BLOCK_H

#include <elf.h>
#include <link.h>
#include <stdint.h>
#include <sys/auxv.h>

#include "private/bionic_macros.h"

struct abort_msg_t;

// When the kernel starts the dynamic linker, it passes a pointer to a block
// of memory containing argc, the argv array, the environment variable array,
// and the array of ELF aux vectors. This class breaks that block up into its
// constituents for easy access.
class KernelArgumentBlock {
 public:
  KernelArgumentBlock(void* raw_args) {
    uintptr_t* args = reinterpret_cast<uintptr_t*>(raw_args);
    argc = static_cast<int>(*args);
    argv = reinterpret_cast<char**>(args + 1);
    envp = argv + argc + 1;

    // Skip over all environment variable definitions to find aux vector.
    // The end of the environment block is marked by two NULL pointers.
    char** p = envp;
    while (*p != NULL) {
      ++p;
    }
    ++p; // Skip second NULL;

    // ARC MOD BEGIN bionic-elf32-auxv-on-x86-64-nacl
    auxv = reinterpret_cast<Elf32_auxv_t*>(p);
    // ARC MOD END
  }

  // Similar to ::getauxval but doesn't require the libc global variables to be set up,
  // so it's safe to call this really early on. This function also lets you distinguish
  // between the inability to find the given type and its value just happening to be 0.
  unsigned long getauxval(unsigned long type, bool* found_match = NULL) {
    // ARC MOD BEGIN bionic-elf32-auxv-on-x86-64-nacl
    for (Elf32_auxv_t* v = auxv; v->a_type != AT_NULL; ++v) {
    // ARC MOD END
      if (v->a_type == type) {
        if (found_match != NULL) {
            *found_match = true;
        }
        return v->a_un.a_val;
      }
    }
    if (found_match != NULL) {
      *found_match = false;
    }
    return 0;
  }

  int argc;
  char** argv;
  char** envp;
  // ARC MOD BEGIN bionic-elf32-auxv-on-x86-64-nacl
  Elf32_auxv_t* auxv;
  // ARC MOD END

  abort_msg_t** abort_message_ptr;

 private:
  DISALLOW_COPY_AND_ASSIGN(KernelArgumentBlock);
};

#endif // KERNEL_ARGUMENT_BLOCK_H
