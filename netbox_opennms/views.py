# Copyright 2026 Ronny Trommer <ronny@no42.org>
# SPDX-License-Identifier: Apache-2.0
"""UI views for plugin models."""

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import redirect, render
from django.views.generic import View
from netbox.views import generic

from . import filtersets, forms, tables
from .client import OpenNMSClient, OpenNMSError
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


class OpenNMSConnectionTestView(LoginRequiredMixin, View):
    """Authenticated action: probe the configured OpenNMS for reachability + auth."""

    template_name = "netbox_opennms/connection_test.html"

    def get(self, request):
        return render(request, self.template_name)

    def post(self, request):
        try:
            with OpenNMSClient.from_config() as client:
                client.test_connection()
        except OpenNMSError as exc:
            messages.error(request, f"OpenNMS connection failed: {exc}")
        else:
            messages.success(
                request,
                "OpenNMS connection OK — reachable and credentials accepted.",
            )
        return redirect("plugins:netbox_opennms:connection_test")
