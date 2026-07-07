"""Build an instagrapi session from a browser `sessionid` cookie.

Reusing a logged-in browser's trusted cookie sidesteps the password-login
checkpoint (``ChallengeRequired``). The returned settings blob is stored on the
credential document (``instagram_accounts.session``) so the campaign/task runners
reuse it instead of logging in with a password.

Blocking (network) — call via ``asyncio.to_thread`` from async routes.
"""
from __future__ import annotations

try:  # optional dependency
    from instagrapi import Client
except Exception:  # noqa: BLE001
    Client = None  # type: ignore


def build_session_from_sessionid(sessionid: str) -> tuple[str, dict]:
    """Validate a browser ``sessionid`` and return ``(username, settings_blob)``.

    Raises RuntimeError if instagrapi is missing or the cookie is invalid.
    """
    if Client is None:
        raise RuntimeError("instagrapi is not installed — run: pip install instagrapi")

    cl = Client()
    cl.login_by_sessionid(sessionid.strip().strip('"').strip("'"))
    username = cl.account_info().username
    return username, cl.get_settings()
