"""Credential loading: Mongo lookup, guarded fallbacks, Fernet decryption."""
from __future__ import annotations

import pytest

from social_trend_analyzer.growth import credentials as cred_mod


class FakeCol:
    def __init__(self, doc):
        self.doc = doc

    def find_one(self, query):
        if self.doc and self.doc.get("credential_id") == query.get("credential_id"):
            return self.doc
        return None


def _settings(fernet_key=""):
    return type("S", (), {"fernet_key": fernet_key})()


def test_load_credential_maps_fields(monkeypatch):
    # Arrange
    doc = {"credential_id": "c1", "username": "demo", "password": "secret", "session": {"a": 1}}
    monkeypatch.setattr(cred_mod, "_collection", lambda: FakeCol(doc))
    monkeypatch.setattr(cred_mod, "get_settings", lambda: _settings())

    # Act
    cred = cred_mod.load_instagram_credential("c1")

    # Assert
    assert cred is not None
    assert cred.username == "demo"
    assert cred.password == "secret"
    assert cred.session == {"a": 1}


def test_load_credential_missing_document(monkeypatch):
    monkeypatch.setattr(cred_mod, "_collection", lambda: FakeCol(None))
    assert cred_mod.load_instagram_credential("nope") is None


def test_load_credential_no_mongo(monkeypatch):
    monkeypatch.setattr(cred_mod, "_collection", lambda: None)
    assert cred_mod.load_instagram_credential("c1") is None


def test_load_credential_empty_id(monkeypatch):
    monkeypatch.setattr(cred_mod, "_collection", lambda: FakeCol({"credential_id": "x"}))
    assert cred_mod.load_instagram_credential("") is None


def test_decrypt_plaintext_when_no_key(monkeypatch):
    monkeypatch.setattr(cred_mod, "get_settings", lambda: _settings(""))
    assert cred_mod._decrypt("plain") == "plain"
    assert cred_mod._decrypt("") == ""


def test_decrypt_fernet_roundtrip(monkeypatch):
    fernet = pytest.importorskip("cryptography.fernet")
    key = fernet.Fernet.generate_key().decode()
    token = fernet.Fernet(key.encode()).encrypt(b"hunter2").decode()
    monkeypatch.setattr(cred_mod, "get_settings", lambda: _settings(key))
    assert cred_mod._decrypt(token) == "hunter2"


def test_decrypt_assumes_plaintext_on_bad_token(monkeypatch):
    fernet = pytest.importorskip("cryptography.fernet")
    key = fernet.Fernet.generate_key().decode()
    monkeypatch.setattr(cred_mod, "get_settings", lambda: _settings(key))
    # not a valid Fernet token -> returned as-is
    assert cred_mod._decrypt("not-a-token") == "not-a-token"
