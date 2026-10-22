# Copyright 2026 Ronny Trommer <ronny@no42.org>
# SPDX-License-Identifier: MIT
"""Model fields that store their value Fernet-encrypted in a text column."""

import copy
import json

from cryptography.fernet import InvalidToken
from django import forms
from django.db import models

from .crypto import Undecryptable, decrypt, encrypt


class _EncryptedField(models.TextField):
    """Base: encrypt on write, decrypt on read, keep undecryptable ciphertext."""

    empty_value = ""

    def _load(self, plaintext):
        return plaintext

    def _dump(self, value):
        return value

    def from_db_value(self, value, expression, connection):
        if value is None or value == "":
            return copy.copy(self.empty_value)
        try:
            return self._load(decrypt(value))
        except (InvalidToken, ValueError):
            # ValueError covers a token that decrypts to invalid JSON
            # (json.JSONDecodeError) or to invalid UTF-8.
            return Undecryptable(value)

    def to_python(self, value):
        if value is None or value == "":
            return copy.copy(self.empty_value)
        return value

    def get_prep_value(self, value):
        if isinstance(value, Undecryptable):
            return str(value)
        if value is None or value == "" or value == self.empty_value:
            return ""
        return encrypt(self._dump(value))


class EncryptedCharField(_EncryptedField):
    """A string stored encrypted."""


class EncryptedJSONField(_EncryptedField):
    """A JSON-serialisable value (default ``{}``) stored encrypted."""

    empty_value = {}

    def _load(self, plaintext):
        return json.loads(plaintext)

    def _dump(self, value):
        return json.dumps(value)

    def formfield(self, **kwargs):
        return super().formfield(**{"form_class": forms.JSONField, **kwargs})
