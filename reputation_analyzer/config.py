"""Standalone config for the reputation analyzer.

Reads the plain env names from the spec (NOT the ``TREND_``-prefixed settings the
trend analyzer uses). ``.env`` is already loaded by ``main.py`` / ``app`` startup.
"""
from __future__ import annotations

import os

# --- Credentials (plain env names, per spec) ---
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
APIFY_API_TOKEN = os.getenv("APIFY_API_TOKEN", "")

# --- Tuning knobs ---
OPENAI_MODEL = os.getenv("REPUTATION_OPENAI_MODEL", "gpt-4o")
POSTS_PER_PLATFORM = int(os.getenv("REPUTATION_POSTS_PER_PLATFORM", "20"))

# Apify run start -> poll -> fetch loop
APIFY_POLL_INTERVAL = int(os.getenv("REPUTATION_APIFY_POLL_INTERVAL", "10"))
APIFY_MAX_ATTEMPTS = int(os.getenv("REPUTATION_APIFY_MAX_ATTEMPTS", "30"))
HTTP_TIMEOUT = int(os.getenv("REPUTATION_HTTP_TIMEOUT", "60"))


def has_openai() -> bool:
    return bool(OPENAI_API_KEY)


def has_apify() -> bool:
    return bool(APIFY_API_TOKEN)
