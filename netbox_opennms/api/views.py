# Copyright 2026 Ronny Trommer <ronny@no42.org>
# SPDX-License-Identifier: Apache-2.0
"""REST API views."""

from netbox.api.viewsets import NetBoxModelViewSet

from ..filtersets import MonitoredServiceFilterSet, MonitoringProfileFilterSet
from ..models import MonitoredService, MonitoringProfile
from .serializers import MonitoredServiceSerializer, MonitoringProfileSerializer


class MonitoringProfileViewSet(NetBoxModelViewSet):
    queryset = MonitoringProfile.objects.all()
    serializer_class = MonitoringProfileSerializer
    filterset_class = MonitoringProfileFilterSet


class MonitoredServiceViewSet(NetBoxModelViewSet):
    queryset = MonitoredService.objects.select_related("profile", "ip_address")
    serializer_class = MonitoredServiceSerializer
    filterset_class = MonitoredServiceFilterSet
