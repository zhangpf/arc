/*
 * Copyright (C) 2011 The Android Open Source Project
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
#include "graphics_translation/egl/color_buffer.h"

#include <GLES2/gl2.h>
#include <GLES2/gl2ext.h>

#include "common/alog.h"
#include "common/options.h"
#ifdef ANSI_FB_LOGGING
#include "common/print_image.h"
#endif  // ANSI_FB_LOGGING
#include "graphics_translation/egl/egl_display_impl.h"
#include "graphics_translation/egl/egl_thread_info.h"
#include "graphics_translation/gles/debug.h"
#include "graphics_translation/gles/gles_context.h"
#include "graphics_translation/gralloc/graphics_buffer.h"
#include "system/window.h"

extern "C" {
void* glMapTexSubImage2DCHROMIUM(GLenum target, GLint level,
                                 GLint xoffset, GLint yoffset,
                                 GLsizei width, GLsizei height,
                                 GLenum format, GLenum type, GLenum access);
void  glUnmapTexSubImage2DCHROMIUM(const void* mem);
}

GlesContext* GetCurrentGlesContext();

bool IsValidNativeWindowBuffer(const ANativeWindowBuffer* native_buffer) {
  if (native_buffer == NULL) {
    return false;
  } else if (native_buffer->common.magic != ANDROID_NATIVE_BUFFER_MAGIC) {
    return false;
  } else if (native_buffer->common.version != sizeof(ANativeWindowBuffer)) {
    return false;
  }
  return true;
}

EglImagePtr GetEglImageFromNativeBuffer(GLeglImageOES img) {
  ANativeWindowBuffer* native_buffer =
      reinterpret_cast<ANativeWindowBuffer*>(img);
  if (!IsValidNativeWindowBuffer(native_buffer)) {
    return EglImagePtr();
  }

  const GraphicsBuffer* gb =
    static_cast<const GraphicsBuffer*>(native_buffer->handle);
  if (gb == NULL) {
    return EglImagePtr();
  }

  EglDisplayImpl* display = EglDisplayImpl::GetDefaultDisplay();
  ColorBufferPtr cb = display->GetColorBuffers().Get(gb->GetHostHandle());
  if (!cb) {
    return EglImagePtr();
  }
  return cb->GetImage();
}

ColorBufferHandle ColorBuffer::Create(EGLDisplay dpy, GLuint width,
                                      GLuint height, GLenum format,
                                      GLenum type, bool sw_write) {
  LOG_ALWAYS_FATAL_IF(
      format != GL_RGB && format != GL_RGBA && format != GL_ALPHA,
      "format(%s) is not supported!", GetEnumString(format));
  LOG_ALWAYS_FATAL_IF(type != GL_UNSIGNED_BYTE &&
                      type != GL_UNSIGNED_SHORT_5_6_5 &&
                      type != GL_UNSIGNED_SHORT_5_5_5_1 &&
                      type != GL_UNSIGNED_SHORT_4_4_4_4,
                      "type(%s) is not supported!", GetEnumString(type));
  EglDisplayImpl* d = EglDisplayImpl::GetDisplay(dpy);

  ColorBuffer* cb = NULL;
  if (d && d->Lock()) {
    cb = new ColorBuffer(dpy, width, height, format, type, sw_write);
    d->Unlock();
  }

  ColorBufferPtr ptr(cb);
  return d->GetColorBuffers().Register(ptr);
}

ColorBuffer::ColorBuffer(EGLDisplay dpy, GLuint width, GLuint height,
                         GLenum format, GLenum type, bool sw_write)
  : display_(dpy),
    key_(0),
    width_(width),
    height_(height),
    format_(format),
    type_(type),
    sw_write_(sw_write),
    texture_(0),
    global_texture_(0),
    image_(0),
    locked_mem_(NULL),
    host_context_(NULL),
    refcount_(1) {
  EglDisplayImpl* d = EglDisplayImpl::GetDisplay(dpy);
  key_ = d->GetColorBuffers().GenerateKey();

  glGenTextures(1, &texture_);
  glBindTexture(GL_TEXTURE_2D, texture_);
  glTexImage2D(GL_TEXTURE_2D, 0, format, width, height, 0, format,
               type, NULL);
  glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR);
  glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR);
  glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE);
  glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE);

  GlesContext* c = GetCurrentGlesContext();
  global_texture_ = c->GetShareGroup()->GetTextureGlobalName(texture_);
  image_ = EglImage::Create(GL_TEXTURE_2D, texture_);
  LOG_ALWAYS_FATAL_IF(!image_, "Could not create draw Image.");
}

ColorBuffer::~ColorBuffer() {
  EglDisplayImpl* d = EglDisplayImpl::GetDisplay(display_);
  d->Lock();
  glDeleteTextures(1, &texture_);
  d->Unlock();
}

void* ColorBuffer::Lock(GLint xoffset, GLint yoffset, GLsizei width,
                        GLsizei height, GLenum format, GLenum type) {
  LOG_ALWAYS_FATAL_IF(!sw_write_,
                      "Try to lock a hardware render color buffer.");

  EglDisplayImpl* d = EglDisplayImpl::GetDisplay(display_);
  if (d->Lock()) {
    if (locked_mem_) {
      ALOGE("Try locking a locked ColorBuffer.");
      d->Unlock();
      return NULL;
    }
    LOG_ALWAYS_FATAL_IF(format != format_,
                        "format(%s) != format_(%s)",
                        GetEnumString(format), GetEnumString(format_));
    LOG_ALWAYS_FATAL_IF(type != type_, "type(%s) != type_(%s)",
                        GetEnumString(type), GetEnumString(type_));
    glBindTexture(GL_TEXTURE_2D, texture_);
    glPixelStorei(GL_UNPACK_ALIGNMENT, 1);
    locked_mem_ = glMapTexSubImage2DCHROMIUM(GL_TEXTURE_2D, 0, xoffset, yoffset,
                                             width, height, format, type,
                                             GL_WRITE_ONLY_OES);
    d->Unlock();
  }
  return locked_mem_;
}

void ColorBuffer::Unlock(const void* mem) {
  EglDisplayImpl* d = EglDisplayImpl::GetDisplay(display_);
  if (d->Lock()) {
    if (!locked_mem_) {
      ALOGE("Try unlocking an unlocked ColorBuffer.");
      d->Unlock();
      return;
    }
    if (locked_mem_ != mem) {
      ALOGE("Try unlocking a ColorBuffer with an invalid mem.");
      d->Unlock();
      return;
    }
    glBindTexture(GL_TEXTURE_2D, texture_);
    glPixelStorei(GL_UNPACK_ALIGNMENT, 1);
    glUnmapTexSubImage2DCHROMIUM(locked_mem_);
    locked_mem_ = NULL;
    d->Unlock();
  }
}

void ColorBuffer::Render() {
  EglDisplayImpl* d = EglDisplayImpl::GetDisplay(display_);
  if (d->Lock()) {
    glViewport(0, 0, width_, height_);
    d->DrawFullscreenQuadLocked(texture_, sw_write_);

#ifdef ANSI_FB_LOGGING
    void* pixels = malloc(width_ * height_ * 4);
    fprintf(stderr, "\e[1;1H");
    glReadPixels(0, 0, width_, height_, GL_RGBA, GL_UNSIGNED_BYTE, pixels);
    arc::PrintImage(stderr, pixels, width_, height_, true);
    free(pixels);
#endif  // ANSI_FB_LOGGING

    d->SwapBuffersLocked();
    d->Unlock();
  }
}

void ColorBuffer::BindToTexture() {
  ContextPtr c = EglThreadInfo::GetInstance().GetCurrentContext();
  if (c) {
    c->BindImageToTexture(image_);
  }
}

void ColorBuffer::Commit() {
  LOG_ALWAYS_FATAL_IF(sw_write_,
                      "Commit() is called for a SW write color buffer.");
  // We do not need flush GL context when compositor is enabled, because
  // the Pepper Compositor API uses CHROMIUM_sync_point extension to sync
  // between GL contexts.
  if (!arc::Options::GetInstance()->enable_compositor) {
    glFlush();
  }
}

void ColorBuffer::BindHostContext(void* host_context) {
  LOG_ALWAYS_FATAL_IF(sw_write_, "Bind a context to a SW write color buffer.");

  if (host_context) {
    host_context_ = host_context;
  }
}

uint32_t ColorBuffer::Acquire() {
  ++refcount_;
  return refcount_;
}

uint32_t ColorBuffer::Release() {
  --refcount_;
  if (refcount_ == 0) {
    EglDisplayImpl* d = EglDisplayImpl::GetDisplay(display_);
    d->GetColorBuffers().Unregister(key_);
  }
  return refcount_;
}