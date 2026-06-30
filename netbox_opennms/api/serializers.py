# Copyright 2026 Ronny Trommer <ronny@no42.org>
# SPDX-License-Identifier: MIT
"""REST API serializers (Epic 5)."""

from django.contrib.contenttypes.models import ContentType
from netbox.api.fields import ContentTypeField
from netbox.api.serializers import NetBoxModelSerializer
from rest_framework import serializers
from utilities.api import get_serializer_for_model

from ..derivation import validate_location_name
from ..models import (
    ASSIGNMENT_MODELS,
    MonitoredService,
    MonitoringAssignment,
    MonitoringDetector,
    MonitoringOverride,
    MonitoringPolicy,
    MonitoringProfile,
)


def _validate_location(value):
    try:
        validate_location_name(value)
    except ValueError as exc:
        raise serializers.ValidationError(str(exc)) from exc
    return value


class MonitoringProfileSerializer(NetBoxModelSerializer):
    url = serializers.HyperlinkedIdentityField(
        view_name="plugins-api:netbox_opennms-api:monitoringprofile-detail"
    )

    class Meta:
        model = MonitoringProfile
        fields = (
            "id",
            "url",
            "display",
            "name",
            "description",
            "scan_interval",
            "default_interfaces",
            "tags",
            "custom_fields",
            "created",
            "last_updated",
        )
        brief_fields = ("id", "url", "display", "name")


class MonitoringDetectorSerializer(NetBoxModelSerializer):
    url = serializers.HyperlinkedIdentityField(
        view_name="plugins-api:netbox_opennms-api:monitoringdetector-detail"
    )

    class Meta:
        model = MonitoringDetector
        fields = (
            "id",
            "url",
            "display",
            "profile",
            "name",
            "preset",
            "rule_class",
            "parameters",
            "tags",
            "custom_fields",
            "created",
            "last_updated",
        )
        brief_fields = ("id", "url", "display", "name")


class MonitoringPolicySerializer(NetBoxModelSerializer):
    url = serializers.HyperlinkedIdentityField(
        view_name="plugins-api:netbox_opennms-api:monitoringpolicy-detail"
    )

    class Meta:
        model = MonitoringPolicy
        fields = (
            "id",
            "url",
            "display",
            "profile",
            "name",
            "preset",
            "rule_class",
            "parameters",
            "tags",
            "custom_fields",
            "created",
            "last_updated",
        )
        brief_fields = ("id", "url", "display", "name")


class MonitoringAssignmentSerializer(NetBoxModelSerializer):
    url = serializers.HyperlinkedIdentityField(
        view_name="plugins-api:netbox_opennms-api:monitoringassignment-detail"
    )

    class Meta:
        model = MonitoringAssignment
        fields = (
            "id",
            "url",
            "display",
            "profile",
            "site",
            "role",
            "location",
            "tags",
            "custom_fields",
            "created",
            "last_updated",
        )
        brief_fields = ("id", "url", "display", "profile", "site", "role")

    def validate_location(self, value):
        return _validate_location(value)


class MonitoringOverrideSerializer(NetBoxModelSerializer):
    url = serializers.HyperlinkedIdentityField(
        view_name="plugins-api:netbox_opennms-api:monitoringoverride-detail"
    )
    assigned_object_type = ContentTypeField(
        queryset=ContentType.objects.filter(ASSIGNMENT_MODELS)
    )
    assigned_object = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = MonitoringOverride
        fields = (
            "id",
            "url",
            "display",
            "assigned_object_type",
            "assigned_object_id",
            "assigned_object",
            "exclude",
            "management_ip",
            "additional_ips",
            "location",
            "tags",
            "custom_fields",
            "created",
            "last_updated",
        )
        brief_fields = ("id", "url", "display", "exclude")

    def validate_location(self, value):
        return _validate_location(value)

    def get_assigned_object(self, obj):
        if obj.assigned_object is None:
            return None
        serializer = get_serializer_for_model(obj.assigned_object)
        context = {"request": self.context["request"]}
        return serializer(obj.assigned_object, nested=True, context=context).data

    def validate(self, data):
        data = super().validate(data)
        content_type = data.get("assigned_object_type") or getattr(
            self.instance, "assigned_object_type", None
        )
        object_id = data.get("assigned_object_id") or getattr(
            self.instance, "assigned_object_id", None
        )
        if content_type is not None and object_id is not None:
            model = content_type.model_class()
            if not model.objects.filter(pk=object_id).exists():
                raise serializers.ValidationError(
                    {"assigned_object_id": "The referenced object does not exist."}
                )
            duplicate = MonitoringOverride.objects.filter(
                assigned_object_type=content_type, assigned_object_id=object_id
            )
            if self.instance is not None:
                duplicate = duplicate.exclude(pk=self.instance.pk)
            if duplicate.exists():
                raise serializers.ValidationError(
                    "This object already has a Monitoring Override."
                )
        return data


class MonitoredServiceSerializer(NetBoxModelSerializer):
    url = serializers.HyperlinkedIdentityField(
        view_name="plugins-api:netbox_opennms-api:monitoredservice-detail"
    )

    class Meta:
        model = MonitoredService
        fields = (
            "id",
            "url",
            "display",
            "override",
            "ip_address",
            "name",
            "tags",
            "custom_fields",
            "created",
            "last_updated",
        )
        brief_fields = ("id", "url", "display", "name")
