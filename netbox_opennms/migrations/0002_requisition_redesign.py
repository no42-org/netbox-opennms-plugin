# Copyright 2026 Ronny Trommer <ronny@no42.org>
# SPDX-License-Identifier: MIT
"""Replace MonitoringProfile + MonitoringAssignment with a user-named Requisition.

Data migration (R8): seed one name-preserving Requisition per existing Foreign
Source so first sync is a no-op. A concrete (site, role) assignment → one
Requisition named ``netbox.{site}.{role}``; a site-level assignment fans out to
one per role present among the site's Devices (H2). Profiles are copied INLINE
into each seeded Requisition (R4). Overrides gain a nullable ``requisition`` FK
(H3) — left NULL here; resolution applies an override by object, so a NULL FK is
harmless and dormant overrides are never dropped.

MUST be validated with ``make makemigrations`` (no missing migrations) and
``make test`` before shipping — the highest-risk artifact in this change.
"""

import django.db.models.deletion
import taggit.managers
import utilities.json
from django.db import migrations, models


def seed_requisitions(apps, schema_editor):
    Requisition = apps.get_model("netbox_opennms", "Requisition")
    MonitoringAssignment = apps.get_model("netbox_opennms", "MonitoringAssignment")
    MonitoringDetector = apps.get_model("netbox_opennms", "MonitoringDetector")
    MonitoringPolicy = apps.get_model("netbox_opennms", "MonitoringPolicy")
    Device = apps.get_model("dcim", "Device")
    VirtualMachine = apps.get_model("virtualization", "VirtualMachine")

    def seed(name, location, profile, filter_params, priority, seen):
        if name in seen:
            return
        seen.add(name)
        requisition = Requisition.objects.create(
            name=name,
            description=profile.description,
            priority=priority,
            object_types="both",
            filter_params=filter_params,
            scan_interval=profile.scan_interval,
            default_interfaces=profile.default_interfaces,
            services=[],
            location=location or "",
        )
        # Inline-copy the profile's rules (new rows; originals still have a NULL
        # requisition and are deleted below).
        for detector in MonitoringDetector.objects.filter(
            profile=profile, requisition__isnull=True
        ):
            MonitoringDetector.objects.create(
                profile=profile,
                requisition=requisition,
                name=detector.name,
                preset=detector.preset,
                rule_class=detector.rule_class,
                parameters=detector.parameters,
            )
        for policy in MonitoringPolicy.objects.filter(
            profile=profile, requisition__isnull=True
        ):
            MonitoringPolicy.objects.create(
                profile=profile,
                requisition=requisition,
                name=policy.name,
                preset=policy.preset,
                rule_class=policy.rule_class,
                parameters=policy.parameters,
            )

    seen = set()
    # Concrete (site, role) first so it wins over a site-level fan-out for the same
    # name (reproduces D9 role-beats-site precedence via priority 100 < 200).
    concrete = MonitoringAssignment.objects.filter(
        role__isnull=False
    ).select_related("profile", "site", "role")
    for assignment in concrete:
        name = f"netbox.{assignment.site.slug}.{assignment.role.slug}"
        seed(
            name,
            assignment.location,
            assignment.profile,
            {"site": [assignment.site.slug], "role": [assignment.role.slug]},
            100,
            seen,
        )

    site_level = MonitoringAssignment.objects.filter(
        role__isnull=True
    ).select_related("profile", "site")
    for assignment in site_level:
        # Enumerate roles present among BOTH Devices and VMs directly sited here,
        # so a VM-only role in the site still gets a name-preserving Requisition
        # (a VM sited only via cluster.scope is the H6 follow-up).
        roles = {}
        for device in Device.objects.filter(
            site=assignment.site, role__isnull=False
        ).select_related("role"):
            roles[device.role.slug] = device.role
        for vm in VirtualMachine.objects.filter(
            site=assignment.site, role__isnull=False
        ).select_related("role"):
            roles[vm.role.slug] = vm.role
        for slug in roles:
            name = f"netbox.{assignment.site.slug}.{slug}"
            seed(
                name,
                assignment.location,
                assignment.profile,
                {"site": [assignment.site.slug], "role": [slug]},
                200,
                seen,
            )

    # Drop the original profile-linked detectors/policies (they never received a
    # requisition); their inline copies now carry the intent.
    MonitoringDetector.objects.filter(requisition__isnull=True).delete()
    MonitoringPolicy.objects.filter(requisition__isnull=True).delete()


def noop(apps, schema_editor):
    # Pre-1.0: rollback is a DB restore, not a reversible data migration (L1).
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("netbox_opennms", "0001_initial"),
        ("dcim", "0237_module_remove_local_context_data"),
        ("extras", "0139_alter_customfieldchoiceset_extra_choices"),
    ]

    operations = [
        migrations.CreateModel(
            name="Requisition",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                ("created", models.DateTimeField(auto_now_add=True, null=True)),
                ("last_updated", models.DateTimeField(auto_now=True, null=True)),
                ("custom_field_data", models.JSONField(blank=True, default=dict, encoder=utilities.json.CustomFieldJSONEncoder)),
                ("name", models.CharField(max_length=100, unique=True)),
                ("description", models.CharField(blank=True, max_length=200)),
                ("priority", models.PositiveIntegerField(db_index=True, default=100)),
                ("object_types", models.CharField(default="both", max_length=10)),
                ("filter_params", models.JSONField(blank=True, default=dict)),
                ("scan_interval", models.CharField(default="1d", max_length=32)),
                ("default_interfaces", models.CharField(default="primary", max_length=16)),
                ("services", models.JSONField(blank=True, default=list)),
                ("location", models.CharField(blank=True, default="", max_length=255)),
                ("tags", taggit.managers.TaggableManager(through="extras.TaggedItem", to="extras.Tag")),
            ],
            options={
                "verbose_name": "requisition",
                "verbose_name_plural": "requisitions",
                "ordering": ("priority", "pk"),
            },
        ),
        # The (profile, name) uniqueness must go before the data migration, which
        # copies the same profile's rule name into several Requisitions.
        migrations.RemoveConstraint(
            model_name="monitoringdetector",
            name="netbox_opennms_monitoringdetector_unique_name",
        ),
        migrations.RemoveConstraint(
            model_name="monitoringpolicy",
            name="netbox_opennms_monitoringpolicy_unique_name",
        ),
        migrations.AddField(
            model_name="monitoringdetector",
            name="requisition",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="detectors",
                to="netbox_opennms.requisition",
            ),
        ),
        migrations.AddField(
            model_name="monitoringpolicy",
            name="requisition",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="policies",
                to="netbox_opennms.requisition",
            ),
        ),
        migrations.AddField(
            model_name="monitoringoverride",
            name="suppressed_services",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.RunPython(seed_requisitions, noop),
        migrations.RemoveField(model_name="monitoringdetector", name="profile"),
        migrations.RemoveField(model_name="monitoringpolicy", name="profile"),
        migrations.AlterField(
            model_name="monitoringdetector",
            name="requisition",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="detectors",
                to="netbox_opennms.requisition",
            ),
        ),
        migrations.AlterField(
            model_name="monitoringpolicy",
            name="requisition",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="policies",
                to="netbox_opennms.requisition",
            ),
        ),
        migrations.AlterModelOptions(
            name="monitoringdetector",
            options={
                "ordering": ("requisition", "name"),
                "verbose_name": "monitoring detector",
                "verbose_name_plural": "monitoring detectors",
            },
        ),
        migrations.AlterModelOptions(
            name="monitoringpolicy",
            options={
                "ordering": ("requisition", "name"),
                "verbose_name": "monitoring policy",
                "verbose_name_plural": "monitoring policies",
            },
        ),
        migrations.AddConstraint(
            model_name="monitoringdetector",
            constraint=models.UniqueConstraint(
                fields=("requisition", "name"),
                name="netbox_opennms_monitoringdetector_unique_name",
            ),
        ),
        migrations.AddConstraint(
            model_name="monitoringpolicy",
            constraint=models.UniqueConstraint(
                fields=("requisition", "name"),
                name="netbox_opennms_monitoringpolicy_unique_name",
            ),
        ),
        migrations.DeleteModel(name="MonitoringAssignment"),
        migrations.DeleteModel(name="MonitoringProfile"),
    ]
