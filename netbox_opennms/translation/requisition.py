# Copyright 2026 Ronny Trommer <ronny@no42.org>
# SPDX-License-Identifier: MIT
"""Render OpenNMS requisition + foreign-source-definition XML (pure, AD-3/AD-5).

The render half of render-and-replace. Deterministic and side-effect-free (no
network, no DB writes): the requisition reads pre-resolved ``NodeSpec`` objects
(the ``membership`` layer owns the ORM lookups), and the foreign-source
definition reads a ``Requisition``'s detectors/policies. ``date_stamp`` is a
parameter so output is reproducible.

The definition emits the Requisition's detectors so OpenNMS auto-discovers
services (reverses v1's AD-11 empty ``<detectors/>``), alongside the declared
services the membership layer places on each interface.
"""

from lxml import etree

from ..choices import InterfaceRoleChoices

MODEL_IMPORT_NS = "http://xmlns.opennms.org/xsd/config/model-import"
FOREIGN_SOURCE_NS = "http://xmlns.opennms.org/xsd/config/foreign-source"


class RenderError(Exception):
    """A Foreign Source can't be rendered into valid XML (a required value is None).

    The renderer assumes resolved nodes (the ``membership`` layer skips members
    without a name or management IP), but a ``NodeSpec`` can still arrive
    malformed via a direct call. Rather than emit malformed XML (or raise an
    opaque ``AttributeError``), the renderer raises this so the sync job can fail
    that sync cleanly.
    """


def _add_parameters(parent, parameters):
    """Append ``<parameter key="…" value="…"/>`` children, sorted by key (AD-3)."""
    for key in sorted(parameters or {}):
        param = etree.SubElement(parent, f"{{{FOREIGN_SOURCE_NS}}}parameter")
        param.set("key", key)
        param.set("value", str(parameters[key]))


def _add_metadata(parent, entries):
    """Append ``<meta-data context=… key=… value=…/>`` children (RD-3)."""
    for context, key, value in entries or ():
        meta = etree.SubElement(parent, f"{{{MODEL_IMPORT_NS}}}meta-data")
        meta.set("context", context)
        meta.set("key", key)
        meta.set("value", str(value))


def render_requisition(foreign_source, nodes, date_stamp=None, default_location=""):
    """Render the complete ``model-import`` requisition for one Foreign Source.

    ``foreign_source`` is the already-derived name (AD-14); ``nodes`` are the
    resolved ``NodeSpec`` objects (``membership.resolve``). Each yields one node
    with its management IP as the primary (``P``) interface and any extra IPs as
    non-primary (``N``). A node's location falls back to ``default_location``
    (passed in for purity). Returns bytes.
    """
    root = etree.Element(
        f"{{{MODEL_IMPORT_NS}}}model-import", nsmap={None: MODEL_IMPORT_NS}
    )
    root.set("foreign-source", foreign_source)
    if date_stamp is not None:
        root.set("date-stamp", date_stamp)

    for node in nodes:
        if not node.node_label:
            raise RenderError(f"node {node.foreign_id!r} has no node-label.")
        # A node with no interface is a valid inventory-only import (RD-6/h) — the
        # membership layer marks it with a Warning; it is not a render error.
        el = etree.SubElement(root, f"{{{MODEL_IMPORT_NS}}}node")
        el.set("node-label", node.node_label)
        el.set("foreign-id", node.foreign_id)

        location = node.location or default_location
        if location:
            el.set("location", location)

        # Primary interface first, then the rest by bare IP for determinism.
        ordered = sorted(
            node.interfaces,
            key=lambda i: (i.role != InterfaceRoleChoices.PRIMARY, i.ip),
        )
        for interface in ordered:
            iface_el = etree.SubElement(el, f"{{{MODEL_IMPORT_NS}}}interface")
            iface_el.set("ip-addr", interface.ip)
            iface_el.set("snmp-primary", interface.role)
            for name in sorted(interface.services):
                service = etree.SubElement(
                    iface_el, f"{{{MODEL_IMPORT_NS}}}monitored-service"
                )
                service.set("service-name", name)
                # Service-scope metadata applies to every monitored-service (RD-3).
                _add_metadata(service, node.service_metadata)
            # Interface-scope metadata applies to every interface (RD-3).
            _add_metadata(iface_el, node.interface_metadata)

        # Node-scope enrichment, after interfaces per the requisition XSD order:
        # <interface>* then <asset>* then <meta-data>* (RD-2/RD-3).
        for name, value in node.assets:
            asset_el = etree.SubElement(el, f"{{{MODEL_IMPORT_NS}}}asset")
            asset_el.set("name", name)
            asset_el.set("value", str(value))
        _add_metadata(el, node.node_metadata)

    return etree.tostring(root, xml_declaration=True, encoding="UTF-8")


def render_foreign_source_definition(foreign_source, requisition, date_stamp=None):
    """Render a foreign-source definition named ``foreign_source`` from a Requisition.

    Emits ``<scan-interval>`` plus the Requisition's detectors (OpenNMS
    auto-discovers the matching services) and policies (categories, interface
    management). This reverses v1's AD-11 empty ``<detectors/>``: detection is a
    service source alongside the Requisition's declared services.

    The definition's ``name`` is the Foreign Source (the Requisition's name) —
    OpenNMS links a definition to a requisition by name, and a mismatch silently
    falls back to OpenNMS's built-in default detectors. Returns bytes.
    """
    root = etree.Element(
        f"{{{FOREIGN_SOURCE_NS}}}foreign-source", nsmap={None: FOREIGN_SOURCE_NS}
    )
    root.set("name", foreign_source)
    if date_stamp is not None:
        root.set("date-stamp", date_stamp)

    scan_interval = etree.SubElement(root, f"{{{FOREIGN_SOURCE_NS}}}scan-interval")
    scan_interval.text = requisition.scan_interval or "1d"

    detectors = etree.SubElement(root, f"{{{FOREIGN_SOURCE_NS}}}detectors")
    for detector in requisition.detectors.all():
        if not detector.rule_class:
            raise RenderError(
                f"detector {detector.name!r} on requisition "
                f"{requisition.name!r} has no class."
            )
        el = etree.SubElement(detectors, f"{{{FOREIGN_SOURCE_NS}}}detector")
        el.set("name", detector.name)
        el.set("class", detector.rule_class)
        _add_parameters(el, detector.parameters)

    policies = etree.SubElement(root, f"{{{FOREIGN_SOURCE_NS}}}policies")
    for policy in requisition.policies.all():
        if not policy.rule_class:
            raise RenderError(
                f"policy {policy.name!r} on requisition {requisition.name!r} has "
                "no class."
            )
        el = etree.SubElement(policies, f"{{{FOREIGN_SOURCE_NS}}}policy")
        el.set("name", policy.name)
        el.set("class", policy.rule_class)
        _add_parameters(el, policy.parameters)

    return etree.tostring(root, xml_declaration=True, encoding="UTF-8")
