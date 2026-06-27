# Copyright 2026 Ronny Trommer <ronny@no42.org>
# SPDX-License-Identifier: Apache-2.0
"""Tests for the connection-test UI action (mocked client, no network)."""

from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from netbox_opennms.client import OpenNMSError

URL = "plugins:netbox_opennms:connection_test"


class ConnectionTestViewTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(username="admin", password="pw")

    def setUp(self):
        self.client.force_login(self.user)

    def test_get_renders_page(self):
        self.assertEqual(self.client.get(reverse(URL)).status_code, 200)

    def test_anonymous_is_redirected(self):
        self.client.logout()
        self.assertIn(self.client.get(reverse(URL)).status_code, (302, 403))

    @mock.patch("netbox_opennms.views.OpenNMSClient.from_config")
    def test_post_success_message(self, mock_from_config):
        # The view uses the client as a context manager (with ... as client).
        client = mock_from_config.return_value.__enter__.return_value
        client.test_connection.return_value = True
        response = self.client.post(reverse(URL), follow=True)
        self.assertContains(response, "OpenNMS connection OK")

    @mock.patch("netbox_opennms.views.OpenNMSClient.from_config")
    def test_post_failure_message(self, mock_from_config):
        client = mock_from_config.return_value.__enter__.return_value
        client.test_connection.side_effect = OpenNMSError("unreachable")
        response = self.client.post(reverse(URL), follow=True)
        self.assertContains(response, "OpenNMS connection failed")
