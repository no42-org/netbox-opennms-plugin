# Copyright 2026 Ronny Trommer <ronny@no42.org>
# SPDX-License-Identifier: Apache-2.0
"""Typed errors for OpenNMS REST interactions (AD-12).

Callers map these to honest outcomes; the connection test surfaces the failure
type rather than a generic 500.
"""


class OpenNMSError(Exception):
    """Base class for all OpenNMS client failures."""


class OpenNMSTransportError(OpenNMSError):
    """OpenNMS could not be reached (connection refused, timeout, DNS, TLS)."""


class OpenNMSAuthError(OpenNMSError):
    """Authentication or authorization failed (HTTP 401/403)."""


class OpenNMSHTTPError(OpenNMSError):
    """OpenNMS returned an unexpected non-2xx HTTP status."""

    def __init__(self, message, status_code=None):
        super().__init__(message)
        self.status_code = status_code
