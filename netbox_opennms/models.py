# Copyright 2026 Ronny Trommer <ronny@no42.org>
# SPDX-License-Identifier: Apache-2.0
"""Plugin data models."""

from django.contrib.contenttypes.fields import GenericForeignKey
from django.core.exceptions import ValidationError
from django.db import models
from django.urls import reverse
from netbox.models import NetBoxModel

from .choices import ServiceChoices

# A Monitoring Profile may be assigned to a Device or a VirtualMachine.
ASSIGNMENT_MODELS = models.Q(
    models.Q(app_label="dcim", model="device")
    | models.Q(app_label="virtualization", model="virtualmachine")
)


class MonitoringProfile(NetBoxModel):
    """Monitoring intent for a single Device or VirtualMachine.

    Attaches to its target via a GenericForeignKey (AD-7). At most one profile
    may exist per object (enforced by a unique constraint, which also supplies
    the GFK index). ``enabled`` toggles whether the object is rendered into an
    OpenNMS requisition; disabling retains the profile (removal is handled by a
    later story via render-and-replace).
    """

    # The GFK's backing ForeignKey must point to Django's native ContentType,
    # never NetBox's ObjectType proxy.
    assigned_object_type = models.ForeignKey(
        to="contenttypes.ContentType",
        on_delete=models.PROTECT,
        limit_choices_to=ASSIGNMENT_MODELS,
        related_name="+",
    )
    assigned_object_id = models.PositiveBigIntegerField()
    assigned_object = GenericForeignKey(
        ct_field="assigned_object_type",
        fk_field="assigned_object_id",
    )
    enabled = models.BooleanField(default=True)
    # The primary interface OpenNMS polls (snmp-primary="P" at render time).
    # Nullable so a missing IP is caught by validation, not a DB error, and so
    # deleting the referenced IP clears it (SET_NULL) rather than being blocked.
    management_ip = models.ForeignKey(
        to="ipam.IPAddress",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    # The object's other monitored IPs — rendered as non-primary (snmp-primary="N")
    # interfaces (AD-15). The management IP is NOT stored here; the renderer is the
    # single interface-set authority and excludes it even if mistakenly added.
    additional_ips = models.ManyToManyField(
        to="ipam.IPAddress",
        related_name="+",
        blank=True,
    )

    class Meta:
        ordering = ("pk",)
        constraints = [
            models.UniqueConstraint(
                fields=("assigned_object_type", "assigned_object_id"),
                name="%(app_label)s_%(class)s_unique_object",
            ),
        ]
        verbose_name = "monitoring profile"
        verbose_name_plural = "monitoring profiles"

    def __str__(self):
        if self.assigned_object is not None:
            return f"{self.assigned_object}"
        return "Monitoring profile"

    def get_absolute_url(self):
        return reverse("plugins:netbox_opennms:monitoringprofile", args=[self.pk])


def profile_ip_pks(profile):
    """PKs of a profile's monitored IPs: the management IP + the additional IPs.

    A node's interfaces are exactly these IPs (AD-15), so a MonitoredService may
    only sit on one of them.
    """
    pks = set()
    if profile.management_ip_id:
        pks.add(profile.management_ip_id)
    pks.update(profile.additional_ips.values_list("pk", flat=True))
    return pks


class MonitoredService(NetBoxModel):
    """A service monitored on one interface (IP) of a MonitoringProfile (AD-15).

    Only the explicit services here are monitored — auto-detection stays disabled
    (AD-11). The ``name`` is drawn from the admin-extensible ``ServiceChoices``.
    """

    profile = models.ForeignKey(
        to=MonitoringProfile,
        on_delete=models.CASCADE,
        related_name="services",
    )
    # The interface this service runs on — must be one of the profile's monitored
    # IPs (management IP or an additional IP).
    ip_address = models.ForeignKey(
        to="ipam.IPAddress",
        on_delete=models.CASCADE,
        related_name="+",
    )
    name = models.CharField(max_length=100, choices=ServiceChoices)

    class Meta:
        ordering = ("profile", "ip_address", "name")
        constraints = [
            models.UniqueConstraint(
                fields=("profile", "ip_address", "name"),
                name="%(app_label)s_%(class)s_unique_service",
            ),
        ]
        verbose_name = "monitored service"
        verbose_name_plural = "monitored services"

    def __str__(self):
        return f"{self.name} on {self.ip_address}"

    def get_absolute_url(self):
        return reverse("plugins:netbox_opennms:monitoredservice", args=[self.pk])

    def clean(self):
        super().clean()
        if (
            self.profile_id
            and self.ip_address_id
            and self.ip_address_id not in profile_ip_pks(self.profile)
        ):
            raise ValidationError(
                {
                    "ip_address": (
                        "The IP must be the profile's management IP or one of "
                        "its additional IPs."
                    )
                }
            )
