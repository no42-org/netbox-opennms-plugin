# Copyright 2026 Ronny Trommer <ronny@no42.org>
# SPDX-License-Identifier: MIT
"""REST API serializers (Requisition redesign)."""

from django.contrib.contenttypes.models import ContentType
from netbox.api.fields import ContentTypeField
from netbox.api.serializers import NetBoxModelSerializer
from rest_framework import serializers
from utilities.api import get_serializer_for_model

from ..derivation import validate_location_name, validate_requisition_name
from ..membership import filter_errors
from ..models import (
    ASSIGNMENT_MODELS,
    MonitoredService,
    MonitoringDetector,
    MonitoringOverride,
    MonitoringPolicy,
    Requisition,
    object_ip_pks,
)


def _validate_location(value):
    try:
        validate_location_name(value)
    except ValueError as exc:
        raise serializers.ValidationError(str(exc)) from exc
    return value


class RequisitionSerializer(NetBoxModelSerializer):
    url = serializers.HyperlinkedIdentityField(
        view_name="plugins-api:netbox_opennms-api:requisition-detail"
    )

    class Meta:
        model = Requisition
        fields = (
            "id",
            "url",
            "display",
            "name",
            "description",
            "object_types",
            "filter_params",
            "scan_interval",
            "default_interfaces",
            "services",
            "location",
            "tags",
            "custom_fields",
            "created",
            "last_updated",
        )
        brief_fields = ("id", "url", "display", "name")

    def validate_location(self, value):
        return _validate_location(value)

    def validate_name(self, value):
        try:
            validate_requisition_name(value)
        except ValueError as exc:
            raise serializers.ValidationError(str(exc)) from exc
        return value

    def validate(self, data):
        data = super().validate(data)
        # Build a transient instance to reuse the resolver's filter guard (C1/H1).
        instance = self.instance or Requisition()
        for attr in ("object_types", "filter_params"):
            if attr in data:
                setattr(instance, attr, data[attr])
        errors = filter_errors(instance)
        if errors:
            raise serializers.ValidationError({"filter_params": errors})
        return data


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
            "requisition",
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
            "requisition",
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
            "suppressed_services",
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

            # Additional IPs must be the object's own interfaces (AD-15) — mirror
            # the form's guard on the API path.
            additional = data.get("additional_ips")
            if additional:
                owned = object_ip_pks(model.objects.get(pk=object_id))
                foreign = [ip for ip in additional if ip.pk not in owned]
                if foreign:
                    raise serializers.ValidationError(
                        {
                            "additional_ips": "These IPs are not assigned to the "
                            "object: " + ", ".join(str(ip) for ip in foreign)
                        }
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
