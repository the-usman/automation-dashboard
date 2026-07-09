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
import os
import random
import re
import time
from pathlib import Path

from .schemas import ActionResult, InstagramCredential

log = logging.getLogger(__name__)

# Allowed characters in a session filename — everything else is replaced so a
# malformed username from the credential store cannot escape the session dir.
_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]")

# Everything Instagram does NOT allow inside a hashtag (only letters, digits and
# underscore are valid; spaces/punctuation make the tag 404).
_NON_HASHTAG = re.compile(r"[^0-9A-Za-z_]")


def _hashtag_tokens(keyword: str) -> list[str]:
    """Turn a raw keyword into valid Instagram hashtag tokens (no spaces/punct).

    A phrase like ``"sensitive skin solutions"`` yields the individual words
    (``sensitive``, ``skin`` — ``solutions`` too) plus the concatenated form
    (``sensitiveskinsolutions``); a single word is just cleaned. Words shorter
    than 3 chars are dropped as too noisy to search.
    """
    raw = str(keyword or "").strip().lstrip("#").strip()
    if not raw:
        return []
    words = raw.split()
    tokens: list[str] = []
    if len(words) > 1:
        for word in words:
            cleaned = _NON_HASHTAG.sub("", word).lower()
            if len(cleaned) >= 3 and cleaned not in tokens:
                tokens.append(cleaned)
    joined = _NON_HASHTAG.sub("", raw).lower()
    if joined and joined not in tokens:
        tokens.append(joined)
    return tokens

try:  # optional dependency
    from instagrapi import Client
    from instagrapi.exceptions import ChallengeRequired
except Exception:  # noqa: BLE001
    Client = None  # type: ignore
    ChallengeRequired = Exception  # type: ignore


class InstagramClient:
    def __init__(self, credential: InstagramCredential, config: dict, session_dir: str):
        self.cred = credential
        self.config = config
        self.session_dir = session_dir
        self._cl = None
        self._actions_done = 0
        self._cap = int(config.get("ig_daily_action_cap", 150))
        self._delay = float(config.get("ig_action_delay_seconds", 4))
        # Reads (hashtag/comments/likers) trip Instagram's anti-bot faster than
        # writes, so they get a longer pause; jitter keeps timing human-like.
        self._read_delay = float(config.get("ig_read_delay_seconds", 8))
        self._jitter = float(config.get("ig_delay_jitter_seconds", 4))

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
        has_session = False
        if self.cred.session:
            try:
                cl.set_settings(self.cred.session)
                has_session = True
            except Exception as e:  # noqa: BLE001
                log.warning("Could not load stored session blob: %s", e)
        elif self._session_path().exists():
            try:
                cl.load_settings(str(self._session_path()))
                has_session = True
            except Exception as e:  # noqa: BLE001
                log.warning("Could not load session file: %s", e)

        # If Instagram issues a checkpoint, feed it a code from the environment
        # (TREND_IG_CHALLENGE_CODE) when present. This lets a human paste the
        # emailed/SMS code for one login without editing code.
        cl.challenge_code_handler = self._challenge_code_handler

        self._cl = cl

        # Raw `sessionid` cookie: use instagrapi's login_by_sessionid, which runs
        # the full login flow so private WRITE actions (follow) are authorized.
        # Plain set_settings only restores read state — follow would 403 with
        # `login_required`. This is the canonical, safe path (one device, one login).
        if self.cred.sessionid:
            try:
                cl.login_by_sessionid(self.cred.sessionid.strip().strip('"').strip("'"))
            except ChallengeRequired as e:
                raise RuntimeError(
                    f"Instagram issued a checkpoint for this session. The `sessionid` "
                    f"cookie may be expired or the account flagged — grab a fresh "
                    f"sessionid from a logged-in browser. (original: {e})"
                ) from e
            if not self.cred.username:
                try:
                    self.cred.username = cl.account_info().username
                except Exception as e:  # noqa: BLE001
                    log.warning("Could not resolve username after sessionid login: %s", e)
            log.info("Logged in via sessionid for @%s", self.cred.username or "?")
            self._persist_session()
            return self

        # Session-only auth restored from a stored blob/file with no password:
        # trust it (it was validated when created) and skip the password login.
        if has_session and not self.cred.password:
            log.info("Logged in via stored session for @%s (no password login)", self.cred.username)
            return self

        try:
            cl.login(self.cred.username, self.cred.password, verification_code=self._verification_code())
        except ChallengeRequired as e:
            raise RuntimeError(
                f"Instagram issued a security checkpoint for @{self.cred.username}. "
                "Password login from this server is not trusted yet. Solve it ONCE and "
                "reuse the session: run `python scripts/ig_login.py`, enter the code "
                "Instagram emails/SMSes you, and it will save a session the server reuses. "
                f"(original: {e})"
            ) from e
        self._persist_session()
        return self

    def _challenge_code_handler(self, username: str, choice) -> str:
        code = os.getenv("TREND_IG_CHALLENGE_CODE", "").strip()
        if code:
            log.info("Using TREND_IG_CHALLENGE_CODE for %s challenge (%s)", username, choice)
        else:
            log.warning("Challenge required for %s (%s) but TREND_IG_CHALLENGE_CODE is unset", username, choice)
        return code

    # --- actions ------------------------------------------------------------ #
    def _rate_limit(self) -> None:
        """Human-like pause after a WRITE action (follow/unfollow)."""
        time.sleep(self._delay + random.uniform(0, self._jitter))

    def _read_pause(self) -> None:
        """Longer, jittered pause between READ calls — reads flag accounts fast."""
        time.sleep(self._read_delay + random.uniform(0, self._jitter))

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

    # --- discovery / harvesting (reads via the logged-in client, no Apify) --- #
    def hashtag_author_counts(
        self, keywords: list[str], posts_per_tag: int
    ) -> tuple[dict[str, int], dict[str, str]]:
        """Rank competitor accounts by how often they author top posts under the
        given hashtags. Returns (username->count, username->full_name)."""
        counts: dict[str, int] = {}
        names: dict[str, str] = {}
        if self._cl is None:
            return counts, names
        # Normalize keywords → valid hashtags (drop spaces/punctuation) and dedupe
        # so a phrase can't 404 and the same tag isn't scanned twice.
        tags: list[str] = []
        for kw in keywords:
            for tag in _hashtag_tokens(kw):
                if tag not in tags:
                    tags.append(tag)
        # Cap how many tags we scan — every extra tag is another private call that
        # brings the account closer to a logout.
        max_tags = int(self.config.get("growth_hashtags_max", 6))
        tags = tags[:max_tags]
        for tag in tags:
            try:
                medias = self._cl.hashtag_medias_top(tag, amount=posts_per_tag)
            except Exception as e:  # noqa: BLE001
                log.warning("hashtag scan failed for #%s: %s", tag, e)
                continue
            for m in medias:
                user = getattr(m, "user", None)
                handle = getattr(user, "username", "") or ""
                if not handle:
                    continue
                counts[handle] = counts.get(handle, 0) + 1
                names.setdefault(handle, getattr(user, "full_name", "") or "")
            self._read_pause()
        return counts, names

    def post_engagers(
        self, username: str, n_posts: int,
        include_commenters: bool = True, include_likers: bool = True,
    ) -> list[str]:
        """Usernames who commented on / liked the account's recent ``n_posts`` posts.

        Comments and likers are **capped** per post (``growth_comments_per_post`` /
        ``growth_likers_per_post``). Fetching *all* of them (``amount=0``) paginates
        the private API hard and is the top reason Instagram logs the session out.
        """
        out: set[str] = set()
        if self._cl is None:
            return []
        comments_cap = int(self.config.get("growth_comments_per_post", 25))
        likers_cap = int(self.config.get("growth_likers_per_post", 25))
        try:
            uid = self._cl.user_id_from_username(username)
            medias = self._cl.user_medias(uid, amount=n_posts)
        except Exception as e:  # noqa: BLE001
            log.warning("could not load media for @%s: %s", username, e)
            return []
        for m in medias:
            mid = getattr(m, "id", None) or getattr(m, "pk", None)
            if not mid:
                continue
            if include_commenters:
                try:
                    # amount>0 fetches just the first page — never "all".
                    for c in self._cl.media_comments(mid, amount=comments_cap):
                        handle = getattr(getattr(c, "user", None), "username", "") or ""
                        if handle:
                            out.add(handle)
                except Exception as e:  # noqa: BLE001
                    log.warning("comments failed for media %s: %s", mid, e)
                self._read_pause()
            if include_likers:
                try:
                    for u in list(self._cl.media_likers(mid))[:likers_cap]:
                        handle = getattr(u, "username", "") or ""
                        if handle:
                            out.add(handle)
                except Exception as e:  # noqa: BLE001
                    log.warning("likers failed for media %s: %s", mid, e)
                self._read_pause()
        return list(out)

    def suggested_users(self) -> list[str]:
        """The logged-in account's own Instagram "Suggested for you" usernames.

        One private call to ``fbsearch/accounts_recs/`` per invocation; call again
        (a "reload") for a fresh/rotated batch. Guarded — returns ``[]`` on error.
        """
        if self._cl is None:
            return []
        try:
            uid = getattr(self._cl, "user_id", None) or self._cl.account_info().pk
            users = self._cl.fbsearch_suggested_profiles(str(uid))
        except Exception as e:  # noqa: BLE001
            log.warning("suggested users fetch failed: %s", e)
            return []
        self._read_pause()
        return [u.username for u in users if getattr(u, "username", "")]

    def following_usernames(self) -> list[str]:
        """Usernames the logged-in account currently follows.

        One paginated private read, capped by ``growth_following_fetch_max``
        (0 = fetch all) — very large following lists paginate the private API
        hard. Raises on failure instead of returning ``[]`` so a bulk-unfollow
        caller fails loudly rather than silently doing nothing.
        """
        if self._cl is None:
            raise RuntimeError("not logged in")
        cap = int(self.config.get("growth_following_fetch_max", 1000))
        uid = getattr(self._cl, "user_id", None) or self._cl.account_info().pk
        users = self._cl.user_following(str(uid), amount=cap)
        self._read_pause()
        return [u.username for u in users.values() if getattr(u, "username", "")]

    def is_business_account(self, username: str) -> bool | None:
        """True if the account is a business/brand **page**, False if a person,
        None when the profile could not be read (caller decides the default).

        Used to follow people only, never brands. One private ``user_info`` read;
        instagrapi caches the resolved user id, so the follow that usually comes
        right after this check does not pay a second lookup.
        """
        if self._cl is None:
            return None
        try:
            uid = self._cl.user_id_from_username(username)
            info = self._cl.user_info(uid)
        except Exception as e:  # noqa: BLE001
            log.warning("could not read profile of @%s for brand check: %s", username, e)
            return None
        self._read_pause()
        return bool(getattr(info, "is_business", False))

    def follow(self, username: str) -> ActionResult:
        return self._act("follow", username)

    def unfollow(self, username: str) -> ActionResult:
        return self._act("unfollow", username)
