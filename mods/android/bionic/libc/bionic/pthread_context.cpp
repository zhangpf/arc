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
//
// Saves and clears register context on the current thread.
// For use with blocking IRT calls.

#include <errno.h>
#include <pthread.h>

#include "private/bionic_atomic_inline.h"
#include "private/pthread_context.h"
#include "pthread_internal.h"

extern "C" {

void __pthread_save_context_regs(void* regs, int size) {
    pthread_internal_t* thread = __get_thread();
    memcpy(thread->context_regs, regs, size);
    thread->has_context_regs = 1;
    ANDROID_MEMBAR_FULL();
}

void __pthread_clear_context_regs() {
    pthread_internal_t* thread = __get_thread();
    thread->has_context_regs = 0;
    ANDROID_MEMBAR_FULL();
}

static bool obtain_lock(bool try_lock) {
    if (try_lock) {
        // Ideally, we could also check that the mutex is async-safe:
        //   ((g_thread_list_lock & MUTEX_TYPE_MASK) == MUTEX_TYPE_BITS_NORMAL)
        if (pthread_mutex_trylock(&g_thread_list_lock))
            return false;
    } else {
        pthread_mutex_lock(&g_thread_list_lock);
    }
    return true;
}

int __pthread_get_thread_count(bool try_lock) {
    if (!obtain_lock(try_lock))
        return -1;

    int count = 0;
    pthread_internal_t* thread = g_thread_list;
    while (thread) {
        ++count;
        thread = thread->next;
    }

    pthread_mutex_unlock(&g_thread_list_lock);
    return count;
}

static void copy_thread_info(__pthread_context_info_t* dst,
                             const pthread_internal_t* src) {
  dst->stack_base = NULL;
  dst->has_context_regs = 0;

  // Copy the stack boundaries.
  if (src->attr.stack_base) {
    dst->stack_base =
        reinterpret_cast<char*>(src->attr.stack_base) +
        src->attr.guard_size;
    dst->stack_size = src->attr.stack_size - src->attr.guard_size;
  }

  // Copy registers, then do a second (racy) read of has_context_regs.
  if (src->has_context_regs) {
    memcpy(dst->context_regs, src->context_regs,
           sizeof(dst->context_regs));
    ANDROID_MEMBAR_FULL();
    dst->has_context_regs = src->has_context_regs;
  }
}

void __pthread_get_current_thread_info(__pthread_context_info_t* info) {
  pthread_internal_t* cur_thread = __get_thread();
  copy_thread_info(info, cur_thread);
}

int __pthread_get_thread_infos(
        bool try_lock, bool include_current,
        int max_info_count, __pthread_context_info_t* infos) {
    if (!obtain_lock(try_lock))
        return -1;

    int idx = 0;
    pthread_internal_t* cur_thread = __get_thread();
    for (pthread_internal_t* thread = g_thread_list;
         thread && idx < max_info_count; thread = thread->next) {
        if (!include_current && thread == cur_thread)
            continue;

        copy_thread_info(infos + idx, thread);
        if (infos[idx].stack_base)
          ++idx;
    }

    pthread_mutex_unlock(&g_thread_list_lock);
    return idx;
}

}  // extern "C"
