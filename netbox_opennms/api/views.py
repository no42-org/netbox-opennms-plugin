# Copyright 2026 Ronny Trommer <ronny@no42.org>
# SPDX-License-Identifier: MIT
"""REST API views (Requisition redesign)."""

from netbox.api.viewsets import NetBoxModelViewSet

from ..filtersets import (
    AssetMappingFilterSet,
    MetadataEntryFilterSet,
    MonitoredInterfaceFilterSet,
    MonitoredServiceFilterSet,
    MonitoringDetectorFilterSet,
    MonitoringOverrideFilterSet,
    MonitoringPolicyFilterSet,
    RequisitionFilterSet,
)
from ..models import (
    AssetMapping,
    MetadataEntry,
    MonitoredInterface,
    MonitoredService,
    MonitoringDetector,
    MonitoringOverride,
    MonitoringPolicy,
    Requisition,
)
from .serializers import (
    AssetMappingSerializer,
    MetadataEntrySerializer,
    MonitoredInterfaceSerializer,
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
        "interfaces", "services"
    ).select_related("assigned_object_type", "management_ip")
    serializer_class = MonitoringOverrideSerializer
    filterset_class = MonitoringOverrideFilterSet


class MonitoredServiceViewSet(NetBoxModelViewSet):
    queryset = MonitoredService.objects.select_related("override", "ip_address")
    serializer_class = MonitoredServiceSerializer
    filterset_class = MonitoredServiceFilterSet


class MonitoredInterfaceViewSet(NetBoxModelViewSet):
    queryset = MonitoredInterface.objects.select_related("override", "ip_address")
    serializer_class = MonitoredInterfaceSerializer
    filterset_class = MonitoredInterfaceFilterSet


class AssetMappingViewSet(NetBoxModelViewSet):
    queryset = AssetMapping.objects.select_related("requisition")
    serializer_class = AssetMappingSerializer
    filterset_class = AssetMappingFilterSet


class MetadataEntryViewSet(NetBoxModelViewSet):
    queryset = MetadataEntry.objects.select_related("requisition")
    serializer_class = MetadataEntrySerializer
    filterset_class = MetadataEntryFilterSet
