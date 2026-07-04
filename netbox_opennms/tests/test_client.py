# Copyright 2026 Ronny Trommer <ronny@no42.org>
# SPDX-License-Identifier: MIT
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
    def test_import_requisition_builds_path_and_passes_rescan(self, mock_request):
        # 202 ACCEPTED is the real OpenNMS import response — it must be success.
        mock_request.return_value = mock.Mock(status_code=202, ok=True)
        _client().import_requisition("netbox.raleigh.router", rescan_existing="false")
        method, url = mock_request.call_args.args
        self.assertEqual(method, "PUT")
        # The Foreign Source is URL-quoted into the path (dots are URL-safe).
        self.assertEqual(
            url,
            "https://onms.example/opennms/rest/requisitions/"
            "netbox.raleigh.router/import",
        )
        self.assertEqual(
            mock_request.call_args.kwargs["params"], {"rescanExisting": "false"}
        )

    @mock.patch.object(requests.Session, "request")
    def test_import_requisition_encodes_unsafe_chars(self, mock_request):
        # The client still percent-encodes any unsafe char it's handed (defensive).
        mock_request.return_value = mock.Mock(status_code=202, ok=True)
        _client().import_requisition("a/b c", rescan_existing="false")
        _, url = mock_request.call_args.args
        self.assertTrue(url.endswith("/rest/requisitions/a%2Fb%20c/import"))

    @mock.patch.object(requests.Session, "request")
    def test_list_locations_parses_dict_form(self, mock_request):
        mock_request.return_value = mock.Mock(
            status_code=200,
            ok=True,
            json=mock.Mock(
                return_value={
                    "location": [
                        {"location-name": "Default"},
                        {"id": "edge-1"},  # id fallback
                        {"name": "edge-2"},  # name fallback
                        {"location-name": ""},  # empty dropped
                        "not-a-dict",  # ignored
                    ]
                }
            ),
        )
        self.assertEqual(
            _client().list_locations(), {"Default", "edge-1", "edge-2"}
        )
        method, url = mock_request.call_args.args
        self.assertEqual(method, "GET")
        self.assertEqual(
            url, "https://onms.example/opennms/api/v2/monitoringLocations"
        )
        self.assertEqual(mock_request.call_args.kwargs["params"], {"limit": 0})

    @mock.patch.object(requests.Session, "request")
    def test_list_locations_parses_bare_list(self, mock_request):
        mock_request.return_value = mock.Mock(
            status_code=200,
            ok=True,
            json=mock.Mock(return_value=[{"location-name": "Default"}]),
        )
        self.assertEqual(_client().list_locations(), {"Default"})

    @mock.patch.object(requests.Session, "request")
    def test_list_locations_http_error_raises(self, mock_request):
        mock_request.return_value = mock.Mock(status_code=500, ok=False)
        with self.assertRaises(OpenNMSError):
            _client().list_locations()

    @mock.patch.object(requests.Session, "request")
    def test_list_locations_non_json_body_raises_opennms_error(self, mock_request):
        # A 2xx with a non-JSON body (proxy/login HTML) must surface as the typed
        # OpenNMSError so best-effort callers degrade — not a bare ValueError.
        mock_request.return_value = mock.Mock(
            status_code=200,
            ok=True,
            json=mock.Mock(side_effect=ValueError("no json")),
        )
        with self.assertRaises(OpenNMSError):
            _client().list_locations()

    @mock.patch.object(requests.Session, "request")
    def test_list_locations_json_scalar_raises_opennms_error(self, mock_request):
        # A JSON scalar (null/number/string) must not escape as AttributeError.
        mock_request.return_value = mock.Mock(
            status_code=200, ok=True, json=mock.Mock(return_value=None)
        )
        with self.assertRaises(OpenNMSError):
            _client().list_locations()

    @mock.patch.object(requests.Session, "request")
    def test_list_requisition_names(self, mock_request):
        mock_request.return_value = mock.Mock(
            status_code=200,
            ok=True,
            json=mock.Mock(
                return_value={
                    "model-import": [
                        {"foreign-source": "netbox.raleigh.router"},
                        {"foreign-source": "netbox.durham.router"},
                        {"no-foreign-source": "ignored"},
                    ]
                }
            ),
        )
        self.assertEqual(
            _client().list_requisition_names(),
            {"netbox.raleigh.router", "netbox.durham.router"},
        )

    @mock.patch.object(requests.Session, "request")
    def test_list_requisition_names_unparseable_raises(self, mock_request):
        mock_request.return_value = mock.Mock(
            status_code=200, ok=True, json=mock.Mock(side_effect=ValueError("x"))
        )
        with self.assertRaises(OpenNMSError):
            _client().list_requisition_names()

    @mock.patch.object(requests.Session, "request")
    def test_delete_requisition_removes_deployed_then_pending(self, mock_request):
        mock_request.return_value = mock.Mock(status_code=202, ok=True)
        _client().delete_requisition("netbox.x.y")
        urls = [c.args[1] for c in mock_request.call_args_list]
        methods = {c.args[0] for c in mock_request.call_args_list}
        self.assertEqual(methods, {"DELETE"})
        # Deployed copy first (it's what GET /rest/requisitions lists), then pending.
        self.assertTrue(urls[0].endswith("/rest/requisitions/deployed/netbox.x.y"))
        self.assertTrue(urls[1].endswith("/rest/requisitions/netbox.x.y"))

    @mock.patch.object(requests.Session, "request")
    def test_delete_foreign_source(self, mock_request):
        mock_request.return_value = mock.Mock(status_code=202, ok=True)
        _client().delete_foreign_source("netbox.x.y")
        method, url = mock_request.call_args.args
        self.assertEqual(method, "DELETE")
        self.assertTrue(url.endswith("/rest/foreignSources/netbox.x.y"))

    @mock.patch.object(requests.Session, "request")
    def test_list_detectors_parses_wrapped_plugins(self, mock_request):
        mock_request.return_value = mock.Mock(
            status_code=200,
            ok=True,
            json=mock.Mock(
                return_value={
                    "plugins": {
                        "plugin": [
                            {
                                "name": "ICMP",
                                "class": "org.opennms.IcmpDetector",
                                "parameters": {
                                    "parameter": [
                                        {"key": "timeout", "required": False},
                                        {"key": "retries", "required": False},
                                    ]
                                },
                            },
                            {"no-class": "dropped"},  # no class → dropped
                        ]
                    }
                }
            ),
        )
        plugins = _client().list_detectors()
        method, url = mock_request.call_args.args
        self.assertEqual(method, "GET")
        self.assertEqual(
            url,
            "https://onms.example/opennms/rest/foreignSourcesConfig/detectors",
        )
        self.assertEqual(len(plugins), 1)
        self.assertEqual(plugins[0].name, "ICMP")
        self.assertEqual(plugins[0].plugin_class, "org.opennms.IcmpDetector")
        self.assertEqual([p.key for p in plugins[0].parameters], ["timeout", "retries"])

    @mock.patch.object(requests.Session, "request")
    def test_list_policies_parses_enum_options_and_required(self, mock_request):
        mock_request.return_value = mock.Mock(
            status_code=200,
            ok=True,
            json=mock.Mock(
                return_value={
                    "plugin": [  # bare "plugin" wrapper, single object collapses too
                        {
                            "name": "Match IP",
                            "class": "org.opennms.MatchingIpInterfacePolicy",
                            "parameters": {
                                "parameter": [
                                    {
                                        "key": "action",
                                        "required": True,
                                        "options": {
                                            "option": ["DO_NOT_PERSIST", "UNMANAGE"]
                                        },
                                    }
                                ]
                            },
                        }
                    ]
                }
            ),
        )
        plugins = _client().list_policies()
        _, url = mock_request.call_args.args
        self.assertTrue(url.endswith("/rest/foreignSourcesConfig/policies"))
        self.assertEqual(len(plugins), 1)
        action = plugins[0].parameters[0]
        self.assertEqual(action.key, "action")
        self.assertTrue(action.required)
        self.assertEqual(action.options, ("DO_NOT_PERSIST", "UNMANAGE"))

    @mock.patch.object(requests.Session, "request")
    def test_list_detectors_required_string_false(self, mock_request):
        # A JSON string "false" for `required` must parse as False, not bool("false").
        mock_request.return_value = mock.Mock(
            status_code=200,
            ok=True,
            json=mock.Mock(
                return_value={
                    "plugin": [
                        {
                            "name": "X",
                            "class": "org.X",
                            "parameters": {
                                "parameter": [{"key": "k", "required": "false"}]
                            },
                        }
                    ]
                }
            ),
        )
        plugins = _client().list_detectors()
        self.assertFalse(plugins[0].parameters[0].required)

    @mock.patch.object(requests.Session, "request")
    def test_list_detectors_unparseable_raises(self, mock_request):
        mock_request.return_value = mock.Mock(
            status_code=200, ok=True, json=mock.Mock(side_effect=ValueError("x"))
        )
        with self.assertRaises(OpenNMSError):
            _client().list_detectors()

    @mock.patch.object(requests.Session, "request")
    def test_list_assets_parses_elementlist(self, mock_request):
        mock_request.return_value = mock.Mock(
            status_code=200,
            ok=True,
            json=mock.Mock(
                return_value={"count": 2, "element": ["serialNumber", "assetNumber"]}
            ),
        )
        self.assertEqual(
            _client().list_assets(), {"serialNumber", "assetNumber"}
        )
        _, url = mock_request.call_args.args
        self.assertTrue(url.endswith("/rest/foreignSourcesConfig/assets"))

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
