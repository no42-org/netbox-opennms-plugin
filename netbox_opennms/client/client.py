# Copyright 2026 Ronny Trommer <ronny@no42.org>
# SPDX-License-Identifier: MIT
"""OpenNMS REST client — the single port for all OpenNMS I/O (AD-2).

Connection plumbing + ``test_connection`` (Story 1.4) and the requisition write
methods used by Sync (Story 1.7): ``post_foreign_source`` / ``post_requisition``
/ ``import_requisition``, plus ``list_locations`` for the best-effort no-Minion
warning (Story 4.1).
"""

import logging
from urllib.parse import quote

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
            # Include a snippet of the response body — OpenNMS explains XSD /
            # validation rejections there, and it makes the honest-status error
            # detail actionable (AD-12) instead of a bare status code.
            text = getattr(response, "text", None)
            detail = ""
            if isinstance(text, str) and text.strip():
                detail = f" — {text.strip()[:500]}"
            raise OpenNMSHTTPError(
                f"OpenNMS returned HTTP {response.status_code} for {path}.{detail}",
                status_code=response.status_code,
            )
        return response

    def test_connection(self):
        """Probe reachability + credentials via ``GET /rest/requisitions``."""
        self._request("GET", "/rest/requisitions")
        return True

    def list_locations(self):
        """Monitoring-location names known to OpenNMS (Story 4.1, FR-5/AD-2).

        ``GET /api/v2/monitoringLocations`` (JSON). A location absent from this
        set has no registered Minion, so a node assigned there is never polled.
        Best-effort callers swallow ``OpenNMSError`` and degrade (AD-16); this
        method itself just raises the typed taxonomy on failure.

        Parsed defensively: the payload may be a bare list or ``{"location": [...]}``,
        and an entry's name may be ``location-name`` (fallback ``id``/``name``).
        """
        response = self._request(
            "GET",
            "/api/v2/monitoringLocations",
            headers={"Accept": "application/json"},
            params={"limit": 0},
        )
        # A 2xx can still carry a non-JSON body (proxy/login HTML, empty body) or
        # a JSON scalar — normalize those parse failures into the typed taxonomy
        # so callers' OpenNMSError handling degrades them (AD-16), rather than a
        # bare ValueError/AttributeError escaping.
        try:
            payload = response.json()
            entries = (
                payload if isinstance(payload, list) else payload.get("location", [])
            )
            names = set()
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                name = (
                    entry.get("location-name")
                    or entry.get("id")
                    or entry.get("name")
                )
                if name:
                    names.add(name)
        except (ValueError, AttributeError, TypeError) as exc:
            raise OpenNMSError(
                "OpenNMS returned an unparseable monitoringLocations response."
            ) from exc
        return names

    def post_foreign_source(self, xml_bytes):
        """Apply a foreign-source definition (auto-detection config) — AD-5/AD-11.

        Posts the rendered ``foreign-source`` XML; must precede the requisition
        import so the empty-``<detectors/>`` definition is in place.
        """
        return self._request(
            "POST",
            "/rest/foreignSources",
            data=xml_bytes,
            headers={"Content-Type": "application/xml"},
        )

    def post_requisition(self, xml_bytes):
        """Stage the complete ``model-import`` requisition for a Foreign Source."""
        return self._request(
            "POST",
            "/rest/requisitions",
            data=xml_bytes,
            headers={"Content-Type": "application/xml"},
        )

    def import_requisition(self, foreign_source, rescan_existing="false"):
        """Activate the staged requisition (async — OpenNMS returns ``202``).

        ``rescan_existing`` comes from ``import_mode`` config (AD-13). A ``202`` is
        success here (accepted for import) — never read as "provisioned" (AD-12).
        """
        return self._request(
            "PUT",
            f"/rest/requisitions/{quote(foreign_source, safe='')}/import",
            params={"rescanExisting": rescan_existing},
        )
