# Copyright 2014 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

import glob
import os

from src.build import build_common
from src.build import staging


class Notices(object):
  """Class that keeps track of Android-style notice and license files.

  Notice files are named NOTICE and have the text of notices in them.  These
  notices are required to be shown in the final product for proper compliance.
  License files are named MODULE_LICENSE_* and their existence demarks a
  directory tree published under the given license."""
  _license_kinds = {}
  # Maps from (relative directory path, file spec) to path that contains
  # that file or None if no parent does.
  _parent_cache = {}

  KIND_PUBLIC_DOMAIN = 'public domain'
  KIND_NOTICE = 'notice'
  KIND_OPEN_SOURCE_REQUIRED = 'requires open source'
  KIND_LGPL_LIKE = 'lgpl-like'
  KIND_GPL_LIKE = 'gpl-like'
  KIND_TODO = 'todo'
  KIND_UNKNOWN = 'unknown'
  KIND_DEFAULT = KIND_NOTICE

  _PER_FILE_LICENSE_KINDS = dict([
      # This file has a modified GPL license header indicating it can be
      # part of a binary distribution without disclosing all sources..
      (staging.as_staging('android/external/gcc-demangle/cp-demangle.c'),
       KIND_NOTICE),
      # TODO(crbug.com/380032): Once we can add licensing information
      # directly in the directory itself, we can remove these per-file
      # license hacks.
      ('third_party/chromium-ppapi/base/atomicops_internals_gcc.h',
       KIND_PUBLIC_DOMAIN),
      # event-log-tags is generated by filtering a file created by an
      # Apache2-licensed script.
      ('canned/target/android/generated/event-log-tags',
       KIND_NOTICE)
  ])

  # Provide a partial ordering of all license kinds based on how
  # "restrictive" they are.  On one end a public domain (non-)license
  # indicates not even a notice of the software is required.  On the other
  # end, GPL licensing requires everything in the process to be open
  # sourced.
  _KIND_RESTRICTIVENESS = {KIND_UNKNOWN: 5,
                           KIND_TODO: 5,
                           KIND_GPL_LIKE: 4,
                           KIND_LGPL_LIKE: 3,
                           KIND_OPEN_SOURCE_REQUIRED: 2,
                           KIND_NOTICE: 1,
                           KIND_PUBLIC_DOMAIN: 0}

  def __init__(self):
    self._license_roots = set()
    # Map from a license root to an example file that required it.  This is
    # helpful for diagnosing licensing violation errors.
    self._license_roots_examples = {}
    self._notice_roots = set()

  def _find_parent_file(self, start_path, filespec):
    """Find a file in start_path or a parent matching filespec.

    This function is purposefully not staging aware.  It will only look in
    the actual file system path provided."""
    if start_path == '':
      return None
    if (start_path, filespec) in self._parent_cache:
      return self._parent_cache[start_path, filespec]
    if (('*' in filespec and glob.glob(os.path.join(start_path, filespec))) or
        os.path.exists(os.path.join(start_path, filespec))):
      self._parent_cache[start_path, filespec] = start_path
      return start_path
    parent_result = self._find_parent_file(os.path.dirname(start_path),
                                           filespec)
    self._parent_cache[start_path, filespec] = parent_result
    return parent_result

  def add_sources(self, files):
    for f in files:
      if os.path.isabs(f):
        f = os.path.relpath(f, build_common.get_arc_root())
      notice_root = self._find_parent_file(os.path.dirname(f), 'NOTICE')
      if notice_root:
        self._notice_roots.add(notice_root)
      if f in self._PER_FILE_LICENSE_KINDS:
        license_root = f
      else:
        license_root = self._find_parent_file(os.path.dirname(f),
                                              'MODULE_LICENSE_*')
      if license_root:
        if license_root not in self._license_roots:
          self._license_roots.add(license_root)
          self._license_roots_examples[license_root] = f

  def has_proper_metadata(self):
    return bool(self._notice_roots) or bool(self._license_roots)

  @staticmethod
  def _get_license_kind_by_path(path):
    license_file = os.path.basename(path)
    if license_file.startswith('MODULE_LICENSE_'):
      license = '_'.join(license_file.split('_')[2:])
      if license in ['GPL']:
        return Notices.KIND_GPL_LIKE
      if license in ['LGPL']:
        return Notices.KIND_LGPL_LIKE
      if license in ['CCSA', 'CPL', 'FRAUNHOFER', 'MPL']:
        return Notices.KIND_OPEN_SOURCE_REQUIRED
      # If the author lets us choose between a notice and more restrictive
      # license, we pick notice.
      if license in ['APACHE2', 'BSD', 'BSD_LIKE', 'BSD_OR_LGPL', 'MIT', 'W3C']:
        return Notices.KIND_NOTICE
      if license == 'TODO':
        return Notices.KIND_TODO
      if license == 'PUBLIC_DOMAIN':
        return Notices.KIND_PUBLIC_DOMAIN
    else:
      raise Exception('Wrong file passed: %s' % path)
    # If we do not recognize it, assume the worst.
    print 'WARNING: Unrecognized license: ' + path
    return Notices.KIND_UNKNOWN

  @staticmethod
  def is_more_restrictive(a, b):
    return Notices._KIND_RESTRICTIVENESS[a] > Notices._KIND_RESTRICTIVENESS[b]

  @staticmethod
  def get_license_kind(path):
    if path not in Notices._license_kinds:
      license_filenames = glob.glob(os.path.join(path, 'MODULE_LICENSE_*'))
      most_restrictive = None
      for license_filename in license_filenames:
        kind = Notices._get_license_kind_by_path(license_filename)
        if (not most_restrictive or
            Notices.is_more_restrictive(kind, most_restrictive)):
          most_restrictive = kind
      if path in Notices._PER_FILE_LICENSE_KINDS:
        Notices._license_kinds[path] = Notices._PER_FILE_LICENSE_KINDS[path]
      elif most_restrictive is None:
        Notices._license_kinds[path] = Notices.KIND_DEFAULT
      else:
        Notices._license_kinds[path] = most_restrictive
    return Notices._license_kinds[path]

  def get_most_restrictive_license_kind(self):
    most_restrictive = Notices.KIND_PUBLIC_DOMAIN
    for p in self._license_roots:
      kind = self.get_license_kind(p)
      if Notices.is_more_restrictive(kind, most_restrictive):
        most_restrictive = kind
    return most_restrictive

  def has_lgpl_or_gpl(self):
    return Notices.is_more_restrictive(self.get_most_restrictive_license_kind(),
                                       Notices.KIND_OPEN_SOURCE_REQUIRED)

  def does_license_force_source_with_binary_distribution(self, path):
    return self.is_more_restrictive(self.get_license_kind(path),
                                    self.KIND_NOTICE)

  def get_gpl_roots(self):
    return [p for p in sorted(self._license_roots)
            if self.get_license_kind(p) == Notices.KIND_GPL_LIKE]

  def get_source_required_roots(self):
    source_required_roots = set(
        [p for p in sorted(self._license_roots)
         if self.does_license_force_source_with_binary_distribution(p)])
    return source_required_roots

  def get_source_required_examples(self):
    return [self.get_license_root_example(r)
            for r in self.get_source_required_roots()]

  def add_notices(self, notices):
    self._license_roots.update(notices._license_roots)
    self._license_roots_examples.update(notices._license_roots_examples)
    self._notice_roots.update(notices._notice_roots)

  def get_notice_roots(self):
    return self._notice_roots

  def get_license_roots(self):
    return self._license_roots

  def get_license_root_example(self, root):
    return self._license_roots_examples[root]

  def get_notice_files(self):
    notice_files = set()
    for p in self._notice_roots:
      notice_file = os.path.join(p, 'NOTICE')
      if os.path.exists(notice_file):
        notice_files.add(notice_file)
    return notice_files

  def __repr__(self):
    output = ['notice.Notices object:']
    output.append('  notices: ' + ', '.join(self.get_notice_files()))
    for r in self.get_license_roots():
      output.append('  License kind "%s" in %s: example file %s' %
                    (Notices.get_license_kind(r), r,
                     self.get_license_root_example(r)))
    return '\n'.join(output)
