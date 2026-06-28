# Copyright 2026 Ronny Trommer <ronny@no42.org>
# SPDX-License-Identifier: MIT
"""Signal handlers.

A GenericForeignKey has no database-level cascade, so deleting a monitored
Device or VirtualMachine would otherwise leave an orphaned MonitoringProfile
pointing at a non-existent object. These handlers clean those up.
"""

from dcim.models import Device
from django.contrib.contenttypes.models import ContentType
from django.db.models.signals import m2m_changed, post_delete, post_save
from django.dispatch import receiver
from virtualization.models import VirtualMachine

from .models import MonitoringProfile, profile_ip_pks


@receiver(post_delete, sender=Device)
@receiver(post_delete, sender=VirtualMachine)
def delete_orphaned_monitoring_profiles(sender, instance, **kwargs):
    content_type = ContentType.objects.get_for_model(sender)
    MonitoringProfile.objects.filter(
        assigned_object_type=content_type,
        assigned_object_id=instance.pk,
    ).delete()


def _prune_orphaned_services(profile):
    """Delete MonitoredServices whose IP is no longer a monitored IP (AD-15).

    A service can only sit on an interface the node actually has (the management
    IP or an additional IP). When an IP leaves the profile, its services would
    otherwise dangle and silently drop from the requisition — remove them so
    stored intent matches what is rendered.
    """
    profile.services.exclude(ip_address_id__in=profile_ip_pks(profile)).delete()


@receiver(post_save, sender=MonitoringProfile)
def prune_services_on_profile_save(sender, instance, **kwargs):
    # Catches a management_ip change (its old-IP services orphan).
    _prune_orphaned_services(instance)


@receiver(m2m_changed, sender=MonitoringProfile.additional_ips.through)
def prune_services_on_additional_ips_change(sender, instance, action, **kwargs):
    # Catches additional IPs being removed/cleared.
    if action in ("post_remove", "post_clear"):
        _prune_orphaned_services(instance)
