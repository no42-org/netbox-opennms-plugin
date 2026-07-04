# Copyright 2026 Ronny Trommer <ronny@no42.org>
# SPDX-License-Identifier: MIT
"""Tests for the Connect OpenNMS UI action (mocked client, no network).

Option D: the page verifies the connection configured in PLUGINS_CONFIG. It is
permission-gated, accepts no user-supplied URL/credentials, shows the effective
URL/username read-only (never the password), and persists nothing.
"""

from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from netbox_opennms.client import OpenNMSError

URL = "plugins:netbox_opennms:connection_test"


def _config(secrets):
    """A get_plugin_config side-effect returning *secrets* by key."""
    return lambda name, key: secrets.get(key, "")


class ConnectionTestViewTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        user_model = get_user_model()
        # Superuser passes the view_requisition permission gate.
        cls.user = user_model.objects.create_user(
            username="admin", password="pw", is_superuser=True
        )
        # An authenticated user without the plugin permission.
        cls.plain = user_model.objects.create_user(username="plain", password="pw")

    def setUp(self):
        self.client.force_login(self.user)

    def test_get_renders_page(self):
        response = self.client.get(reverse(URL))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Connect OpenNMS")

    def test_anonymous_is_redirected(self):
        self.client.logout()
        self.assertIn(self.client.get(reverse(URL)).status_code, (302, 403))

    def test_requires_permission(self):
        self.client.force_login(self.plain)
        self.assertEqual(self.client.get(reverse(URL)).status_code, 403)

    @mock.patch("netbox_opennms.views.get_plugin_config")
    def test_get_shows_configured_url_username_not_password(self, mock_cfg):
        mock_cfg.side_effect = _config(
            {
                "opennms_url": "https://onms.example.org/opennms",
                "opennms_username": "provision-svc",
                "opennms_password": "SUPER-SECRET",
            }
        )
        response = self.client.get(reverse(URL))
        self.assertContains(response, "https://onms.example.org/opennms")
        self.assertContains(response, "provision-svc")
        # The password is reported as configured, never rendered.
        self.assertContains(response, "Configured")
        self.assertNotContains(response, "SUPER-SECRET")

    @mock.patch("netbox_opennms.views.OpenNMSClient.from_config")
    def test_post_probes_configured_connection_success(self, mock_from_config):
        client = mock_from_config.return_value.__enter__.return_value
        client.test_connection.return_value = True
        response = self.client.post(reverse(URL), follow=True)
        self.assertContains(response, "OpenNMS connection OK")
        # Always tests the configured connection — no user-supplied values.
        mock_from_config.assert_called_once_with()

    @mock.patch("netbox_opennms.views.OpenNMSClient.from_config")
    def test_post_failure_message(self, mock_from_config):
        client = mock_from_config.return_value.__enter__.return_value
        client.test_connection.side_effect = OpenNMSError("unreachable")
        response = self.client.post(reverse(URL), follow=True)
        self.assertContains(response, "OpenNMS connection failed")
