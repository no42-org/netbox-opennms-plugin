# Copyright 2026 Ronny Trommer <ronny@no42.org>
# SPDX-License-Identifier: MIT
"""Navigation menu items."""

from netbox.plugins import PluginMenuButton, PluginMenuItem

menu_items = (
    PluginMenuItem(
        link="plugins:netbox_opennms:opennmsserver_list",
        link_text="OpenNMS Servers",
        permissions=["netbox_opennms.view_opennmsserver"],
        buttons=(
            PluginMenuButton(
                link="plugins:netbox_opennms:opennmsserver_add",
                title="Add",
                icon_class="mdi mdi-plus-thick",
                permissions=["netbox_opennms.add_opennmsserver"],
            ),
        ),
    ),
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
        permissions=["netbox_opennms.view_requisition"],
    ),
)
