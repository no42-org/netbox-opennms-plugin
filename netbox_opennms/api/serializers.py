# Copyright 2026 Ronny Trommer <ronny@no42.org>
# SPDX-License-Identifier: Apache-2.0
"""REST API serializers."""

from django.contrib.contenttypes.models import ContentType
from netbox.api.fields import ContentTypeField
from netbox.api.serializers import NetBoxModelSerializer
from rest_framework import serializers
from utilities.api import get_serializer_for_model

from ..derivation import validate_location_name
from ..models import (
    ASSIGNMENT_MODELS,
    MonitoredService,
    MonitoringProfile,
    object_ip_pks,
    profile_ip_pks,
)


class MonitoringProfileSerializer(NetBoxModelSerializer):
    url = serializers.HyperlinkedIdentityField(
        view_name="plugins-api:netbox_opennms-api:monitoringprofile-detail"
    )
    assigned_object_type = ContentTypeField(
        queryset=ContentType.objects.filter(ASSIGNMENT_MODELS)
    )
    assigned_object = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = MonitoringProfile
        fields = (
            "id",
            "url",
            "display",
            "assigned_object_type",
            "assigned_object_id",
            "assigned_object",
            "management_ip",
            "additional_ips",
            "location",
            "enabled",
            "tags",
            "custom_fields",
            "created",
            "last_updated",
        )
        brief_fields = ("id", "url", "display", "enabled")

    def validate_location(self, value):
        # Serializers don't run Model.clean(); enforce the AD-9 name rule here.
        try:
            validate_location_name(value)
        except ValueError as exc:
            raise serializers.ValidationError(str(exc)) from exc
        return value

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
            duplicate = MonitoringProfile.objects.filter(
                assigned_object_type=content_type,
                assigned_object_id=object_id,
            )
            if self.instance is not None:
                duplicate = duplicate.exclude(pk=self.instance.pk)
            if duplicate.exists():
                raise serializers.ValidationError(
                    "This object already has a Monitoring Profile."
                )

            # Additional IPs must belong to the object and must not duplicate the
            # management IP (AD-15) — mirror the form's guard on the API path.
            additional = data.get("additional_ips")
            if additional:
                target = model.objects.get(pk=object_id)
                owned = object_ip_pks(target)
                management_ip = data.get("management_ip") or getattr(
                    self.instance, "management_ip", None
                )
                management_pk = management_ip.pk if management_ip is not None else None
                filtered = [ip for ip in additional if ip.pk != management_pk]
                foreign = [ip for ip in filtered if ip.pk not in owned]
                if foreign:
                    raise serializers.ValidationError(
                        {
                            "additional_ips": "These IPs are not assigned to the "
                            "object: " + ", ".join(str(ip) for ip in foreign)
                        }
                    )
                data["additional_ips"] = filtered
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
            "profile",
            "ip_address",
            "name",
            "tags",
            "custom_fields",
            "created",
            "last_updated",
        )
        brief_fields = ("id", "url", "display", "name")

    def validate(self, data):
        data = super().validate(data)
        profile = data.get("profile") or getattr(self.instance, "profile", None)
        ip_address = data.get("ip_address") or getattr(
            self.instance, "ip_address", None
        )
        if (
            profile is not None
            and ip_address is not None
            and ip_address.pk not in profile_ip_pks(profile)
        ):
            raise serializers.ValidationError(
                {
                    "ip_address": "The IP must be the profile's management IP or "
                    "one of its additional IPs."
                }
            )
        return data
