/*
 * Copyright (C) 2008 The Android Open Source Project
 * All rights reserved.
 *
 * Redistribution and use in source and binary forms, with or without
 * modification, are permitted provided that the following conditions
 * are met:
 *  * Redistributions of source code must retain the above copyright
 *    notice, this list of conditions and the following disclaimer.
 *  * Redistributions in binary form must reproduce the above copyright
 *    notice, this list of conditions and the following disclaimer in
 *    the documentation and/or other materials provided with the
 *    distribution.
 *
 * THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
 * "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
 * LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS
 * FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE
 * COPYRIGHT OWNER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT,
 * INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING,
 * BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS
 * OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED
 * AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
 * OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT
 * OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF
 * SUCH DAMAGE.
 */

#include "libc_init_common.h"

#include <elf.h>
#include <errno.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/auxv.h>
#include <sys/time.h>
#include <unistd.h>

#include "private/bionic_auxv.h"
#include "private/bionic_ssp.h"
#include "private/bionic_tls.h"
#include "private/KernelArgumentBlock.h"
#include "pthread_internal.h"
// ARC MOD BEGIN
// Include irt.h and irt_syscalls.h to use random for __stack_chk_guard.
#if defined(BARE_METAL_BIONIC)
#include <irt.h>
#include <irt_syscalls.h>
#include <sys/endian.h>
#include "nacl_signals.h"

#endif
// ARC MOD END

extern "C" abort_msg_t** __abort_message_ptr;
extern "C" int __system_properties_init(void);
extern "C" int __set_tls(void* ptr);
extern "C" int __set_tid_address(int* tid_address);

void __libc_init_vdso();

// Not public, but well-known in the BSDs.
const char* __progname;

// Declared in <unistd.h>.
char** environ;

// Declared in "private/bionic_ssp.h".
uintptr_t __stack_chk_guard = 0;

// ARC MOD BEGIN
// Utility to obtain current stack for libc_init_tls.
#if defined(HAVE_ARC)
static void* __get_sp(void) {
  // We add 1 for the stack space consumed by push %ebp (or r7 for ARM).
  return (void **)__builtin_frame_address(0) + 1;
}
#endif
// ARC MOD END
/* Init TLS for the initial thread. Called by the linker _before_ libc is mapped
 * in memory. Beware: all writes to libc globals from this function will
 * apply to linker-private copies and will not be visible from libc later on.
 *
 * Note: this function creates a pthread_internal_t for the initial thread and
 * stores the pointer in TLS, but does not add it to pthread's thread list. This
 * has to be done later from libc itself (see __libc_init_common).
 *
 * This function also stores a pointer to the kernel argument block in a TLS slot to be
 * picked up by the libc constructor.
 */
void __libc_init_tls(KernelArgumentBlock& args) {
  __libc_auxv = args.auxv;

  static void* tls[BIONIC_TLS_SLOTS];
  static pthread_internal_t main_thread;
  main_thread.tls = tls;

  // Tell the kernel to clear our tid field when we exit, so we're like any other pthread.
  // As a side-effect, this tells us our pid (which is the same as the main thread's tid).
  // ARC MOD BEGIN
  // NaCl does not have set_tid_address. Instead, we will pass the
  // address to __nacl_irt_thread_exit.
  // main_thread.tid = __set_tid_address(&main_thread.tid);
  main_thread.tid = gettid();
  // ARC MOD END
  main_thread.set_cached_pid(main_thread.tid);

  // We don't want to free the main thread's stack even when the main thread exits
  // because things like environment variables with global scope live on it.
  // We also can't free the pthread_internal_t itself, since that lives on the main
  // thread's stack rather than on the heap.
  pthread_attr_init(&main_thread.attr);
  main_thread.attr.flags = PTHREAD_ATTR_FLAG_USER_ALLOCATED_STACK | PTHREAD_ATTR_FLAG_MAIN_THREAD;
  main_thread.attr.guard_size = 0; // The main thread has no guard page.
  main_thread.attr.stack_size = 0; // User code should never see this; we'll compute it when asked.
  // TODO: the main thread's sched_policy and sched_priority need to be queried.

  // ARC MOD BEGIN
  // Try producing fake information about our stack to be used by
  // pthread_attr.cpp, so that we do not need to parse /proc/self/maps
  // when we need the information.
#if defined(HAVE_ARC)
  uintptr_t stack_top =
      BIONIC_ALIGN(reinterpret_cast<uintptr_t>(__get_sp()), PAGE_SIZE);
  // It would be safe to assume we have 8MB of stack. Yoshi's default
  // stack size is 8MB and the main thread of SFI NaCl has 16MB stack
  // (see NACL_DEFAULT_STACK_MAX in service_runtime/sel_ldr.h).
  //
  // Note this number must be compatible with the one defined in
  // __wrap_getrlimit in misc_wrap.cc. Currently, we return
  // RLIM_INFINITY for RLIMIT_STACK. Bionic assumes the stack size is
  // 8MB when RLIM_INFINITY is returned. See the hard-coded value in
  // __pthread_attr_getstack_main_thread of pthread_attr.cpp.
  // TODO(crbug.com/452386): Consider moving getrlimit from
  // posix_translation to Bionic.
  main_thread.attr.stack_size = 8 * 1024 * 1024;
  main_thread.attr.stack_base = reinterpret_cast<void*>(
      stack_top - main_thread.attr.stack_size);
#endif
  // ARC MOD END
  __init_thread(&main_thread, false);
  __init_tls(&main_thread);
  __set_tls(main_thread.tls);
  tls[TLS_SLOT_BIONIC_PREINIT] = &args;

  __init_alternate_signal_stack(&main_thread);
}

// ARC MOD BEGIN
#if defined(BARE_METAL_BIONIC)
// Define helper functions to initialize __stack_chk_guard for ARC.
static void init_stack_chk_guard_by_irt_random() {
  nacl_irt_random irt_random;
  if (__nacl_irt_query(NACL_IRT_RANDOM_v0_1, &irt_random,
                       sizeof(irt_random)) != sizeof(irt_random)) {
    static const char msg[] =
        "Failed to get irt_random for __stack_chk_guard! "
        "(this is OK for unittests)\n";
    write(STDERR_FILENO, msg, sizeof(msg) - 1);
  }
  size_t nread;
  if (irt_random.get_random_bytes(
          reinterpret_cast<char*>(&__stack_chk_guard),
          sizeof(__stack_chk_guard), &nread) != 0 ||
      nread != sizeof(__stack_chk_guard)) {
    static const char msg[] =
        "Failed to get random bytes for __stack_chk_guard!\n";
    write(STDERR_FILENO, msg, sizeof(msg) - 1);
    exit(1);
  }
}
#endif

static void init_stack_chk_guard() {
  // __stack_chk_guard is a variable which could be used by GCC to detect stack
  // smashing (see -fstack-protector).
  //
  // Since NaCl does not provide AT_RANDOM, we fill a fixed value
  // here. This would be okay for NaCl because NaCl toolchain does not
  // support -fstack-protector anyway.
  __stack_chk_guard = 0xfee1dead;
#if defined(BARE_METAL_BIONIC)
  // For Bare Metal mode, we use IRT random to initialize the
  // canary. If IRT random does not exist, it means we are using
  // nonsfi_loader which does not have IRT random and we are running a
  // unittest. We do not care the security of unittests, let's just
  // keep going.
  init_stack_chk_guard_by_irt_random();
#elif !defined(__native_client__)
#error Either __native_client__ or BARE_METAL_BIONIC must be set.
#endif
}
// ARC MOD END
void __libc_init_common(KernelArgumentBlock& args) {
  // Initialize various globals.
  environ = args.envp;
  errno = 0;
  __libc_auxv = args.auxv;
  __progname = args.argv[0] ? args.argv[0] : "<unknown>";
  __abort_message_ptr = args.abort_message_ptr;

  // AT_RANDOM is a pointer to 16 bytes of randomness on the stack.
  // ARC MOD BEGIN
#if defined(HAVE_ARC)
  init_stack_chk_guard();
  // The least significant byte of the canary must be zero to prevent
  // memory exposure by functions like puts. This is compatible with
  // glibc. See
  // https://sourceware.org/git/?p=glibc.git;a=blob;f=sysdeps/generic/dl-osinfo.h;h=d7667f862dd40a2c2b4d4672cdef7a617f047274;hb=HEAD
#if BYTE_ORDER != LITTLE_ENDIAN
#error "We only support little endian architectures"
#endif
  __stack_chk_guard &= ~0xff;
#else
  // ARC MOD END
  __stack_chk_guard = *reinterpret_cast<uintptr_t*>(getauxval(AT_RANDOM));
  // ARC MOD BEGIN
#endif  // HAVE_ARC
  // ARC MOD END

  // Get the main thread from TLS and add it to the thread list.
  pthread_internal_t* main_thread = __get_thread();
  _pthread_internal_add(main_thread);

  __system_properties_init(); // Requires 'environ'.
  // ARC MOD BEGIN nacl-async-signal
#if !defined(BUILDING_LINKER) && defined(BARE_METAL_BIONIC)
  // Async-signal support is only available in non-SFI mode.
  __nacl_signal_install_handler();
#endif
  // ARC MOD END

  __libc_init_vdso();
}

/* This function will be called during normal program termination
 * to run the destructors that are listed in the .fini_array section
 * of the executable, if any.
 *
 * 'fini_array' points to a list of function addresses. The first
 * entry in the list has value -1, the last one has value 0.
 */
void __libc_fini(void* array) {
  void** fini_array = reinterpret_cast<void**>(array);
  const size_t minus1 = ~(size_t)0; /* ensure proper sign extension */

  /* Sanity check - first entry must be -1 */
  if (array == NULL || (size_t)fini_array[0] != minus1) {
    return;
  }

  /* skip over it */
  fini_array += 1;

  /* Count the number of destructors. */
  int count = 0;
  while (fini_array[count] != NULL) {
    ++count;
  }

  /* Now call each destructor in reverse order. */
  while (count > 0) {
    void (*func)() = (void (*)()) fini_array[--count];

    /* Sanity check, any -1 in the list is ignored */
    if ((size_t)func == minus1) {
      continue;
    }

    func();
  }

#ifndef LIBC_STATIC
  {
    extern void __libc_postfini(void) __attribute__((weak));
    if (__libc_postfini) {
      __libc_postfini();
    }
  }
#endif
}
