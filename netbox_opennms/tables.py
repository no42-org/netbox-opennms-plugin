# Copyright 2026 Ronny Trommer <ronny@no42.org>
# SPDX-License-Identifier: Apache-2.0
"""Tables for plugin list views."""

import django_tables2 as tables
from netbox.tables import NetBoxTable, columns

from .models import MonitoringProfile


class MonitoringProfileTable(NetBoxTable):
    assigned_object = tables.Column(linkify=True, verbose_name="Object")
    assigned_object_type = columns.ContentTypeColumn(verbose_name="Type")
    management_ip = tables.Column(linkify=True, verbose_name="Management IP")
    enabled = columns.BooleanColumn()

    class Meta(NetBoxTable.Meta):
        model = MonitoringProfile
        fields = (
            "pk",
            "id",
            "assigned_object",
            "assigned_object_type",
            "management_ip",
            "enabled",
            "created",
            "last_updated",
            "actions",
        )
        default_columns = (
            "assigned_object",
            "assigned_object_type",
            "management_ip",
            "enabled",
        )
