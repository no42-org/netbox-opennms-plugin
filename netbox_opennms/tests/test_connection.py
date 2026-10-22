# Copyright 2026 Ronny Trommer <ronny@no42.org>
# SPDX-License-Identifier: MIT
"""Tests for the per-server Test connection action (mocked client, no network)."""

from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import NoReverseMatch, reverse

from netbox_opennms.client import OpenNMSError
from netbox_opennms.tests.servers import make_server

URL = "plugins:netbox_opennms:opennmsserver_test"


class ServerTestConnectionViewTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        user_model = get_user_model()
        cls.user = user_model.objects.create_user(
            username="admin", password="pw", is_superuser=True
        )
        # An authenticated user without view_opennmsserver.
        cls.plain = user_model.objects.create_user(username="plain", password="pw")
        cls.server = make_server()

    def setUp(self):
        self.client.force_login(self.user)

    def _url(self):
        return reverse(URL, args=[self.server.pk])

    def test_requires_permission(self):
        self.client.force_login(self.plain)
        self.assertEqual(self.client.post(self._url()).status_code, 403)

    @mock.patch("netbox_opennms.views.OpenNMSClient.for_server")
    def test_success_flashes_and_redirects(self, mock_for_server):
        client = mock_for_server.return_value.__enter__.return_value
        client.test_connection.return_value = True
        response = self.client.post(self._url())
        self.assertRedirects(
            response, self.server.get_absolute_url(), fetch_redirect_response=False
        )
        mock_for_server.assert_called_once_with(self.server)
        response = self.client.get(self.server.get_absolute_url())
        self.assertContains(response, "OpenNMS connection OK")

    @mock.patch("netbox_opennms.views.OpenNMSClient.for_server")
    def test_failure_flashes_error(self, mock_for_server):
        client = mock_for_server.return_value.__enter__.return_value
        client.test_connection.side_effect = OpenNMSError("unreachable")
        response = self.client.post(self._url(), follow=True)
        self.assertContains(response, "OpenNMS connection failed: unreachable")

    def test_global_connection_page_is_gone(self):
        with self.assertRaises(NoReverseMatch):
            reverse("plugins:netbox_opennms:connection_test")
