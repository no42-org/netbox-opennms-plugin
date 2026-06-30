# Copyright 2026 Ronny Trommer <ronny@no42.org>
# SPDX-License-Identifier: MIT
"""REST API views (Epic 5)."""

from netbox.api.viewsets import NetBoxModelViewSet

from ..filtersets import (
    MonitoredServiceFilterSet,
    MonitoringAssignmentFilterSet,
    MonitoringDetectorFilterSet,
    MonitoringOverrideFilterSet,
    MonitoringPolicyFilterSet,
    MonitoringProfileFilterSet,
)
from ..models import (
    MonitoredService,
    MonitoringAssignment,
    MonitoringDetector,
    MonitoringOverride,
    MonitoringPolicy,
    MonitoringProfile,
)
from .serializers import (
    MonitoredServiceSerializer,
    MonitoringAssignmentSerializer,
    MonitoringDetectorSerializer,
    MonitoringOverrideSerializer,
    MonitoringPolicySerializer,
    MonitoringProfileSerializer,
)


class MonitoringProfileViewSet(NetBoxModelViewSet):
    queryset = MonitoringProfile.objects.prefetch_related("detectors", "policies")
    serializer_class = MonitoringProfileSerializer
    filterset_class = MonitoringProfileFilterSet


class MonitoringDetectorViewSet(NetBoxModelViewSet):
    queryset = MonitoringDetector.objects.select_related("profile")
    serializer_class = MonitoringDetectorSerializer
    filterset_class = MonitoringDetectorFilterSet


class MonitoringPolicyViewSet(NetBoxModelViewSet):
    queryset = MonitoringPolicy.objects.select_related("profile")
    serializer_class = MonitoringPolicySerializer
    filterset_class = MonitoringPolicyFilterSet


class MonitoringAssignmentViewSet(NetBoxModelViewSet):
    queryset = MonitoringAssignment.objects.select_related("profile", "site", "role")
    serializer_class = MonitoringAssignmentSerializer
    filterset_class = MonitoringAssignmentFilterSet


class MonitoringOverrideViewSet(NetBoxModelViewSet):
    queryset = MonitoringOverride.objects.prefetch_related(
        "additional_ips", "services"
    ).select_related("assigned_object_type", "management_ip")
    serializer_class = MonitoringOverrideSerializer
    filterset_class = MonitoringOverrideFilterSet


class MonitoredServiceViewSet(NetBoxModelViewSet):
    queryset = MonitoredService.objects.select_related("override", "ip_address")
    serializer_class = MonitoredServiceSerializer
    filterset_class = MonitoredServiceFilterSet
