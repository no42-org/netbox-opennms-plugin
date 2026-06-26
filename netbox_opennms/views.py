# Copyright 2026 Ronny Trommer <ronny@no42.org>
# SPDX-License-Identifier: Apache-2.0
"""UI views for plugin models."""

from netbox.views import generic

from . import filtersets, forms, tables
from .models import MonitoringProfile


class MonitoringProfileView(generic.ObjectView):
    queryset = MonitoringProfile.objects.all()


class MonitoringProfileListView(generic.ObjectListView):
    queryset = MonitoringProfile.objects.all()
    table = tables.MonitoringProfileTable
    filterset = filtersets.MonitoringProfileFilterSet


class MonitoringProfileEditView(generic.ObjectEditView):
    queryset = MonitoringProfile.objects.all()
    form = forms.MonitoringProfileForm


class MonitoringProfileDeleteView(generic.ObjectDeleteView):
    queryset = MonitoringProfile.objects.all()


class MonitoringProfileBulkDeleteView(generic.BulkDeleteView):
    queryset = MonitoringProfile.objects.all()
    table = tables.MonitoringProfileTable
