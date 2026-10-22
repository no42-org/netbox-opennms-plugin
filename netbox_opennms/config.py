# Copyright 2026 Ronny Trommer <ronny@no42.org>
# SPDX-License-Identifier: MIT
"""Reader for the ``servers`` mapping in ``PLUGINS_CONFIG["netbox_opennms"]``.

The single place that reads the provisioned-server declarations and the legacy
flat keys (``opennms_url`` and friends). The flat keys are the single-server
shorthand for ``servers["default"]``. Malformed values are tolerated (skipped or
coerced) so a typo in configuration never raises at import or render time.
"""

from urllib.parse import urlsplit

from netbox.plugins import get_plugin_config

PLUGIN_NAME = "netbox_opennms"
_STR_FIELDS = ("url", "username", "password", "default_location")


def _text(value):
    if value is None:
        return ""
    return value if isinstance(value, str) else str(value)


def _headers(raw):
    """``raw`` as a ``{str: str}`` dict; ``requests`` rejects any other type."""
    if not isinstance(raw, dict):
        return {}
    headers = {}
    for key, value in raw.items():
        if key is None or value is None:
            continue
        try:
            headers[_text(key)] = _text(value)
        except Exception:
            continue
    return headers


def _normalise(raw):
    entry = {field: _text(raw.get(field)) for field in _STR_FIELDS}
    entry["headers"] = _headers(raw.get("headers"))
    return entry


def normalised_url(url):
    """``url`` without a trailing slash and with a lower-case scheme and host.

    Two URLs that normalise equal reach the same OpenNMS instance.
    """
    parts = urlsplit((url or "").strip())
    return (parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/"))


def server_entries():
    """Return ``{name: entry}`` for every configured server, as a fresh dict."""
    servers = get_plugin_config(PLUGIN_NAME, "servers")
    entries = {}
    if isinstance(servers, dict):
        for name, raw in servers.items():
            if isinstance(raw, dict):
                entries[name] = _normalise(raw)
    flat_url = _text(get_plugin_config(PLUGIN_NAME, "opennms_url"))
    if flat_url and "default" not in entries:
        entries["default"] = _normalise(
            {
                "url": flat_url,
                "username": get_plugin_config(PLUGIN_NAME, "opennms_username"),
                "password": get_plugin_config(PLUGIN_NAME, "opennms_password"),
                "default_location": get_plugin_config(PLUGIN_NAME, "default_location"),
            }
        )
    return entries


def server_entry(name):
    """Return the entry called ``name``, or ``None``."""
    return server_entries().get(name)


def upgrade_target(plugins_config):
    """Pick ``(name, url, default_location)`` for the upgrade migration.

    Works only on the raw dict it is given, so a migration can pass
    ``settings.PLUGINS_CONFIG.get("netbox_opennms", {})``.
    """
    flat_url = _text(plugins_config.get("opennms_url"))
    if flat_url:
        return ("default", flat_url, _text(plugins_config.get("default_location")))
    servers = plugins_config.get("servers")
    if isinstance(servers, dict) and len(servers) == 1:
        name, raw = next(iter(servers.items()))
        if isinstance(raw, dict):
            entry = _normalise(raw)
            return (name, entry["url"], entry["default_location"])
    return ("default", "", "")
