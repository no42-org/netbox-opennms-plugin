# Copyright 2026 Ronny Trommer <ronny@no42.org>
# SPDX-License-Identifier: Apache-2.0
"""Navigation menu items."""

from netbox.plugins import PluginMenuButton, PluginMenuItem

menu_items = (
    PluginMenuItem(
        link="plugins:netbox_opennms:monitoringprofile_list",
        link_text="Monitoring Profiles",
        buttons=(
            PluginMenuButton(
                link="plugins:netbox_opennms:monitoringprofile_add",
                title="Add",
                icon_class="mdi mdi-plus-thick",
            ),
        ),
    ),
    PluginMenuItem(
        link="plugins:netbox_opennms:monitoredservice_list",
        link_text="Monitored Services",
        buttons=(
            PluginMenuButton(
                link="plugins:netbox_opennms:monitoredservice_add",
                title="Add",
                icon_class="mdi mdi-plus-thick",
            ),
        ),
    ),
    PluginMenuItem(
        link="plugins:netbox_opennms:connection_test",
        link_text="Connection Test",
    ),
)
