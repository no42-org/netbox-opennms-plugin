# Copyright 2026 Ronny Trommer <ronny@no42.org>
# SPDX-License-Identifier: Apache-2.0
"""OpenNMS REST client — the single port for all OpenNMS I/O (AD-2).

This story implements connection plumbing + ``test_connection``. The port grows
as later stories add ``post_foreign_source`` / ``post_requisition`` /
``import_requisition`` / ``list_locations``.
"""

import logging

import requests
from netbox.plugins import get_plugin_config
from requests.auth import HTTPBasicAuth

from .errors import (
    OpenNMSAuthError,
    OpenNMSError,
    OpenNMSHTTPError,
    OpenNMSTransportError,
)

logger = logging.getLogger("netbox_opennms")

DEFAULT_TIMEOUT = 10
PLUGIN_NAME = "netbox_opennms"


class OpenNMSClient:
    """Thin REST adapter for OpenNMS, behind a single port (AD-2)."""

    def __init__(
        self, base_url, username, password, verify=True, timeout=DEFAULT_TIMEOUT
    ):
        self.base_url = (base_url or "").rstrip("/")
        self.verify = verify
        self.timeout = timeout
        self._session = requests.Session()
        self._session.auth = HTTPBasicAuth(username, password)

    @classmethod
    def from_config(cls):
        """Build a client from PLUGINS_CONFIG (credentials never come from models)."""
        base_url = get_plugin_config(PLUGIN_NAME, "opennms_url")
        if not base_url:
            raise OpenNMSError(
                "OpenNMS URL is not configured "
                "(set PLUGINS_CONFIG['netbox_opennms']['opennms_url'])."
            )
        if not base_url.startswith(("https://", "http://")):
            raise OpenNMSError("OpenNMS URL must start with https:// or http://.")
        if base_url.startswith("http://"):
            logger.warning(
                "OpenNMS URL uses http:// — credentials are sent in cleartext; "
                "use https:// (AD-13)."
            )
        username = get_plugin_config(PLUGIN_NAME, "opennms_username")
        password = get_plugin_config(PLUGIN_NAME, "opennms_password")
        if not username or not password:
            raise OpenNMSError(
                "OpenNMS credentials are not configured "
                "(set opennms_username and opennms_password)."
            )
        return cls(base_url=base_url, username=username, password=password)

    def close(self):
        self._session.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self.close()

    def _request(self, method, path, **kwargs):
        url = f"{self.base_url}{path}"
        kwargs.setdefault("timeout", self.timeout)
        kwargs.setdefault("verify", self.verify)
        # Don't silently follow redirects — a 3xx to an auth portal or a moved
        # context path must surface, not masquerade as success.
        kwargs.setdefault("allow_redirects", False)
        try:
            response = self._session.request(method, url, **kwargs)
        except requests.RequestException as exc:
            raise OpenNMSTransportError(
                f"Could not reach OpenNMS at {url}: {exc}"
            ) from exc
        if response.status_code in (401, 403):
            raise OpenNMSAuthError(
                f"OpenNMS rejected the credentials (HTTP {response.status_code})."
            )
        # Only a genuine 2xx is success (3xx redirects are not).
        if not 200 <= response.status_code < 300:
            raise OpenNMSHTTPError(
                f"OpenNMS returned HTTP {response.status_code} for {path}.",
                status_code=response.status_code,
            )
        return response

    def test_connection(self):
        """Probe reachability + credentials via ``GET /rest/requisitions``."""
        self._request("GET", "/rest/requisitions")
        return True
