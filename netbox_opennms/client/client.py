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

from .discovery import parse_plugins
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

    def list_detectors(self):
        """Available OpenNMS detectors + their parameter schema (RD-1, AD-2).

        ``GET /rest/foreignSourcesConfig/detectors`` (JSON) — the same endpoint the
        OpenNMS web UI uses to build its detector editor. Returns a list of
        ``DiscoveredPlugin`` (name + class + ``key``/``required``/``options`` params).
        Reflects what the target instance registers, incl. plugin/OIA detectors.
        """
        return self._list_plugins("/rest/foreignSourcesConfig/detectors")

    def list_policies(self):
        """Available OpenNMS provisioning policies + their parameter schema (RD-1)."""
        return self._list_plugins("/rest/foreignSourcesConfig/policies")

    def list_assets(self):
        """Available OpenNMS node **asset** field names (RD-2, AD-2).

        ``GET /rest/foreignSourcesConfig/assets`` → an ``ElementList``
        (``{"count": N, "element": [...]}`` on Horizon 36; a bare list on some
        versions). Returns the field-name set; typed error on an unparseable body.
        """
        response = self._request(
            "GET",
            "/rest/foreignSourcesConfig/assets",
            headers={"Accept": "application/json"},
        )
        try:
            payload = response.json()
            elements = (
                payload if isinstance(payload, list) else payload.get("element", [])
            )
            return {e for e in elements if isinstance(e, str)}
        except (ValueError, AttributeError, TypeError) as exc:
            raise OpenNMSError(
                "OpenNMS returned an unparseable foreignSourcesConfig/assets response."
            ) from exc

    def _list_plugins(self, path):
        """GET a ``foreignSourcesConfig`` plugin list; typed taxonomy on a bad body."""
        response = self._request(
            "GET", path, headers={"Accept": "application/json"}
        )
        try:
            return parse_plugins(response.json())
        except (ValueError, AttributeError, TypeError) as exc:
            raise OpenNMSError(
                f"OpenNMS returned an unparseable {path} response."
            ) from exc

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

    def list_requisition_names(self):
        """Foreign Source names of every requisition OpenNMS holds (drift recon).

        ``GET /rest/requisitions`` → ``{"model-import": [{"foreign-source": …}, …]}``.
        The reconciler compares this against the Foreign Sources NetBox still
        governs to find orphans. Parsed defensively into the typed taxonomy on a
        non-JSON/unexpected body (AD-16), like ``list_locations``.
        """
        response = self._request(
            "GET",
            "/rest/requisitions",
            headers={"Accept": "application/json"},
            params={"limit": 0},
        )
        try:
            payload = response.json()
            entries = (
                payload
                if isinstance(payload, list)
                else payload.get("model-import", [])
            )
            names = set()
            for entry in entries:
                if isinstance(entry, dict) and entry.get("foreign-source"):
                    names.add(entry["foreign-source"])
        except (ValueError, AttributeError, TypeError) as exc:
            raise OpenNMSError(
                "OpenNMS returned an unparseable requisitions response."
            ) from exc
        return names

    def get_requisition(self, foreign_source):
        """The current (deployed) requisition for a Foreign Source as JSON, or None.

        ``GET /rest/requisitions/{fs}`` → the ``model-import`` document. A 404 (the
        Foreign Source is not yet in OpenNMS) returns ``None`` so the dry-run reads
        it as an all-added diff (R7/M5), rather than raising. Other errors raise the
        typed taxonomy; an unparseable 2xx body raises ``OpenNMSError``.
        """
        return self._get_json_or_none(
            f"/rest/requisitions/{quote(foreign_source, safe='')}"
        )

    def get_foreign_source(self, foreign_source):
        """The current foreign-source definition for a Foreign Source as JSON, or None.

        ``GET /rest/foreignSources/{fs}`` → detectors/policies/scan-interval. A 404
        returns ``None`` (no definition yet). Feeds the dry-run's definition diff.
        """
        return self._get_json_or_none(
            f"/rest/foreignSources/{quote(foreign_source, safe='')}"
        )

    def _get_json_or_none(self, path):
        """GET *path* as JSON; ``None`` on 404; typed taxonomy on other failures."""
        try:
            response = self._request(
                "GET", path, headers={"Accept": "application/json"}
            )
        except OpenNMSHTTPError as exc:
            if exc.status_code == 404:
                return None
            raise
        try:
            return response.json()
        except (ValueError, AttributeError, TypeError) as exc:
            raise OpenNMSError(
                f"OpenNMS returned an unparseable response for {path}."
            ) from exc

    def delete_requisition(self, foreign_source):
        """Delete a requisition by Foreign Source — the DEPLOYED copy then pending.

        ``GET /rest/requisitions`` lists the *deployed* requisition, so removing a
        Foreign Source from the list (and stopping the drift reconciler re-finding
        it) requires ``DELETE /rest/requisitions/deployed/{fs}``; deleting the base
        (pending) path alone leaves the deployed copy listed. The pending delete
        follows so a later re-create starts clean. Both are idempotent in OpenNMS.
        """
        fs = quote(foreign_source, safe="")
        self._request("DELETE", f"/rest/requisitions/deployed/{fs}")
        return self._request("DELETE", f"/rest/requisitions/{fs}")

    def delete_foreign_source(self, foreign_source):
        """Delete a foreign-source definition by name (the detectors/policies shell)."""
        return self._request(
            "DELETE", f"/rest/foreignSources/{quote(foreign_source, safe='')}"
        )
