# Copyright 2026 Ronny Trommer <ronny@no42.org>
# SPDX-License-Identifier: Apache-2.0
"""REST API views."""

from netbox.api.viewsets import NetBoxModelViewSet

from ..filtersets import MonitoringProfileFilterSet
from ..models import MonitoringProfile
from .serializers import MonitoringProfileSerializer


class MonitoringProfileViewSet(NetBoxModelViewSet):
    queryset = MonitoringProfile.objects.all()
    serializer_class = MonitoringProfileSerializer
    filterset_class = MonitoringProfileFilterSet
