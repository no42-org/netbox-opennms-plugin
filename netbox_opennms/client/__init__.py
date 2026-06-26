# Copyright 2026 Ronny Trommer <ronny@no42.org>
# SPDX-License-Identifier: Apache-2.0
"""OpenNMS REST client package (the adapter behind the port, AD-2)."""

from .client import OpenNMSClient
from .errors import (
    OpenNMSAuthError,
    OpenNMSError,
    OpenNMSHTTPError,
    OpenNMSTransportError,
)

__all__ = [
    "OpenNMSClient",
    "OpenNMSError",
    "OpenNMSTransportError",
    "OpenNMSAuthError",
    "OpenNMSHTTPError",
]
