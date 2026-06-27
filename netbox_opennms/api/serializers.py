# Copyright 2026 Ronny Trommer <ronny@no42.org>
# SPDX-License-Identifier: Apache-2.0
"""REST API serializers."""

from django.contrib.contenttypes.models import ContentType
from netbox.api.fields import ContentTypeField
from netbox.api.serializers import NetBoxModelSerializer
from rest_framework import serializers
from utilities.api import get_serializer_for_model

from ..models import ASSIGNMENT_MODELS, MonitoringProfile


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
            "enabled",
            "tags",
            "custom_fields",
            "created",
            "last_updated",
        )
        brief_fields = ("id", "url", "display", "enabled")

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
                object_ip_pks = set()
                for interface in target.interfaces.all():
                    object_ip_pks.update(
                        interface.ip_addresses.values_list("pk", flat=True)
                    )
                management_ip = data.get("management_ip") or getattr(
                    self.instance, "management_ip", None
                )
                management_pk = management_ip.pk if management_ip is not None else None
                filtered = [ip for ip in additional if ip.pk != management_pk]
                foreign = [ip for ip in filtered if ip.pk not in object_ip_pks]
                if foreign:
                    raise serializers.ValidationError(
                        {
                            "additional_ips": "These IPs are not assigned to the "
                            "object: " + ", ".join(str(ip) for ip in foreign)
                        }
                    )
                data["additional_ips"] = filtered
        return data
