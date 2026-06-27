# Copyright 2026 Ronny Trommer <ronny@no42.org>
# SPDX-License-Identifier: Apache-2.0
"""REST API URL routing."""

from netbox.api.routers import NetBoxRouter

from . import views

app_name = "netbox_opennms-api"

router = NetBoxRouter()
router.register("monitoring-profiles", views.MonitoringProfileViewSet)
router.register("monitored-services", views.MonitoredServiceViewSet)

urlpatterns = router.urls
