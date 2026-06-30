# Copyright 2026 Ronny Trommer <ronny@no42.org>
# SPDX-License-Identifier: MIT
"""REST API URL routing (Epic 5)."""

from netbox.api.routers import NetBoxRouter

from . import views

app_name = "netbox_opennms-api"

router = NetBoxRouter()
router.register("monitoring-profiles", views.MonitoringProfileViewSet)
router.register("monitoring-detectors", views.MonitoringDetectorViewSet)
router.register("monitoring-policies", views.MonitoringPolicyViewSet)
router.register("monitoring-assignments", views.MonitoringAssignmentViewSet)
router.register("monitoring-overrides", views.MonitoringOverrideViewSet)
router.register("monitored-services", views.MonitoredServiceViewSet)

urlpatterns = router.urls
