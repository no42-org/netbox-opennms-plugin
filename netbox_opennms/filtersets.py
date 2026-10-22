# Copyright 2026 Ronny Trommer <ronny@no42.org>
# SPDX-License-Identifier: MIT
"""Filter sets for plugin models (Requisition redesign)."""

import django_filters
from django.db.models import Q
from netbox.filtersets import NetBoxModelFilterSet

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


class OpenNMSServerFilterSet(NetBoxModelFilterSet):
    class Meta:
        model = OpenNMSServer
        fields = ("id", "name", "origin", "url", "default_location")

    def search(self, queryset, name, value):
        if value:
            return queryset.filter(
                Q(name__icontains=value)
                | Q(description__icontains=value)
                | Q(url__icontains=value)
            )
        return queryset


class RequisitionFilterSet(NetBoxModelFilterSet):
    server_id = django_filters.ModelMultipleChoiceFilter(
        queryset=OpenNMSServer.objects.all(), label="Server (ID)"
    )
    server = django_filters.ModelMultipleChoiceFilter(
        field_name="server__name",
        queryset=OpenNMSServer.objects.all(),
        to_field_name="name",
        label="Server (name)",
    )

    class Meta:
        model = Requisition
        fields = (
            "id",
            "name",
            "object_types",
            "scan_interval",
            "default_interfaces",
            "location",
        )

    def search(self, queryset, name, value):
        if value:
            return queryset.filter(
                Q(name__icontains=value) | Q(description__icontains=value)
            )
        return queryset


class MonitoringDetectorFilterSet(NetBoxModelFilterSet):
    class Meta:
        model = MonitoringDetector
        fields = ("id", "requisition", "name", "preset", "rule_class")

    def search(self, queryset, name, value):
        if value:
            return queryset.filter(name__icontains=value)
        return queryset


class MonitoringPolicyFilterSet(NetBoxModelFilterSet):
    class Meta:
        model = MonitoringPolicy
        fields = ("id", "requisition", "name", "preset", "rule_class")

    def search(self, queryset, name, value):
        if value:
            return queryset.filter(name__icontains=value)
        return queryset


class MonitoringOverrideFilterSet(NetBoxModelFilterSet):
    class Meta:
        model = MonitoringOverride
        fields = (
            "id",
            "assigned_object_type",
            "assigned_object_id",
            "exclude",
            "location",
        )

    def search(self, queryset, name, value):
        if value:
            return queryset.filter(location__icontains=value)
        return queryset


class MonitoredServiceFilterSet(NetBoxModelFilterSet):
    class Meta:
        model = MonitoredService
        fields = ("id", "override", "ip_address", "name")

    def search(self, queryset, name, value):
        if value:
            return queryset.filter(name__icontains=value)
        return queryset


class MonitoredInterfaceFilterSet(NetBoxModelFilterSet):
    class Meta:
        model = MonitoredInterface
        fields = ("id", "override", "ip_address", "role")

    def search(self, queryset, name, value):
        return queryset


class AssetMappingFilterSet(NetBoxModelFilterSet):
    class Meta:
        model = AssetMapping
        fields = ("id", "requisition", "netbox_source", "asset_field")

    def search(self, queryset, name, value):
        if value:
            return queryset.filter(asset_field__icontains=value)
        return queryset


class MetadataEntryFilterSet(NetBoxModelFilterSet):
    class Meta:
        model = MetadataEntry
        fields = ("id", "requisition", "scope", "context", "key")

    def search(self, queryset, name, value):
        if value:
            return queryset.filter(key__icontains=value)
        return queryset
