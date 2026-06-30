# Copyright 2026 Ronny Trommer <ronny@no42.org>
# SPDX-License-Identifier: MIT
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
        link="plugins:netbox_opennms:monitoringassignment_list",
        link_text="Monitoring Assignments",
        buttons=(
            PluginMenuButton(
                link="plugins:netbox_opennms:monitoringassignment_add",
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
        link="plugins:netbox_opennms:connection_test",
        link_text="Connection Test",
    ),
)
