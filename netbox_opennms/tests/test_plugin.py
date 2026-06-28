# Copyright 2026 Ronny Trommer <ronny@no42.org>
# SPDX-License-Identifier: MIT
"""Smoke tests for plugin loading and configuration defaults.

Run inside a configured NetBox environment, e.g.::

    ./manage.py test netbox_opennms

These assert the plugin registers and that its PLUGINS_CONFIG defaults
resolve via ``get_plugin_config``.
"""

from django.db import models
from django.test import SimpleTestCase
from netbox.plugins import get_plugin_config

from netbox_opennms import NetBoxOpenNMSConfig, __version__


class PluginConfigTestCase(SimpleTestCase):
    def test_plugin_metadata(self):
        self.assertEqual(NetBoxOpenNMSConfig.name, "netbox_opennms")
        self.assertEqual(NetBoxOpenNMSConfig.base_url, "opennms")
        self.assertEqual(NetBoxOpenNMSConfig.version, __version__)

    def test_min_version_pinned_to_46(self):
        # 4.6.1 floor — the no-worker warning uses any_workers_for_queue (added
        # in 4.6.1; absent in 4.6.0).
        self.assertEqual(NetBoxOpenNMSConfig.min_version, "4.6.1")

    def test_config_defaults_resolve(self):
        self.assertEqual(get_plugin_config("netbox_opennms", "import_mode"), "false")
        for key in (
            "opennms_url",
            "opennms_username",
            "opennms_password",
            "default_location",
        ):
            self.assertEqual(get_plugin_config("netbox_opennms", key), "")

    def test_models_module_present(self):
        # Story 1.2 introduced the first model.
        from netbox_opennms.models import MonitoringProfile

        self.assertTrue(issubclass(MonitoringProfile, models.Model))
