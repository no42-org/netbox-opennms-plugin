# Copyright 2026 Ronny Trommer <ronny@no42.org>
# SPDX-License-Identifier: MIT
"""Symmetric encryption for credentials stored in plugin models.

The Fernet key comes from ``PLUGINS_CONFIG["netbox_opennms"]["credentials_key"]``
when set. Otherwise it is derived from NetBox's ``SECRET_KEY`` with HKDF, so a
default install needs no extra configuration. Rotating ``SECRET_KEY`` without
setting ``credentials_key`` makes stored ciphertext undecryptable (``InvalidToken``).
"""

import base64
from functools import lru_cache

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from netbox.plugins import get_plugin_config

PLUGIN_NAME = "netbox_opennms"
_HKDF_INFO = b"netbox_opennms.credentials"


class Undecryptable(str):
    """Stand-in for a stored value that could not be decrypted.

    Holds the untouched ciphertext so saving a model never destroys it.
    """


@lru_cache(maxsize=1)
def fernet() -> Fernet:
    """Return the process-wide Fernet (cached; tests call ``cache_clear()``)."""
    configured = get_plugin_config(PLUGIN_NAME, "credentials_key")
    if configured:
        key = configured.encode() if isinstance(configured, str) else configured
    else:
        raw = HKDF(
            algorithm=hashes.SHA256(), length=32, salt=None, info=_HKDF_INFO
        ).derive(settings.SECRET_KEY.encode())
        key = base64.urlsafe_b64encode(raw)
    try:
        return Fernet(key)
    except ValueError as exc:
        raise ImproperlyConfigured(
            "PLUGINS_CONFIG['netbox_opennms']['credentials_key'] "
            "is not a valid Fernet key"
        ) from exc


def encrypt(text: str) -> str:
    return fernet().encrypt(text.encode()).decode()


def decrypt(token: str) -> str:
    """Decrypt ``token``; raises ``cryptography.fernet.InvalidToken``."""
    return fernet().decrypt(token.encode()).decode()
