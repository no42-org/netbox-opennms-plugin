# Copyright 2026 Ronny Trommer <ronny@no42.org>
# SPDX-License-Identifier: MIT
"""REST API serializers (Requisition redesign)."""

from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError as DjangoValidationError
from netbox.api.fields import ContentTypeField
from netbox.api.serializers import NetBoxModelSerializer
from rest_framework import serializers
from utilities.api import get_serializer_for_model

from ..choices import ServerOriginChoices
from ..derivation import location_name_error, requisition_name_error
from ..membership import filter_errors
from ..models import (
    ASSIGNMENT_MODELS,
    AssetMapping,
    MetadataEntry,
    MonitoredInterface,
    MonitoredService,
    MonitoringDetector,
    MonitoringOverride,
    MonitoringPolicy,
    OpenNMSServer,
    Requisition,
)


def _validate_location(value):
    error = location_name_error(value)
    if error:
        raise serializers.ValidationError(error)
    return value


class OpenNMSServerSerializer(NetBoxModelSerializer):
    """An OpenNMS server; ``password`` and ``headers`` are write-only.

    ``address`` is the model's ``url`` (the API ``url`` is the self link). On
    read, ``username`` is the effective one (from ``PLUGINS_CONFIG`` for a
    provisioned row). NetBox also serializes servers for its event queue.
    """

    url = serializers.HyperlinkedIdentityField(
        view_name="plugins-api:netbox_opennms-api:opennmsserver-detail"
    )
    address = serializers.CharField(source="url", max_length=255)
    password = serializers.CharField(
        write_only=True, required=False, allow_blank=True, max_length=255
    )
    headers = serializers.JSONField(write_only=True, required=False)

    class Meta:
        model = OpenNMSServer
        fields = (
            "id",
            "url",
            "display",
            "name",
            "origin",
            "address",
            "username",
            "password",
            "headers",
            "default_location",
            "description",
            "tags",
            "custom_fields",
            "created",
            "last_updated",
        )
        read_only_fields = ("origin",)
        brief_fields = ("id", "url", "display", "name")

    def to_representation(self, instance):
        data = super().to_representation(instance)
        if "username" in data:
            data["username"] = instance.effective_username
        return data

    def validate(self, data):
        # A blank password on update keeps the stored one (as in the UI form):
        # leave the field untouched; OpenNMSServer.clean() owns the rule.
        if self.instance is not None and not data.get("password", True):
            data.pop("password")
        # A provisioned row whose config entry is gone is adopted as local, as
        # the UI form does. Checked before super().validate() applies the
        # request's attributes (a new name) to the instance.
        if self.instance is not None and self.instance.is_missing:
            data["origin"] = ServerOriginChoices.LOCAL
        try:
            return super().validate(data)
        except DjangoValidationError as exc:
            # The model calls the API "address" field "url".
            if hasattr(exc, "error_dict") and "url" in exc.error_dict:
                exc.error_dict["address"] = exc.error_dict.pop("url")
            raise


class RequisitionSerializer(NetBoxModelSerializer):
    url = serializers.HyperlinkedIdentityField(
        view_name="plugins-api:netbox_opennms-api:requisition-detail"
    )
    server = OpenNMSServerSerializer(nested=True, required=False)

    class Meta:
        model = Requisition
        fields = (
            "id",
            "url",
            "display",
            "name",
            "description",
            "server",
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
        error = requisition_name_error(value)
        if error:
            raise serializers.ValidationError(error)
        return value

    def validate(self, data):
        if self.instance is None and "server" not in data:
            servers = list(OpenNMSServer.objects.all()[:2])
            if not servers:
                message = "This field is required: no OpenNMS server exists yet."
                raise serializers.ValidationError({"server": message})
            if len(servers) > 1:
                message = "This field is required when more than one server exists."
                raise serializers.ValidationError({"server": message})
            data["server"] = servers[0]
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
            "management_role",
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


class MonitoredInterfaceSerializer(NetBoxModelSerializer):
    url = serializers.HyperlinkedIdentityField(
        view_name="plugins-api:netbox_opennms-api:monitoredinterface-detail"
    )

    class Meta:
        model = MonitoredInterface
        fields = (
            "id",
            "url",
            "display",
            "override",
            "ip_address",
            "role",
            "tags",
            "custom_fields",
            "created",
            "last_updated",
        )
        brief_fields = ("id", "url", "display", "role")


class AssetMappingSerializer(NetBoxModelSerializer):
    url = serializers.HyperlinkedIdentityField(
        view_name="plugins-api:netbox_opennms-api:assetmapping-detail"
    )

    class Meta:
        model = AssetMapping
        fields = (
            "id",
            "url",
            "display",
            "requisition",
            "netbox_source",
            "asset_field",
            "tags",
            "custom_fields",
            "created",
            "last_updated",
        )
        brief_fields = ("id", "url", "display", "asset_field")


class MetadataEntrySerializer(NetBoxModelSerializer):
    url = serializers.HyperlinkedIdentityField(
        view_name="plugins-api:netbox_opennms-api:metadataentry-detail"
    )

    class Meta:
        model = MetadataEntry
        fields = (
            "id",
            "url",
            "display",
            "requisition",
            "scope",
            "context",
            "key",
            "value_source",
            "literal_value",
            "tags",
            "custom_fields",
            "created",
            "last_updated",
        )
        brief_fields = ("id", "url", "display", "key")
