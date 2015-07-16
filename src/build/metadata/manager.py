# Copyright 2015 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
#
# Parses metadata definitions in definitions.json

import json
import re

_METADATA_FILE_LOCATION = 'src/build/metadata/definitions.json'


class MetadataDefinition:
  def __init__(self, jsonDict):
    self.name = jsonDict['name']
    self.default_value = jsonDict['defaultValue']
    self._command_argument_name = jsonDict.get('commandArugmentName', None)
    self.allowed_values = jsonDict.get('allowedValues', None)
    self.short_option_name = jsonDict.get('shortOptionName', None)
    self.short_value_mapping = jsonDict.get('shortValueMapping', None)
    self.help = jsonDict.get('help', None)
    self.developer_only = jsonDict.get('developerOnly', False)
    self.external_only = jsonDict.get('externalOnly', False)
    self.no_plugin = jsonDict.get('noPlugin', False)

  @property
  def command_argument_name(self):
    if self._command_argument_name:
      return self._command_argument_name
    else:
      return re.sub('([A-Z])', r'-\1', self.name).lower()

  @property
  def python_name(self):
    return re.sub('-', '_', self.command_argument_name)


def get_metadata_definitions():
  with open(_METADATA_FILE_LOCATION, 'r') as f:
    metadata_json = json.load(f)

  return map(MetadataDefinition, metadata_json)
