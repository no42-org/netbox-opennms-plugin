# Copyright 2026 Ronny Trommer <ronny@no42.org>
# SPDX-License-Identifier: Apache-2.0
"""Filter sets for plugin models."""

from netbox.filtersets import NetBoxModelFilterSet

from .models import MonitoredService, MonitoringProfile


class MonitoringProfileFilterSet(NetBoxModelFilterSet):
    class Meta:
        model = MonitoringProfile
        fields = ("id", "enabled", "assigned_object_type", "assigned_object_id")

    def search(self, queryset, name, value):
        # No free-text field on this model: a text query matches nothing rather
        # than silently returning every profile.
        if value:
            return queryset.none()
        return queryset


class MonitoredServiceFilterSet(NetBoxModelFilterSet):
    class Meta:
        model = MonitoredService
        fields = ("id", "profile", "ip_address", "name")

    def search(self, queryset, name, value):
        if value:
            return queryset.filter(name__icontains=value)
        return queryset
