# Copyright 2026 Ronny Trommer <ronny@no42.org>
# SPDX-License-Identifier: Apache-2.0
"""Packaging guards (Story 4.3) — keep the release honest."""

from pathlib import Path

from django.test import SimpleTestCase

import netbox_opennms

PACKAGE_ROOT = Path(netbox_opennms.__file__).parent
SPDX = "SPDX-License-Identifier: Apache-2.0"


class LicenseHeaderTest(SimpleTestCase):
    def test_every_source_file_has_spdx_header(self):
        """Every .py/.html under the plugin package carries the SPDX header (AC3).

        Scope is the package source (not build/config files, not the built wheel);
        it's the CI backstop so a future header-less package file fails here, not
        just at review. (Template-shipping is verified at build time, Story 4.4.)
        """
        missing = []
        for path in PACKAGE_ROOT.rglob("*"):
            if path.suffix not in {".py", ".html"}:
                continue
            if "__pycache__" in path.parts:
                continue
            if SPDX not in path.read_text(encoding="utf-8"):
                missing.append(str(path.relative_to(PACKAGE_ROOT)))
        self.assertEqual(missing, [], f"Files missing SPDX header: {missing}")
