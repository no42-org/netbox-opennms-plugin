# Copyright 2026 Ronny Trommer <ronny@no42.org>
# SPDX-License-Identifier: Apache-2.0
"""NetBox plugin that provisions nodes into OpenNMS via the REST provisioning API."""

from netbox.plugins import PluginConfig

__version__ = "0.1.0"


class NetBoxOpenNMSConfig(PluginConfig):
    """Plugin configuration for netbox-opennms-plugin.

    Declares NetBox compatibility and the connection configuration surface
    (``PLUGINS_CONFIG``). Credentials are read at runtime via
    ``get_plugin_config`` and are never stored in plugin models (AD-13).
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
    min_version = "4.6.0"
    # max_version intentionally unset — pinned against a tested 4.6.x patch at
    # release (Story 4.4). Do not pin Django independently; NetBox bundles it.

    # Connection surface consumed by the OpenNMS REST client (Story 1.4).
    # Override these in NetBox's PLUGINS_CONFIG. Secrets belong here / in a
    # secrets backend, never in plugin models.
    default_settings = {
        "opennms_url": "",
        "opennms_username": "",
        "opennms_password": "",
        "default_location": "",
        # rescanExisting value used by the import step (Story 1.7).
        "import_mode": "false",
    }

    def ready(self):
        super().ready()
        from . import signals  # noqa: F401  (registers post_delete handlers)


config = NetBoxOpenNMSConfig
