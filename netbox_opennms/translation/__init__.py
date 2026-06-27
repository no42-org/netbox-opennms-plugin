# Copyright 2026 Ronny Trommer <ronny@no42.org>
# SPDX-License-Identifier: Apache-2.0
"""Pure translation layer — NetBox objects → OpenNMS requisition XML (AD-3)."""

from .requisition import (
    RenderError,
    render_foreign_source_definition,
    render_requisition,
)

__all__ = ["RenderError", "render_requisition", "render_foreign_source_definition"]
