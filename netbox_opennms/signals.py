# Copyright 2026 Ronny Trommer <ronny@no42.org>
# SPDX-License-Identifier: MIT
"""Signal handlers (Epic 5).

A GenericForeignKey has no database-level cascade, so deleting a monitored
Device or VirtualMachine would otherwise leave an orphaned MonitoringOverride
pointing at a non-existent object. And an override's explicit services must sit
on one of its own interfaces (its management IP or an additional IP) — when an
IP leaves the override, its dangling services are pruned so stored intent
matches what is rendered (AD-15).
"""

from dcim.models import Device
from django.contrib.contenttypes.models import ContentType
from django.db.models.signals import m2m_changed, post_delete, post_save
from django.dispatch import receiver
from virtualization.models import VirtualMachine

from .models import MonitoringOverride, override_ip_pks


@receiver(post_delete, sender=Device)
@receiver(post_delete, sender=VirtualMachine)
def delete_orphaned_overrides(sender, instance, **kwargs):
    content_type = ContentType.objects.get_for_model(sender)
    MonitoringOverride.objects.filter(
        assigned_object_type=content_type,
        assigned_object_id=instance.pk,
    ).delete()


def _prune_orphaned_services(override):
    """Delete an override's services whose IP is no longer one of its IPs (AD-15)."""
    override.services.exclude(ip_address_id__in=override_ip_pks(override)).delete()


@receiver(post_save, sender=MonitoringOverride)
def prune_services_on_override_save(sender, instance, **kwargs):
    # Catches a management_ip change (its old-IP services orphan).
    _prune_orphaned_services(instance)


@receiver(m2m_changed, sender=MonitoringOverride.additional_ips.through)
def prune_services_on_additional_ips_change(sender, instance, action, **kwargs):
    # Catches additional IPs being removed/cleared.
    if action in ("post_remove", "post_clear"):
        _prune_orphaned_services(instance)
