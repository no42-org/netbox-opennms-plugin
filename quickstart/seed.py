# Copyright 2026 Ronny Trommer <ronny@no42.org>
# SPDX-License-Identifier: MIT
"""Seed the quickstart NetBox with test Devices, VMs, and Monitoring Profiles.

Run it with `quickstart/seed.sh`, or by hand inside the NetBox container (the ORM
+ plugin are available there) — from the quickstart/ directory:

    docker compose exec -T netbox \\
      /opt/netbox/venv/bin/python manage.py shell < seed.py

Idempotent — every object is get_or_create'd, so re-running is safe.

It builds enough to exercise every plugin path: multiple sites/roles, devices
with several interfaces + additional IPs, VMs (via a site-scoped cluster), a
multi-node Foreign Source mixing devices and VMs, monitored services, a valid
and an OpenNMS-unknown location (to trip the no-Minion warning), and an enabled,
a disabled, and an unmonitored object.
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

from netbox_opennms.derivation import foreign_source_for
from netbox_opennms.models import MonitoredService, MonitoringProfile

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


def iface_ip(parent, ifname, cidr):
    """Create (or fetch) an interface on a Device/VM and an IP assigned to it."""
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
    return ip


def profile(target, mgmt_ip, *, enabled=True, location="", additional=(), services=()):
    """Create (or fetch) a MonitoringProfile for a Device/VM and wire its intent.

    services: iterable of (ip, service_name). ips in services/additional must be
    the object's own addresses (the renderer is the interface authority, AD-15).
    """
    ct = ContentType.objects.get_for_model(target)
    prof = MonitoringProfile.objects.get_or_create(
        assigned_object_type=ct,
        assigned_object_id=target.pk,
        defaults={
            "management_ip": mgmt_ip,
            "enabled": enabled,
            "location": location,
        },
    )[0]
    if additional:
        prof.additional_ips.set(additional)
    for ip, name in services:
        MonitoredService.objects.get_or_create(profile=prof, ip_address=ip, name=name)
    return prof


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

# Devices (eth0 = management; eth1/lo0 = additional monitored interfaces).
rtr1 = device("rtr-1", raleigh, router, vsr)
rtr1_m = iface_ip(rtr1, "eth0", "198.51.100.11/24")
rtr1_e1 = iface_ip(rtr1, "eth1", "198.51.100.111/24")
rtr1_lo = iface_ip(rtr1, "lo0", "192.0.2.1/32")

rtr2 = device("rtr-2", raleigh, router, vsr)
rtr2_m = iface_ip(rtr2, "eth0", "198.51.100.12/24")
rtr2_e1 = iface_ip(rtr2, "eth1", "198.51.100.112/24")

sw1 = device("sw-1", raleigh, switch, vsw)
sw1_m = iface_ip(sw1, "eth0", "198.51.100.21/24")

fw1 = device("fw-1", raleigh, firewall, vsr)
fw1_m = iface_ip(fw1, "eth0", "198.51.100.31/24")
iface_ip(fw1, "eth1", "198.51.100.131/24")

rtr3 = device("rtr-3", durham, router, vsr)
rtr3_m = iface_ip(rtr3, "eth0", "203.0.113.11/24")
rtr3_e1 = iface_ip(rtr3, "eth1", "203.0.113.111/24")

sw2 = device("sw-2", asheville, switch, vsw)
sw2_m = iface_ip(sw2, "eth0", "203.0.113.21/24")

# A cluster scoped to Raleigh, so its VMs derive netbox.raleigh.<role> and join
# the devices in those Foreign Sources (a mixed Device+VM requisition).
kvm = ClusterType.objects.get_or_create(slug="kvm", defaults={"name": "KVM"})[0]
vmhost = Cluster.objects.get_or_create(
    name="vmhost-1", defaults={"type": kvm, "status": "active"}
)[0]
vmhost.scope = raleigh
vmhost.save()

vm1 = vm("vm-1", vmhost, router)
vm1_m = iface_ip(vm1, "eth0", "198.51.100.41/24")
vm1_e1 = iface_ip(vm1, "eth1", "198.51.100.141/24")

vm2 = vm("vm-2", vmhost, router)
vm2_m = iface_ip(vm2, "eth0", "198.51.100.42/24")

vm3 = vm("vm-3", vmhost, switch)
vm3_m = iface_ip(vm3, "eth0", "198.51.100.51/24")

# Monitoring Profiles — intent to push to OpenNMS.
profile(
    rtr1, rtr1_m, location="Default", additional=[rtr1_e1, rtr1_lo],
    services=[(rtr1_m, "ICMP"), (rtr1_m, "SNMP"), (rtr1_e1, "HTTP")],
)
profile(rtr2, rtr2_m, additional=[rtr2_e1], services=[(rtr2_m, "ICMP")])
profile(sw1, sw1_m, services=[(sw1_m, "ICMP"), (sw1_m, "SNMP")])
# An OpenNMS-unknown location → trips the best-effort no-Minion warning (4.1).
profile(fw1, fw1_m, location="edge-rdu")
profile(rtr3, rtr3_m, additional=[rtr3_e1])
profile(vm1, vm1_m, additional=[vm1_e1], services=[(vm1_m, "ICMP")])
profile(vm2, vm2_m)
# Disabled → shows the "removed"/not-monitored observability state.
profile(sw2, sw2_m, enabled=False)
# (vm-3 intentionally has NO profile — shows an unmonitored object.)

# --- summary ---------------------------------------------------------------

print("\nSeeded:")
print(f"  sites={Site.objects.count()} roles={DeviceRole.objects.count()} "
      f"devices={Device.objects.count()} vms={VirtualMachine.objects.count()} "
      f"profiles={MonitoringProfile.objects.count()} "
      f"services={MonitoredService.objects.count()}")
print("\nForeign Sources (from enabled profiles):")
fs_members = {}
for p in MonitoringProfile.objects.filter(enabled=True):
    fs_members.setdefault(foreign_source_for(p.assigned_object), []).append(
        str(p.assigned_object)
    )
for fs in sorted(fs_members):
    print(f"  {fs:28s} {', '.join(sorted(fs_members[fs]))}")
print("\nNot monitored: vm-3 (no profile) · sw-2 (profile disabled)")
print("Now open http://localhost:8000/plugins/opennms/monitoring-profiles/ "
      "and Sync.\n")
