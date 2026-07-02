# Copyright 2026 Ronny Trommer <ronny@no42.org>
# SPDX-License-Identifier: MIT
"""Seed the quickstart NetBox with test Devices, VMs, and the Requisition model.

Run it with `quickstart/seed.sh`, or by hand inside the NetBox container (the ORM
+ plugin are available there) — from the quickstart/ directory:

    docker compose exec -T netbox \\
      /opt/netbox/venv/bin/python manage.py shell < seed.py

Idempotent — every object is get_or_create'd, so re-running is safe.

It builds enough to exercise every path: user-named **Requisitions** (each a
disjoint filter over Devices/VMs + inline detectors/policies + declared
services), per-object **Monitoring Overrides** (extra IPs, an added service, a
suppressed declared service, an excluded object, an OpenNMS-unknown location to
trip the no-Minion warning), a multi-node Foreign Source mixing devices and VMs,
and an object in no requisition (unmonitored). Filters are pairwise disjoint —
an overlap would be a blocking conflict. A node's management IP is its primary
IP.
"""

from dcim.models import (
    Device,
    DeviceRole,
    DeviceType,
    Interface,
    Manufacturer,
    Site,
)
from django.contrib.contenttypes.models import ContentType
from ipam.models import IPAddress
from virtualization.models import (
    Cluster,
    ClusterType,
    VirtualMachine,
    VMInterface,
)

from netbox_opennms.membership import monitored_foreign_sources, resolve
from netbox_opennms.models import (
    MonitoredService,
    MonitoringDetector,
    MonitoringOverride,
    MonitoringPolicy,
    Requisition,
)
from netbox_opennms.presets import resolve_detector, resolve_policy

# --- helpers (idempotent) --------------------------------------------------


def site(name, slug):
    return Site.objects.get_or_create(
        slug=slug, defaults={"name": name, "status": "active"}
    )[0]


def role(name, slug, color):
    return DeviceRole.objects.get_or_create(
        slug=slug, defaults={"name": name, "color": color}
    )[0]


def device_type(model, slug, mfr):
    return DeviceType.objects.get_or_create(
        slug=slug, defaults={"model": model, "manufacturer": mfr}
    )[0]


def device(name, a_site, a_role, dtype):
    return Device.objects.get_or_create(
        name=name,
        defaults={
            "site": a_site,
            "role": a_role,
            "device_type": dtype,
            "status": "active",
        },
    )[0]


def vm(name, cluster, a_role):
    return VirtualMachine.objects.get_or_create(
        name=name,
        defaults={"cluster": cluster, "role": a_role, "status": "active"},
    )[0]


def iface_ip(parent, ifname, cidr, *, primary=False):
    """Create (or fetch) an interface + an IP; optionally mark it the primary IP."""
    if isinstance(parent, VirtualMachine):
        interface = VMInterface.objects.get_or_create(
            virtual_machine=parent, name=ifname
        )[0]
    else:
        interface = Interface.objects.get_or_create(
            device=parent, name=ifname, defaults={"type": "virtual"}
        )[0]
    ip = IPAddress.objects.get_or_create(
        address=cidr, defaults={"assigned_object": interface, "status": "active"}
    )[0]
    if primary and parent.primary_ip4_id is None:
        parent.primary_ip4 = ip
        parent.save()
    return ip


def requisition(
    name,
    filter_params,
    *,
    detectors=(),
    policies=(),
    services=(),
    location="",
    object_types="both",
):
    """A user-named Requisition + its inline detectors/policies (from the presets)."""
    req = Requisition.objects.get_or_create(
        name=name,
        defaults={
            "object_types": object_types,
            "filter_params": filter_params,
            "services": list(services),
            "location": location,
        },
    )[0]
    for det_name, preset in detectors:
        cls, params = resolve_detector(preset)
        MonitoringDetector.objects.get_or_create(
            requisition=req,
            name=det_name,
            defaults={"preset": preset, "rule_class": cls, "parameters": params},
        )
    for pol_name, preset in policies:
        cls, params = resolve_policy(preset)
        MonitoringPolicy.objects.get_or_create(
            requisition=req,
            name=pol_name,
            defaults={"preset": preset, "rule_class": cls, "parameters": params},
        )
    return req


def override(
    target, *, exclude=False, additional=(), location="", services=(), suppressed=()
):
    ov = MonitoringOverride.objects.get_or_create(
        assigned_object_type=ContentType.objects.get_for_model(target),
        assigned_object_id=target.pk,
        defaults={
            "exclude": exclude,
            "location": location,
            "suppressed_services": list(suppressed),
        },
    )[0]
    if additional:
        ov.additional_ips.set(additional)
    for ip, name in services:
        MonitoredService.objects.get_or_create(override=ov, ip_address=ip, name=name)
    return ov


# --- build the world -------------------------------------------------------

raleigh = site("Raleigh", "raleigh")
durham = site("Durham", "durham")
asheville = site("Asheville", "asheville")

router = role("Router", "router", "2196f3")
switch = role("Switch", "switch", "4caf50")
firewall = role("Firewall", "firewall", "f44336")

acme = Manufacturer.objects.get_or_create(slug="acme", defaults={"name": "Acme"})[0]
vsr = device_type("VSR-100", "vsr-100", acme)
vsw = device_type("VSW-50", "vsw-50", acme)

# Devices — eth0 is the management (primary) interface; eth1/lo0 are extras.
rtr1 = device("rtr-1", raleigh, router, vsr)
rtr1_m = iface_ip(rtr1, "eth0", "198.51.100.11/24", primary=True)
rtr1_e1 = iface_ip(rtr1, "eth1", "198.51.100.111/24")
rtr1_lo = iface_ip(rtr1, "lo0", "192.0.2.1/32")

rtr2 = device("rtr-2", raleigh, router, vsr)
iface_ip(rtr2, "eth0", "198.51.100.12/24", primary=True)

sw1 = device("sw-1", raleigh, switch, vsw)
iface_ip(sw1, "eth0", "198.51.100.21/24", primary=True)

fw1 = device("fw-1", raleigh, firewall, vsr)
iface_ip(fw1, "eth0", "198.51.100.31/24", primary=True)

rtr3 = device("rtr-3", durham, router, vsr)
iface_ip(rtr3, "eth0", "203.0.113.11/24", primary=True)

# An UNMONITORED object: a durham switch — no requisition matches it.
swd = device("sw-durham", durham, switch, vsw)
iface_ip(swd, "eth0", "203.0.113.31/24", primary=True)

sw2 = device("sw-2", asheville, switch, vsw)
iface_ip(sw2, "eth0", "203.0.113.21/24", primary=True)

# A cluster scoped to Raleigh, so its VMs sit in Raleigh and join the Raleigh
# requisitions (a mixed Device+VM requisition).
kvm = ClusterType.objects.get_or_create(slug="kvm", defaults={"name": "KVM"})[0]
vmhost = Cluster.objects.get_or_create(
    name="vmhost-1", defaults={"type": kvm, "status": "active"}
)[0]
vmhost.scope = raleigh
vmhost.save()

vm1 = vm("vm-1", vmhost, router)
vm1_m = iface_ip(vm1, "eth0", "198.51.100.41/24", primary=True)
vm1_e1 = iface_ip(vm1, "eth1", "198.51.100.141/24")

vm2 = vm("vm-2", vmhost, router)
iface_ip(vm2, "eth0", "198.51.100.42/24", primary=True)

vm3 = vm("vm-3", vmhost, switch)
iface_ip(vm3, "eth0", "198.51.100.51/24", primary=True)

# --- requisitions (name = Foreign Source; filter = membership) -------------

# Raleigh routers + switches: monitored as network devices (ICMP + SNMP declared).
requisition(
    "netbox.raleigh.router",
    {"site": ["raleigh"], "role": ["router"]},
    detectors=[("ICMP", "icmp"), ("SNMP", "snmp")],
    policies=[("Categorise", "set-node-category")],
    services=["ICMP", "SNMP"],
    location="Default",
)
requisition(
    "netbox.raleigh.switch",
    {"site": ["raleigh"], "role": ["switch"]},
    detectors=[("ICMP", "icmp"), ("SNMP", "snmp")],
    services=["ICMP", "SNMP"],
)
# Firewalls use a location OpenNMS won't know → no-Minion warning.
requisition(
    "netbox.raleigh.firewall",
    {"site": ["raleigh"], "role": ["firewall"]},
    detectors=[("ICMP", "icmp")],
    services=["ICMP"],
    location="edge-rdu",
)
# Durham: only the routers (sw-durham stays unmonitored — no requisition matches).
requisition(
    "netbox.durham.router",
    {"site": ["durham"], "role": ["router"]},
    detectors=[("ICMP", "icmp"), ("SNMP", "snmp")],
    services=["ICMP", "SNMP"],
)
# Asheville switches.
requisition(
    "netbox.asheville.switch",
    {"site": ["asheville"], "role": ["switch"]},
    detectors=[("ICMP", "icmp"), ("SNMP", "snmp")],
    services=["ICMP", "SNMP"],
)

# --- per-object overrides --------------------------------------------------

# rtr-1: monitor two extra interfaces + an explicit HTTP service on eth1.
override(rtr1, additional=[rtr1_e1, rtr1_lo], services=[(rtr1_e1, "HTTP")],
         location="Default")
# rtr-2: suppress the declared SNMP service for this one device.
override(rtr2, suppressed=["SNMP"])
# vm-1: one extra interface.
override(vm1, additional=[vm1_e1])
# sw-2: explicitly excluded despite matching netbox.asheville.switch.
override(sw2, exclude=True)

# --- summary ---------------------------------------------------------------

print("\nSeeded:")
print(
    f"  sites={Site.objects.count()} roles={DeviceRole.objects.count()} "
    f"devices={Device.objects.count()} vms={VirtualMachine.objects.count()} "
    f"requisitions={Requisition.objects.count()} "
    f"overrides={MonitoringOverride.objects.count()}"
)
print("\nForeign Sources (requisitions with members):")
for fs in monitored_foreign_sources():
    resolution = resolve(fs)
    members = ", ".join(sorted(n.node_label for n in resolution.nodes))
    print(f"  {fs:28s} {members or '(no monitorable members)'}")
print("\nUnmonitored: sw-durham (no requisition) · sw-2 (override excludes it)")
print(
    "Now open http://localhost:8000/plugins/opennms/sync/ and Sync.\n"
)
