# Copyright 2026 Ronny Trommer <ronny@no42.org>
# SPDX-License-Identifier: MIT
"""Tables for plugin list views (Requisition redesign)."""

import django_tables2 as tables
from netbox.tables import NetBoxTable, columns

from .models import (
    AssetMapping,
    MetadataEntry,
    MonitoredInterface,
    MonitoredService,
    MonitoringDetector,
    MonitoringOverride,
    MonitoringPolicy,
    OpenNMSServer,
    Requisition,
)

SERVER_STATUS = """
{% if record.is_provisioned %}{% if record.is_missing %}
<span class="badge text-bg-danger">Missing from configuration</span>
{% else %}<span class="badge text-bg-info">Managed by configuration</span>{% endif %}
{% endif %}
{% if not record.credentials_ok %}
<span class="badge text-bg-warning">Credentials need re-entering</span>
{% endif %}
"""


class OpenNMSServerTable(NetBoxTable):
    name = tables.Column(linkify=True)
    username = tables.Column(accessor="effective_username", orderable=False)
    status = tables.TemplateColumn(
        template_code=SERVER_STATUS, orderable=False, verbose_name="Status"
    )

    class Meta(NetBoxTable.Meta):
        model = OpenNMSServer
        # No password or headers column, so CSV export never carries secrets.
        fields = (
            "pk",
            "id",
            "name",
            "origin",
            "url",
            "username",
            "default_location",
            "description",
            "created",
            "last_updated",
            "actions",
        )
        default_columns = ("name", "origin", "status", "url", "username")


class RequisitionTable(NetBoxTable):
    name = tables.Column(linkify=True)
    server = tables.Column(linkify=True)

    class Meta(NetBoxTable.Meta):
        model = Requisition
        fields = (
            "pk",
            "id",
            "name",
            "server",
            "description",
            "object_types",
            "scan_interval",
            "default_interfaces",
            "location",
            "created",
            "last_updated",
            "actions",
        )
        default_columns = (
            "name",
            "server",
            "object_types",
            "scan_interval",
            "location",
        )


class MonitoringDetectorTable(NetBoxTable):
    name = tables.Column(linkify=True)
    requisition = tables.Column(linkify=True)

    class Meta(NetBoxTable.Meta):
        model = MonitoringDetector
        fields = (
            "pk",
            "id",
            "requisition",
            "name",
            "preset",
            "rule_class",
            "created",
            "last_updated",
            "actions",
        )
        default_columns = ("requisition", "name", "preset", "rule_class")


class MonitoringPolicyTable(NetBoxTable):
    name = tables.Column(linkify=True)
    requisition = tables.Column(linkify=True)

    class Meta(NetBoxTable.Meta):
        model = MonitoringPolicy
        fields = (
            "pk",
            "id",
            "requisition",
            "name",
            "preset",
            "rule_class",
            "created",
            "last_updated",
            "actions",
        )
        default_columns = ("requisition", "name", "preset", "rule_class")


class MonitoringOverrideTable(NetBoxTable):
    assigned_object = tables.Column(linkify=True, verbose_name="Object")
    assigned_object_type = columns.ContentTypeColumn(verbose_name="Type")
    management_ip = tables.Column(linkify=True, verbose_name="Management IP")
    exclude = columns.BooleanColumn()

    class Meta(NetBoxTable.Meta):
        model = MonitoringOverride
        fields = (
            "pk",
            "id",
            "assigned_object",
            "assigned_object_type",
            "exclude",
            "management_ip",
            "location",
            "created",
            "last_updated",
            "actions",
        )
        default_columns = (
            "assigned_object",
            "assigned_object_type",
            "exclude",
            "management_ip",
        )


class MonitoredServiceTable(NetBoxTable):
    override = tables.Column(linkify=True)
    ip_address = tables.Column(linkify=True, verbose_name="Interface IP")
    name = tables.Column(linkify=True)

    class Meta(NetBoxTable.Meta):
        model = MonitoredService
        fields = (
            "pk",
            "id",
            "override",
            "ip_address",
            "name",
            "created",
            "last_updated",
            "actions",
        )
        default_columns = ("override", "ip_address", "name")


class MonitoredInterfaceTable(NetBoxTable):
    override = tables.Column(linkify=True)
    ip_address = tables.Column(linkify=True, verbose_name="Interface IP")

    class Meta(NetBoxTable.Meta):
        model = MonitoredInterface
        fields = (
            "pk",
            "id",
            "override",
            "ip_address",
            "role",
            "created",
            "last_updated",
            "actions",
        )
        default_columns = ("override", "ip_address", "role")


class AssetMappingTable(NetBoxTable):
    requisition = tables.Column(linkify=True)
    asset_field = tables.Column(linkify=True)

    class Meta(NetBoxTable.Meta):
        model = AssetMapping
        fields = (
            "pk",
            "id",
            "requisition",
            "netbox_source",
            "asset_field",
            "created",
            "last_updated",
            "actions",
        )
        default_columns = ("requisition", "netbox_source", "asset_field")


class MetadataEntryTable(NetBoxTable):
    requisition = tables.Column(linkify=True)
    key = tables.Column(linkify=True)

    class Meta(NetBoxTable.Meta):
        model = MetadataEntry
        fields = (
            "pk",
            "id",
            "requisition",
            "scope",
            "context",
            "key",
            "value_source",
            "literal_value",
            "created",
            "last_updated",
            "actions",
        )
        default_columns = ("requisition", "scope", "context", "key")
