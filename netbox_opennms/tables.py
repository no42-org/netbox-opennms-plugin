# Copyright 2026 Ronny Trommer <ronny@no42.org>
# SPDX-License-Identifier: MIT
"""Tables for plugin list views (Epic 5)."""

import django_tables2 as tables
from netbox.tables import NetBoxTable, columns

from .models import (
    MonitoredService,
    MonitoringAssignment,
    MonitoringDetector,
    MonitoringOverride,
    MonitoringPolicy,
    MonitoringProfile,
)


class MonitoringProfileTable(NetBoxTable):
    name = tables.Column(linkify=True)

    class Meta(NetBoxTable.Meta):
        model = MonitoringProfile
        fields = (
            "pk",
            "id",
            "name",
            "description",
            "scan_interval",
            "default_interfaces",
            "created",
            "last_updated",
            "actions",
        )
        default_columns = ("name", "description", "scan_interval", "default_interfaces")


class MonitoringDetectorTable(NetBoxTable):
    name = tables.Column(linkify=True)
    profile = tables.Column(linkify=True)

    class Meta(NetBoxTable.Meta):
        model = MonitoringDetector
        fields = (
            "pk",
            "id",
            "profile",
            "name",
            "preset",
            "rule_class",
            "created",
            "last_updated",
            "actions",
        )
        default_columns = ("profile", "name", "preset", "rule_class")


class MonitoringPolicyTable(NetBoxTable):
    name = tables.Column(linkify=True)
    profile = tables.Column(linkify=True)

    class Meta(NetBoxTable.Meta):
        model = MonitoringPolicy
        fields = (
            "pk",
            "id",
            "profile",
            "name",
            "preset",
            "rule_class",
            "created",
            "last_updated",
            "actions",
        )
        default_columns = ("profile", "name", "preset", "rule_class")


class MonitoringAssignmentTable(NetBoxTable):
    profile = tables.Column(linkify=True)
    site = tables.Column(linkify=True)
    role = tables.Column(linkify=True)

    class Meta(NetBoxTable.Meta):
        model = MonitoringAssignment
        fields = (
            "pk",
            "id",
            "profile",
            "site",
            "role",
            "location",
            "created",
            "last_updated",
            "actions",
        )
        default_columns = ("profile", "site", "role", "location")


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
