# Copyright 2026 Ronny Trommer <ronny@no42.org>
# SPDX-License-Identifier: Apache-2.0
"""UI URL routing."""

from django.urls import path
from netbox.views.generic import ObjectChangeLogView

from . import views
from .models import MonitoringProfile

urlpatterns = (
    path(
        "monitoring-profiles/",
        views.MonitoringProfileListView.as_view(),
        name="monitoringprofile_list",
    ),
    path(
        "monitoring-profiles/add/",
        views.MonitoringProfileEditView.as_view(),
        name="monitoringprofile_add",
    ),
    path(
        "monitoring-profiles/delete/",
        views.MonitoringProfileBulkDeleteView.as_view(),
        name="monitoringprofile_bulk_delete",
    ),
    path(
        "monitoring-profiles/<int:pk>/",
        views.MonitoringProfileView.as_view(),
        name="monitoringprofile",
    ),
    path(
        "monitoring-profiles/<int:pk>/edit/",
        views.MonitoringProfileEditView.as_view(),
        name="monitoringprofile_edit",
    ),
    path(
        "monitoring-profiles/<int:pk>/delete/",
        views.MonitoringProfileDeleteView.as_view(),
        name="monitoringprofile_delete",
    ),
    path(
        "monitoring-profiles/<int:pk>/changelog/",
        ObjectChangeLogView.as_view(),
        name="monitoringprofile_changelog",
        kwargs={"model": MonitoringProfile},
    ),
)
