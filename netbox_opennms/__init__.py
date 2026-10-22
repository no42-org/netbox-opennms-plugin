# Copyright 2026 Ronny Trommer <ronny@no42.org>
# SPDX-License-Identifier: MIT
"""NetBox plugin that provisions nodes into OpenNMS via the REST provisioning API."""

from netbox.plugins import PluginConfig

# Single source of truth for the version: pyproject reads this via
# [tool.setuptools.dynamic] (a top-level literal, AST-read at build time without
# importing NetBox). PluginConfig.version also derives from it.
__version__ = "0.1.0"


class NetBoxOpenNMSConfig(PluginConfig):
    """Plugin configuration for netbox-opennms-plugin.

    Declares NetBox compatibility and the configuration surface
    (``PLUGINS_CONFIG``). Provisioned servers read credentials from
    PLUGINS_CONFIG at runtime. Local servers store them Fernet-encrypted in
    ``OpenNMSServer``; the key never lives in the database (AD-13).
    """

    name = "netbox_opennms"
    verbose_name = "NetBox OpenNMS"
    description = (
        "Provision NetBox devices and virtual machines into OpenNMS "
        "via the REST provisioning API."
    )
    version = __version__
    author = "Ronny Trommer"
    author_email = "ronny@no42.org"
    base_url = "opennms"

    # NetBox 4.6 introduced Python 3.12+ and Django 6.0; pin to the 4.6.x line.
    # 4.6.1 minimum: the no-worker warning (Story 1.8) uses
    # utilities.rqworker.any_workers_for_queue, added in 4.6.1.
    min_version = "4.6.1"
    # max_version intentionally unset — pinned against a tested 4.6.x patch at
    # release (Story 4.4). Do not pin Django independently; NetBox bundles it.

    # Connection surface consumed by the OpenNMS REST client (Story 1.4).
    # Override these in NetBox's PLUGINS_CONFIG. Provisioned server secrets
    # belong here or in a secrets backend. The flat opennms_* keys and
    # default_location are the single-server shorthand for servers["default"];
    # "servers" maps a name to {url, username, password, default_location,
    # headers}. credentials_key seeds encryption of credentials stored in models.
    default_settings = {
        "servers": {},
        "credentials_key": "",
        "opennms_url": "",
        "opennms_username": "",
        "opennms_password": "",
        "default_location": "",
        # rescanExisting value used by the import step (Story 1.7).
        "import_mode": "false",
        # Periodic drift reconciler: clear OpenNMS netbox.* Foreign Sources that
        # NetBox no longer governs (last member left / moved / unassigned). "true"
        # / "false". Touches only the plugin's own namespace.
        "reconcile_orphans": "true",
    }

    def ready(self):
        super().ready()
        from . import signals  # noqa: F401  (registers post_delete handlers)


config = NetBoxOpenNMSConfig
