# Copyright 2026 Ronny Trommer <ronny@no42.org>
# SPDX-License-Identifier: Apache-2.0
"""Forms for plugin models."""

from dcim.models import Device
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _
from ipam.models import IPAddress
from netbox.forms import NetBoxModelForm
from utilities.forms.fields import (
    DynamicModelChoiceField,
    DynamicModelMultipleChoiceField,
)
from virtualization.models import VirtualMachine

from .models import (
    MonitoredService,
    MonitoringProfile,
    object_ip_pks,
    profile_ip_pks,
)


class MonitoringProfileForm(NetBoxModelForm):
    """Create/edit a Monitoring Profile.

    NetBox core has no unified GenericForeignKey form field, so the target is
    selected through two optional fields (Device / Virtual Machine) of which
    exactly one must be set; ``clean`` maps the choice onto ``assigned_object``.
    """

    device = DynamicModelChoiceField(
        queryset=Device.objects.all(),
        required=False,
        label=_("Device"),
    )
    virtual_machine = DynamicModelChoiceField(
        queryset=VirtualMachine.objects.all(),
        required=False,
        label=_("Virtual Machine"),
    )
    management_ip = DynamicModelChoiceField(
        queryset=IPAddress.objects.all(),
        required=False,
        label=_("Management IP"),
        help_text=_("Defaults to the object's primary IP if left blank."),
    )
    additional_ips = DynamicModelMultipleChoiceField(
        queryset=IPAddress.objects.all(),
        required=False,
        label=_("Additional IPs"),
        query_params={
            "device_id": "$device",
            "virtual_machine_id": "$virtual_machine",
        },
        help_text=_("Other IPs of this object to monitor as non-primary interfaces."),
    )

    class Meta:
        model = MonitoringProfile
        fields = (
            "device",
            "virtual_machine",
            "management_ip",
            "additional_ips",
            "location",
            "enabled",
            "tags",
        )

    def __init__(self, *args, **kwargs):
        instance = kwargs.get("instance")
        initial = kwargs.get("initial", {}).copy()
        if instance is not None and instance.assigned_object_id:
            obj = instance.assigned_object
            if isinstance(obj, Device):
                initial.setdefault("device", obj)
            elif isinstance(obj, VirtualMachine):
                initial.setdefault("virtual_machine", obj)
            if not instance.management_ip_id and obj is not None and obj.primary_ip:
                initial.setdefault("management_ip", obj.primary_ip)
        kwargs["initial"] = initial
        super().__init__(*args, **kwargs)

    def clean(self):
        super().clean()
        device = self.cleaned_data.get("device")
        virtual_machine = self.cleaned_data.get("virtual_machine")
        if bool(device) == bool(virtual_machine):
            raise ValidationError(_("Select exactly one of Device or Virtual Machine."))
        target = device or virtual_machine
        self.instance.assigned_object = target

        # The unique constraint references assigned_object_type/_id, which are
        # not form fields, so Django's validate_unique would skip it — surface a
        # clean error here instead of a database IntegrityError.
        content_type = ContentType.objects.get_for_model(target)
        duplicate = (
            MonitoringProfile.objects.filter(
                assigned_object_type=content_type,
                assigned_object_id=target.pk,
            )
            .exclude(pk=self.instance.pk)
            .exists()
        )
        if duplicate:
            raise ValidationError(_("This object already has a Monitoring Profile."))

        # Resolve the management IP: explicit choice, else the object's primary
        # IP. Write it back to cleaned_data so the model instance is constructed
        # with the resolved value (management_ip is a form field, so setting it
        # on self.instance directly would be overwritten by _post_clean).
        management_ip = self.cleaned_data.get("management_ip") or target.primary_ip
        if management_ip is None:
            raise ValidationError(
                {
                    "management_ip": _(
                        "No management IP set and the object has no primary IP."
                    )
                }
            )
        self.cleaned_data["management_ip"] = management_ip

        # Additional IPs must belong to the monitored object (AD-15) and must not
        # duplicate the management IP (which is the primary "P" interface).
        additional = self.cleaned_data.get("additional_ips")
        if additional:
            # Drop the management IP first — it's the lone primary (no duplicate
            # P+N), and it may legitimately be off the object's interfaces
            # (Story 1.3), so excluding it before the membership check avoids a
            # false "not assigned" error.
            additional = [ip for ip in additional if ip.pk != management_ip.pk]
            owned = object_ip_pks(target)
            foreign = [ip for ip in additional if ip.pk not in owned]
            if foreign:
                raise ValidationError(
                    {
                        "additional_ips": _(
                            "These IPs are not assigned to the selected object: %(ips)s"
                        )
                        % {"ips": ", ".join(str(ip) for ip in foreign)}
                    }
                )
            self.cleaned_data["additional_ips"] = additional
        return self.cleaned_data


class MonitoredServiceForm(NetBoxModelForm):
    """Add/edit a service monitored on one interface of a profile."""

    profile = DynamicModelChoiceField(
        queryset=MonitoringProfile.objects.all(),
        label=_("Monitoring Profile"),
    )
    ip_address = DynamicModelChoiceField(
        queryset=IPAddress.objects.all(),
        label=_("Interface IP"),
        help_text=_("Must be the profile's management IP or an additional IP."),
    )

    class Meta:
        model = MonitoredService
        fields = ("profile", "ip_address", "name", "tags")

    def clean(self):
        super().clean()
        profile = self.cleaned_data.get("profile")
        ip_address = self.cleaned_data.get("ip_address")
        if profile and ip_address and ip_address.pk not in profile_ip_pks(profile):
            raise ValidationError(
                {
                    "ip_address": _(
                        "The IP must be the profile's management IP or one of "
                        "its additional IPs."
                    )
                }
            )
        return self.cleaned_data
