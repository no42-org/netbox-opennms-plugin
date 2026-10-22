# Copyright 2026 Ronny Trommer <ronny@no42.org>
# SPDX-License-Identifier: MIT
"""Tests for the Fernet key derivation and the encrypted model fields."""

from unittest import mock

from cryptography.fernet import Fernet, InvalidToken
from django import forms
from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase, override_settings

from netbox_opennms.crypto import Undecryptable, decrypt, encrypt, fernet
from netbox_opennms.fields import EncryptedCharField, EncryptedJSONField


class CryptoTest(SimpleTestCase):
    def tearDown(self):
        fernet.cache_clear()

    def test_round_trip_unicode(self):
        self.assertEqual(decrypt(encrypt("pässwörd ✓")), "pässwörd ✓")

    def test_ciphertext_hides_plaintext(self):
        self.assertNotIn("secret", encrypt("secret"))

    def test_derived_key_is_stable_for_secret_key(self):
        with override_settings(SECRET_KEY="a" * 50):
            first = encrypt("x")
            fernet.cache_clear()
            self.assertEqual(decrypt(first), "x")

    def test_rotated_secret_key_raises_invalid_token(self):
        with override_settings(SECRET_KEY="a" * 50):
            token = encrypt("x")
        fernet.cache_clear()
        with override_settings(SECRET_KEY="b" * 50):
            with self.assertRaises(InvalidToken):
                decrypt(token)

    def test_explicit_credentials_key_wins(self):
        key = Fernet.generate_key().decode()
        with mock.patch("netbox_opennms.crypto.get_plugin_config", return_value=key):
            token = encrypt("x")
        self.assertEqual(Fernet(key.encode()).decrypt(token.encode()).decode(), "x")

    def test_invalid_credentials_key_is_improperly_configured(self):
        with mock.patch("netbox_opennms.crypto.get_plugin_config", return_value="nope"):
            with self.assertRaisesRegex(ImproperlyConfigured, "credentials_key"):
                fernet()


class EncryptedFieldTest(SimpleTestCase):
    def tearDown(self):
        fernet.cache_clear()

    def test_char_field_round_trip(self):
        f = EncryptedCharField()
        stored = f.get_prep_value("hunter2")
        self.assertNotIn("hunter2", stored)
        self.assertEqual(f.from_db_value(stored, None, None), "hunter2")

    def test_json_field_round_trip(self):
        f = EncryptedJSONField()
        stored = f.get_prep_value({"CF-Access-Client-Id": "abc"})
        self.assertEqual(
            f.from_db_value(stored, None, None), {"CF-Access-Client-Id": "abc"}
        )

    def test_empty_values(self):
        self.assertEqual(EncryptedCharField().from_db_value("", None, None), "")
        self.assertEqual(EncryptedJSONField().from_db_value("", None, None), {})
        self.assertEqual(EncryptedCharField().get_prep_value(""), "")
        self.assertEqual(EncryptedJSONField().get_prep_value({}), "")

    def test_undecryptable_sentinel_preserves_ciphertext(self):
        f = EncryptedCharField()
        value = f.from_db_value("gAAAAAnot-a-real-token", None, None)
        self.assertIsInstance(value, Undecryptable)
        self.assertEqual(f.get_prep_value(value), "gAAAAAnot-a-real-token")

    def test_json_undecryptable_preserves_ciphertext(self):
        f = EncryptedJSONField()
        value = f.from_db_value("gAAAAAnot-a-real-token", None, None)
        self.assertIsInstance(value, Undecryptable)
        self.assertEqual(f.get_prep_value(value), "gAAAAAnot-a-real-token")
        self.assertIs(f.to_python(value), value)

    def test_json_formfield_is_json_field(self):
        self.assertIsInstance(EncryptedJSONField().formfield(), forms.JSONField)
