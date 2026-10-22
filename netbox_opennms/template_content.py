# Copyright 2026 Ronny Trommer <ronny@no42.org>
# SPDX-License-Identifier: MIT
"""Detail-page extensions for monitored objects (Story 4.2 observability).

Adds the OpenNMS last-sync panel to a Device's / VirtualMachine's detail page so
an operator sees provisioning status without opening the Monitoring Profile.
NetBox auto-discovers ``template_extensions`` — no PluginConfig change.
"""

from netbox.plugins import PluginTemplateExtension

from .jobs import sync_status_for

PANEL = "netbox_opennms/inc/sync_status_panel.html"


class _SyncStatusPanel(PluginTemplateExtension):
    """Render the per-server last-sync panel, or nothing if unmonitored."""

    def right_page(self):
        # Self-guard: an observability panel must never break the host object's
        # detail page, so degrade to nothing on any unexpected error.
        try:
            obj = self.context["object"]
            rows = sync_status_for(obj)
            # Show the panel when the object is monitored, has sync history, or is
            # CONFLICTED on any server (the conflict must be visible where the
            # operator looks — C1); an unmonitored object gets nothing.
            if not rows or not any(
                row["governed"] or row["job"] or row["conflicts"] for row in rows
            ):
                return ""
            return self.render(
                PANEL,
                extra_context={
                    "sync_status_rows": rows,
                    # One node-level Remove per panel: it excludes the object on
                    # every server (spec decision 13).
                    "sync_status_removable": any(
                        row["governed"] or row["conflicts"] for row in rows
                    ),
                },
            )
        except Exception:
            return ""


class DeviceSyncStatusPanel(_SyncStatusPanel):
    models = ["dcim.device"]


class VirtualMachineSyncStatusPanel(_SyncStatusPanel):
    models = ["virtualization.virtualmachine"]


template_extensions = [DeviceSyncStatusPanel, VirtualMachineSyncStatusPanel]
