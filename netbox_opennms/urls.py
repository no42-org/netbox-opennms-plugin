# Copyright 2026 Ronny Trommer <ronny@no42.org>
# SPDX-License-Identifier: MIT
"""UI URL routing (Requisition redesign)."""

from django.urls import path
from netbox.views.generic import ObjectChangeLogView

from . import views
from .models import (
    MonitoredInterface,
    MonitoredService,
    MonitoringDetector,
    MonitoringOverride,
    MonitoringPolicy,
    Requisition,
)


def _crud(prefix, name, view_prefix, model, *, bulk_delete=True):
    """The standard list/add/<pk>/edit/delete/changelog routes for a model."""
    routes = [
        path(f"{prefix}/", getattr(views, f"{view_prefix}ListView").as_view(),
             name=f"{name}_list"),
        path(f"{prefix}/add/", getattr(views, f"{view_prefix}EditView").as_view(),
             name=f"{name}_add"),
    ]
    if bulk_delete:
        routes.append(
            path(f"{prefix}/delete/",
                 getattr(views, f"{view_prefix}BulkDeleteView").as_view(),
                 name=f"{name}_bulk_delete")
        )
    routes += [
        path(f"{prefix}/<int:pk>/", getattr(views, f"{view_prefix}View").as_view(),
             name=name),
        path(f"{prefix}/<int:pk>/edit/",
             getattr(views, f"{view_prefix}EditView").as_view(), name=f"{name}_edit"),
        path(f"{prefix}/<int:pk>/delete/",
             getattr(views, f"{view_prefix}DeleteView").as_view(),
             name=f"{name}_delete"),
        path(f"{prefix}/<int:pk>/changelog/", ObjectChangeLogView.as_view(),
             name=f"{name}_changelog", kwargs={"model": model}),
    ]
    return routes


urlpatterns = (
    *_crud("requisitions", "requisition", "Requisition", Requisition),
    path(
        "requisitions/<int:pk>/sync/",
        views.RequisitionSyncView.as_view(),
        name="requisition_sync",
    ),
    path(
        "requisitions/<int:pk>/duplicate/",
        views.RequisitionDuplicateView.as_view(),
        name="requisition_duplicate",
    ),
    path(
        "requisitions/<int:pk>/dry-run/",
        views.RequisitionDryRunView.as_view(),
        name="requisition_dry_run",
    ),
    *_crud("monitoring-detectors", "monitoringdetector", "MonitoringDetector",
           MonitoringDetector),
    *_crud("monitoring-policies", "monitoringpolicy", "MonitoringPolicy",
           MonitoringPolicy),
    *_crud("monitoring-overrides", "monitoringoverride", "MonitoringOverride",
           MonitoringOverride),
    *_crud("monitored-services", "monitoredservice", "MonitoredService",
           MonitoredService),
    *_crud("monitored-interfaces", "monitoredinterface", "MonitoredInterface",
           MonitoredInterface),
    path(
        "sync/",
        views.SyncPreviewView.as_view(),
        name="sync_preview",
    ),
    path(
        "sync/all/",
        views.MonitoringSyncAllView.as_view(),
        name="sync_all",
    ),
    path(
        "sync/foreign-source/",
        views.ForeignSourceSyncView.as_view(),
        name="foreign_source_sync",
    ),
    path(
        "connection-test/",
        views.OpenNMSConnectionTestView.as_view(),
        name="connection_test",
    ),
)
