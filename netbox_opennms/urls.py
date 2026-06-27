# Copyright 2026 Ronny Trommer <ronny@no42.org>
# SPDX-License-Identifier: Apache-2.0
"""UI URL routing."""

from django.urls import path
from netbox.views.generic import ObjectChangeLogView

from . import views
from .models import MonitoredService, MonitoringProfile

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
    path(
        "monitoring-profiles/<int:pk>/sync/",
        views.MonitoringProfileSyncView.as_view(),
        name="monitoringprofile_sync",
    ),
    path(
        "monitoring-profiles/<int:pk>/remove/",
        views.MonitoringProfileRemoveView.as_view(),
        name="monitoringprofile_remove",
    ),
    path(
        "monitored-services/",
        views.MonitoredServiceListView.as_view(),
        name="monitoredservice_list",
    ),
    path(
        "monitored-services/add/",
        views.MonitoredServiceEditView.as_view(),
        name="monitoredservice_add",
    ),
    path(
        "monitored-services/delete/",
        views.MonitoredServiceBulkDeleteView.as_view(),
        name="monitoredservice_bulk_delete",
    ),
    path(
        "monitored-services/<int:pk>/",
        views.MonitoredServiceView.as_view(),
        name="monitoredservice",
    ),
    path(
        "monitored-services/<int:pk>/edit/",
        views.MonitoredServiceEditView.as_view(),
        name="monitoredservice_edit",
    ),
    path(
        "monitored-services/<int:pk>/delete/",
        views.MonitoredServiceDeleteView.as_view(),
        name="monitoredservice_delete",
    ),
    path(
        "monitored-services/<int:pk>/changelog/",
        ObjectChangeLogView.as_view(),
        name="monitoredservice_changelog",
        kwargs={"model": MonitoredService},
    ),
    path(
        "connection-test/",
        views.OpenNMSConnectionTestView.as_view(),
        name="connection_test",
    ),
)
