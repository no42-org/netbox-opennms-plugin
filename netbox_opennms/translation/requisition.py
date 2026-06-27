# Copyright 2026 Ronny Trommer <ronny@no42.org>
# SPDX-License-Identifier: Apache-2.0
"""Render OpenNMS requisition + foreign-source-definition XML (pure, AD-3, AD-5).

This is the render half of render-and-replace. Posting/importing is Story 1.7.
The functions are deterministic and side-effect-free (no network, no DB writes);
they read the passed objects' attributes only. ``date_stamp`` is a parameter so
output is reproducible (the sync job supplies the timestamp).
"""

from collections import defaultdict

from lxml import etree

from ..derivation import foreign_id_for

MODEL_IMPORT_NS = "http://xmlns.opennms.org/xsd/config/model-import"
FOREIGN_SOURCE_NS = "http://xmlns.opennms.org/xsd/config/foreign-source"


class RenderError(Exception):
    """A profile can't be rendered into requisition XML (a required field is None).

    The renderer assumes validated profiles (Story 1.3/2.4), but a field can still
    go missing after the fact — ``management_ip`` is ``SET_NULL`` so deleting the
    IP leaves an enabled profile with ``management_ip=None``; ``Device.name`` is
    nullable; ``assigned_object`` can be cleared by a raw delete. Rather than
    emit malformed XML (or raise an opaque ``AttributeError``/``TypeError``), the
    renderer raises this naming the offending profile + missing field, so the 1.7
    sync job can catch it and fail that sync cleanly.
    """


def _bare_ip(ip):
    """The bare IP (no CIDR mask) for an interface ``ip-addr``.

    ``IPAddress.address`` may be a ``netaddr`` network or a plain string
    (in-memory, pre-DB-roundtrip); stripping the mask off the string form
    handles both.
    """
    return str(ip.address).split("/")[0]


def _management_ip(profile):
    """The bare management IP for the primary interface."""
    return _bare_ip(profile.management_ip)


def _add_services(interface_el, names):
    """Append ``<monitored-service service-name="…">`` children, sorted (AD-3)."""
    for name in sorted(names):
        service = etree.SubElement(
            interface_el, f"{{{MODEL_IMPORT_NS}}}monitored-service"
        )
        service.set("service-name", name)


def render_requisition(foreign_source, profiles, date_stamp=None):
    """Render the complete ``model-import`` requisition for one Foreign Source.

    ``foreign_source`` is the already-derived name (from ``foreign_source_for`` —
    not re-derived here, AD-14). ``profiles`` are the enabled MonitoringProfiles
    grouped under it. Each yields one node with a stable type-qualified Foreign
    ID and its management IP as the primary (``P``) interface. Returns bytes.
    """
    root = etree.Element(
        f"{{{MODEL_IMPORT_NS}}}model-import", nsmap={None: MODEL_IMPORT_NS}
    )
    root.set("foreign-source", foreign_source)
    if date_stamp is not None:
        root.set("date-stamp", date_stamp)

    for profile in profiles:
        target = profile.assigned_object
        if target is None:
            raise RenderError(
                f"MonitoringProfile pk={profile.pk} has no assigned object; "
                "cannot render a node."
            )
        try:
            foreign_id = foreign_id_for(target)
        except TypeError as exc:
            # limit_choices_to is form-only, so an ORM/REST/import-created profile
            # can point at a non-Device/VM. Convert the type signal into the same
            # clean contract so the 1.7 sync fails the sync, not the whole batch.
            raise RenderError(
                f"MonitoringProfile pk={profile.pk} target is not a Device or "
                "VirtualMachine; cannot render a node."
            ) from exc
        if not target.name:
            raise RenderError(
                f"MonitoringProfile pk={profile.pk} target has no name; "
                "node-label is required."
            )
        if profile.management_ip is None:
            raise RenderError(
                f"MonitoringProfile pk={profile.pk} ({target.name}) has no "
                "management IP; a primary interface is required."
            )

        node = etree.SubElement(root, f"{{{MODEL_IMPORT_NS}}}node")
        node.set("node-label", target.name)
        node.set("foreign-id", foreign_id)

        # Services grouped by the interface IP they run on (AD-15).
        services_by_ip = defaultdict(list)
        for service in profile.services.all():
            services_by_ip[service.ip_address_id].append(service.name)

        # The single interface-set authority (AD-15): the management IP is the
        # lone primary ("P"); additional IPs are non-primary ("N"). An IP appears
        # at most once per node, keyed by bare address, so a duplicate address
        # (including the management IP re-listed as additional) merges onto the
        # existing interface rather than emitting a second element or dropping its
        # services. Additional IPs are sorted by bare address for determinism.
        primary_ip = _management_ip(profile)
        interfaces = [(primary_ip, "P", [profile.management_ip_id])]
        index = {primary_ip: 0}
        for ip in sorted(profile.additional_ips.all(), key=_bare_ip):
            bare = _bare_ip(ip)
            if bare in index:
                interfaces[index[bare]][2].append(ip.pk)
            else:
                index[bare] = len(interfaces)
                interfaces.append((bare, "N", [ip.pk]))

        for bare, snmp_primary, ip_pks in interfaces:
            interface = etree.SubElement(node, f"{{{MODEL_IMPORT_NS}}}interface")
            interface.set("ip-addr", bare)
            interface.set("snmp-primary", snmp_primary)
            names = set()
            for ip_pk in ip_pks:
                names.update(services_by_ip.get(ip_pk, []))
            _add_services(interface, names)

    return etree.tostring(root, xml_declaration=True, encoding="UTF-8")


def render_foreign_source_definition(name, date_stamp=None):
    """Render a foreign-source definition with auto-detection disabled (AD-11).

    Empty ``<detectors/>`` (no service auto-detection — explicit services are
    authoritative) and ``<scan-interval>0s</scan-interval>`` (no periodic rescan).
    OpenNMS parses scan-interval as a duration, so the zero needs an explicit
    unit (a bare ``0`` can fail the duration parser). Returns bytes.
    """
    root = etree.Element(
        f"{{{FOREIGN_SOURCE_NS}}}foreign-source", nsmap={None: FOREIGN_SOURCE_NS}
    )
    root.set("name", name)
    if date_stamp is not None:
        root.set("date-stamp", date_stamp)

    scan_interval = etree.SubElement(root, f"{{{FOREIGN_SOURCE_NS}}}scan-interval")
    scan_interval.text = "0s"
    etree.SubElement(root, f"{{{FOREIGN_SOURCE_NS}}}detectors")
    etree.SubElement(root, f"{{{FOREIGN_SOURCE_NS}}}policies")

    return etree.tostring(root, xml_declaration=True, encoding="UTF-8")
