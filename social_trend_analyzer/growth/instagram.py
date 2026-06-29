"""instagrapi wrapper — the only account-bound surface (follow/unfollow).

Guarded like the rest of the package: importing works without instagrapi
installed; calling :meth:`InstagramClient.login` without it raises a clear error.

Safety built in:
* session load/save (DB blob → file → password) to avoid repeat logins
* 2FA via TOTP seed
* a delay between every action (``ig_action_delay_seconds``)
* a hard per-run cap (``ig_daily_action_cap``)
"""
from __future__ import annotations

import logging
import re
import time
from pathlib import Path

from .schemas import ActionResult, InstagramCredential

log = logging.getLogger(__name__)

# Allowed characters in a session filename — everything else is replaced so a
# malformed username from the credential store cannot escape the session dir.
_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]")

try:  # optional dependency
    from instagrapi import Client
except Exception:  # noqa: BLE001
    Client = None  # type: ignore


class InstagramClient:
    def __init__(self, credential: InstagramCredential, config: dict, session_dir: str):
        self.cred = credential
        self.config = config
        self.session_dir = session_dir
        self._cl = None
        self._actions_done = 0
        self._cap = int(config.get("ig_daily_action_cap", 150))
        self._delay = float(config.get("ig_action_delay_seconds", 4))

    # --- session helpers ---------------------------------------------------- #
    def _session_path(self) -> Path:
        safe = _SAFE_NAME.sub("_", self.cred.username or "session")
        return Path(self.session_dir) / f"{safe}.json"

    def _persist_session(self) -> None:
        try:
            path = self._session_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            self._cl.dump_settings(str(path))
        except Exception as e:  # noqa: BLE001
            log.warning("Failed to persist IG session for %s: %s", self.cred.username, e)

    def _verification_code(self) -> str:
        if not self.cred.totp_seed:
            return ""
        try:
            return self._cl.totp_generate_code(self.cred.totp_seed)
        except Exception as e:  # noqa: BLE001
            log.warning("TOTP code generation failed: %s", e)
            return ""

    # --- login -------------------------------------------------------------- #
    def login(self) -> "InstagramClient":
        if Client is None:
            raise RuntimeError("instagrapi is not installed — run: pip install instagrapi")
        cl = Client()

        # Prefer a stored session blob, then an on-disk session file.
        if self.cred.session:
            try:
                cl.set_settings(self.cred.session)
            except Exception as e:  # noqa: BLE001
                log.warning("Could not load stored session blob: %s", e)
        elif self._session_path().exists():
            try:
                cl.load_settings(str(self._session_path()))
            except Exception as e:  # noqa: BLE001
                log.warning("Could not load session file: %s", e)

        self._cl = cl
        cl.login(self.cred.username, self.cred.password, verification_code=self._verification_code())
        self._persist_session()
        return self

    # --- actions ------------------------------------------------------------ #
    def _rate_limit(self) -> None:
        time.sleep(self._delay)

    def _act(self, action: str, username: str) -> ActionResult:
        if self._cl is None:
            return ActionResult(username=username, action=action, status="error", message="not logged in")
        if self._actions_done >= self._cap:
            return ActionResult(username=username, action=action, status="skipped",
                                message=f"daily cap ({self._cap}) reached")
        try:
            uid = self._cl.user_id_from_username(username)
            if action == "follow":
                self._cl.user_follow(uid)
            else:
                self._cl.user_unfollow(uid)
            self._actions_done += 1
            self._rate_limit()
            return ActionResult(username=username, action=action, status="success", user_id=str(uid))
        except Exception as e:  # noqa: BLE001
            log.warning("IG %s failed for %s: %s", action, username, e)
            return ActionResult(username=username, action=action, status="error", message=str(e))

    def follow(self, username: str) -> ActionResult:
        return self._act("follow", username)

    def unfollow(self, username: str) -> ActionResult:
        return self._act("unfollow", username)
