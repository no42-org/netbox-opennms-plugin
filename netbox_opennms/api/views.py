# Copyright 2026 Ronny Trommer <ronny@no42.org>
# SPDX-License-Identifier: MIT
"""REST API views (Requisition redesign)."""

from netbox.api.viewsets import NetBoxModelViewSet

from ..filtersets import (
    MonitoredServiceFilterSet,
    MonitoringDetectorFilterSet,
    MonitoringOverrideFilterSet,
    MonitoringPolicyFilterSet,
    RequisitionFilterSet,
)
from ..models import (
    MonitoredService,
    MonitoringDetector,
    MonitoringOverride,
    MonitoringPolicy,
    Requisition,
)
from .serializers import (
    MonitoredServiceSerializer,
    MonitoringDetectorSerializer,
    MonitoringOverrideSerializer,
    MonitoringPolicySerializer,
    RequisitionSerializer,
)


class RequisitionViewSet(NetBoxModelViewSet):
    queryset = Requisition.objects.prefetch_related("detectors", "policies")
    serializer_class = RequisitionSerializer
    filterset_class = RequisitionFilterSet


class MonitoringDetectorViewSet(NetBoxModelViewSet):
    queryset = MonitoringDetector.objects.select_related("requisition")
    serializer_class = MonitoringDetectorSerializer
    filterset_class = MonitoringDetectorFilterSet


class MonitoringPolicyViewSet(NetBoxModelViewSet):
    queryset = MonitoringPolicy.objects.select_related("requisition")
    serializer_class = MonitoringPolicySerializer
    filterset_class = MonitoringPolicyFilterSet


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
