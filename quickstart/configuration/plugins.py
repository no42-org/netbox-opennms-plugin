# Copyright 2026 Ronny Trommer <ronny@no42.org>
# SPDX-License-Identifier: MIT
# Example plugin config for the quickstart deployment, mounted into NetBox at
# /etc/netbox/config/plugins.py. Edit opennms_url/credentials to point at your
# own OpenNMS, or use the bundled one (`docker compose --profile opennms up`).
PLUGINS = ["netbox_opennms"]

PLUGINS_CONFIG = {
    "netbox_opennms": {
        # The bundled OpenNMS service (reachable as "opennms" on the compose net).
        "opennms_url": "http://opennms:8980/opennms",
        "opennms_username": "admin",
        "opennms_password": "admin",
        # OpenNMS monitoring location for profiles that don't set one ("" = Default).
        "default_location": "",
        # rescanExisting value for the import step: "true" | "false" | "dbonly".
        "import_mode": "false",
    },
}
