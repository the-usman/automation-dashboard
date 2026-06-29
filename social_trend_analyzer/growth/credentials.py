"""Instagram credential loading from MongoDB.

Same cluster/pattern as the email-outreach workflow (a ``project_db`` collection
keyed by ``credential_id``). Guarded exactly like ``storage.py`` /
``rag.py``: with no Mongo URI / driver configured the loader returns ``None`` so
callers can degrade gracefully instead of crashing.

Password and totp_seed are decrypted with Fernet when ``TREND_FERNET_KEY`` is
set; if decryption fails the stored value is assumed to be plaintext (eases
migration). Prefer the ``session`` blob over the password to avoid repeat logins.
"""
from __future__ import annotations

import logging
from functools import lru_cache

from ..config import get_settings
from .schemas import InstagramCredential

log = logging.getLogger(__name__)

try:  # optional dependency (ships with motor, already in pyproject)
    from pymongo import MongoClient
except Exception:  # noqa: BLE001
    MongoClient = None  # type: ignore

try:  # optional dependency (cryptography already in pyproject)
    from cryptography.fernet import Fernet
except Exception:  # noqa: BLE001
    Fernet = None  # type: ignore


@lru_cache
def _collection():
    settings = get_settings()
    if MongoClient is None or not settings.mongo_uri:
        return None
    try:
        client = MongoClient(settings.mongo_uri, serverSelectionTimeoutMS=5000)
        return client[settings.mongo_db][settings.ig_credential_collection]
    except Exception as e:  # noqa: BLE001
        log.warning("MongoDB connect failed: %s", e)
        return None


def _decrypt(value: str | None) -> str:
    if not value:
        return ""
    settings = get_settings()
    if Fernet is not None and settings.fernet_key:
        try:
            return Fernet(settings.fernet_key.encode()).decrypt(value.encode()).decode()
        except Exception:  # noqa: BLE001 — assume already plaintext
            return value
    return value


def load_instagram_credential(credential_id: str) -> InstagramCredential | None:
    """Fetch one credential document by ``credential_id``. ``None`` if Mongo is
    unconfigured or the document is missing."""
    col = _collection()
    if col is None or not credential_id:
        log.info("Credentials: Mongo disabled or empty credential_id — returning None")
        return None
    try:
        doc = col.find_one({"credential_id": credential_id})
    except Exception as e:  # noqa: BLE001
        log.warning("Credential lookup failed for %s: %s", credential_id, e)
        return None
    if not doc:
        log.warning("No credential document for credential_id=%s", credential_id)
        return None

    return InstagramCredential(
        credential_id=credential_id,
        username=doc.get("username", ""),
        password=_decrypt(doc.get("password") or doc.get("enc_password")),
        totp_seed=_decrypt(doc.get("totp_seed")) or None,
        session=doc.get("session") or None,
    )
