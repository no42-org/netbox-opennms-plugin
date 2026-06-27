# Copyright 2026 Ronny Trommer <ronny@no42.org>
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for the OpenNMS REST client (mocked HTTP, no network)."""

from unittest import mock

import requests
from django.test import SimpleTestCase

from netbox_opennms.client import (
    OpenNMSAuthError,
    OpenNMSClient,
    OpenNMSError,
    OpenNMSHTTPError,
    OpenNMSTransportError,
)


def _client():
    return OpenNMSClient(
        base_url="https://onms.example/opennms/",
        username="svc",
        password="secret",
    )


class OpenNMSClientTest(SimpleTestCase):
    @mock.patch.object(requests.Session, "request")
    def test_connection_success(self, mock_request):
        mock_request.return_value = mock.Mock(status_code=200, ok=True)
        self.assertTrue(_client().test_connection())
        method, url = mock_request.call_args.args
        self.assertEqual(method, "GET")
        # trailing slash stripped; /rest path appended to the /opennms base.
        self.assertEqual(url, "https://onms.example/opennms/rest/requisitions")
        self.assertIn("timeout", mock_request.call_args.kwargs)

    @mock.patch.object(requests.Session, "request")
    def test_auth_error(self, mock_request):
        mock_request.return_value = mock.Mock(status_code=401, ok=False)
        with self.assertRaises(OpenNMSAuthError):
            _client().test_connection()

    @mock.patch.object(requests.Session, "request")
    def test_http_error_carries_status(self, mock_request):
        mock_request.return_value = mock.Mock(status_code=500, ok=False)
        with self.assertRaises(OpenNMSHTTPError) as ctx:
            _client().test_connection()
        self.assertEqual(ctx.exception.status_code, 500)

    @mock.patch.object(
        requests.Session, "request", side_effect=requests.ConnectionError("boom")
    )
    def test_transport_error(self, _mock_request):
        with self.assertRaises(OpenNMSTransportError):
            _client().test_connection()

    @mock.patch.object(
        requests.Session, "request", side_effect=requests.Timeout("slow")
    )
    def test_timeout_is_transport_error(self, _mock_request):
        with self.assertRaises(OpenNMSTransportError):
            _client().test_connection()

    @mock.patch.object(requests.Session, "request")
    def test_redirect_is_not_success(self, mock_request):
        mock_request.return_value = mock.Mock(status_code=302, ok=True)
        with self.assertRaises(OpenNMSHTTPError):
            _client().test_connection()
        # redirects are not followed silently
        self.assertFalse(mock_request.call_args.kwargs["allow_redirects"])

    @mock.patch.object(requests.Session, "request")
    def test_post_foreign_source(self, mock_request):
        mock_request.return_value = mock.Mock(status_code=200, ok=True)
        _client().post_foreign_source(b"<foreign-source/>")
        method, url = mock_request.call_args.args
        self.assertEqual(method, "POST")
        self.assertEqual(url, "https://onms.example/opennms/rest/foreignSources")
        self.assertEqual(mock_request.call_args.kwargs["data"], b"<foreign-source/>")
        self.assertEqual(
            mock_request.call_args.kwargs["headers"]["Content-Type"],
            "application/xml",
        )

    @mock.patch.object(requests.Session, "request")
    def test_post_requisition(self, mock_request):
        mock_request.return_value = mock.Mock(status_code=200, ok=True)
        _client().post_requisition(b"<model-import/>")
        method, url = mock_request.call_args.args
        self.assertEqual(method, "POST")
        self.assertEqual(url, "https://onms.example/opennms/rest/requisitions")
        self.assertEqual(mock_request.call_args.kwargs["data"], b"<model-import/>")

    @mock.patch.object(requests.Session, "request")
    def test_import_requisition_encodes_fs_and_passes_rescan(self, mock_request):
        # 202 ACCEPTED is the real OpenNMS import response — it must be success.
        mock_request.return_value = mock.Mock(status_code=202, ok=True)
        _client().import_requisition("netbox:raleigh:router", rescan_existing="false")
        method, url = mock_request.call_args.args
        self.assertEqual(method, "PUT")
        # ':' in the Foreign Source name is percent-encoded in the path.
        self.assertEqual(
            url,
            "https://onms.example/opennms/rest/requisitions/"
            "netbox%3Araleigh%3Arouter/import",
        )
        self.assertEqual(
            mock_request.call_args.kwargs["params"], {"rescanExisting": "false"}
        )

    def test_from_config_requires_url(self):
        with mock.patch(
            "netbox_opennms.client.client.get_plugin_config", return_value=""
        ):
            with self.assertRaises(OpenNMSError):
                OpenNMSClient.from_config()

    def test_from_config_requires_credentials(self):
        def fake(_plugin, key):
            return "https://onms.example/opennms" if key == "opennms_url" else ""

        with mock.patch(
            "netbox_opennms.client.client.get_plugin_config", side_effect=fake
        ):
            with self.assertRaises(OpenNMSError):
                OpenNMSClient.from_config()
