# Copyright 2026 Ronny Trommer <ronny@no42.org>
# SPDX-License-Identifier: MIT
"""Navigation menu items."""

from netbox.plugins import PluginMenuButton, PluginMenuItem

menu_items = (
    PluginMenuItem(
        link="plugins:netbox_opennms:requisition_list",
        link_text="Requisitions",
        buttons=(
            PluginMenuButton(
                link="plugins:netbox_opennms:requisition_add",
                title="Add",
                icon_class="mdi mdi-plus-thick",
            ),
        ),
    ),
    PluginMenuItem(
        link="plugins:netbox_opennms:monitoringoverride_list",
        link_text="Monitoring Overrides",
        buttons=(
            PluginMenuButton(
                link="plugins:netbox_opennms:monitoringoverride_add",
                title="Add",
                icon_class="mdi mdi-plus-thick",
            ),
        ),
    ),
    PluginMenuItem(
        link="plugins:netbox_opennms:sync_preview",
        link_text="Sync Preview",
    ),
    PluginMenuItem(
        link="plugins:netbox_opennms:connection_test",
        link_text="Connection Test",
    ),
)
