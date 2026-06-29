"""Reddit collector — free official API (OAuth2 client-credentials → hot posts)."""
from __future__ import annotations

import logging

import httpx

from ..config import get_settings

log = logging.getLogger(__name__)


def _token(settings) -> str | None:
    try:
        r = httpx.post(
            "https://www.reddit.com/api/v1/access_token",
            data={"grant_type": "client_credentials"},
            auth=(settings.reddit_client_id, settings.reddit_client_secret),
            headers={"User-Agent": settings.reddit_user_agent},
            timeout=30,
        )
        r.raise_for_status()
        return r.json().get("access_token")
    except Exception as e:  # noqa: BLE001
        log.warning("Reddit auth failed: %s", e)
        return None


def collect(client: dict, config: dict) -> list[dict]:
    settings = get_settings()
    if not (settings.reddit_client_id and settings.reddit_client_secret):
        return []
    if "reddit" not in (client.get("target_platforms") or []):
        return []

    subreddits = client.get("target_subreddits") or []
    if not subreddits:
        return []

    token = _token(settings)
    if not token:
        return []

    headers = {"Authorization": f"bearer {token}", "User-Agent": settings.reddit_user_agent}
    out: list[dict] = []
    with httpx.Client(timeout=30, headers=headers) as http:
        for sub in subreddits:
            try:
                r = http.get(f"https://oauth.reddit.com/r/{sub}/hot", params={"limit": 25})
                r.raise_for_status()
                # raw payload is the full listing; Normalize expands data.children
                out.append({"platform": "reddit", "source": "reddit_hot", "source_actor": "reddit_api", "raw": r.json()})
            except Exception as e:  # noqa: BLE001
                log.warning("Reddit r/%s failed: %s", sub, e)
    return out
