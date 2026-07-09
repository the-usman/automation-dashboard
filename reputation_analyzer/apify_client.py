"""Apify profile scraper — one profile actor per platform.

Self-contained: its own httpx start -> poll -> fetch loop. Guarded — returns an
empty list when no Apify token is configured or a run fails, so the caller can
isolate a single platform's failure without crashing the whole request.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from . import config

log = logging.getLogger(__name__)

API = "https://api.apify.com/v2"
TERMINAL = {"SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT"}

# Profile-scraper actors per platform (Apify uses '~' between org and actor).
PROFILE_ACTORS = {
    "instagram": "apify~instagram-scraper",
    "x": "apidojo~tweet-scraper",
    "tiktok": "clockworks~tiktok-scraper",
}


def _window_start() -> str:
    """First day (UTC, YYYY-MM-DD) of the reputation lookback window."""
    start = datetime.now(timezone.utc) - timedelta(days=config.LOOKBACK_DAYS)
    return start.strftime("%Y-%m-%d")


def _actor_input(platform: str, username: str, limit: int) -> dict[str, Any]:
    """Build the profile-scrape input for one platform's actor.

    Each actor gets the lookback window in its own dialect; ``normalize`` also
    re-enforces the window client-side in case an actor ignores the param.
    """
    since = _window_start()
    if platform == "instagram":
        return {
            "directUrls": [f"https://www.instagram.com/{username}/"],
            "resultsType": "posts",
            "resultsLimit": limit,
            "onlyPostsNewerThan": since,
        }
    if platform == "x":
        return {
            "twitterHandles": [username],
            "sort": "Latest",
            "maxItems": limit,
            "start": since,
        }
    if platform == "tiktok":
        return {
            "profiles": [username],
            "resultsPerPage": limit,
            "shouldDownloadVideos": False,
            "shouldDownloadCovers": False,
            "oldestPostDateUnified": since,
        }
    raise ValueError(f"unsupported platform: {platform!r}")


def _items(payload: Any) -> list[dict]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("items", "data"):
            if isinstance(payload.get(key), list):
                return payload[key]
        return [payload] if payload else []
    return []


def scrape_profile(platform: str, username: str) -> list[dict]:
    """Scrape one profile's recent posts. Returns raw actor items (capped).

    Raises nothing for expected failures — logs a warning and returns ``[]`` so
    a single bad username/platform doesn't fail the whole reputation request.
    """
    actor_id = PROFILE_ACTORS.get(platform)
    if not config.has_apify() or not actor_id:
        log.info("APIFY: no token/actor — skipping %s/%s", platform, username)
        return []

    limit = config.POSTS_PER_PLATFORM
    actor_input = _actor_input(platform, username, limit)
    params = {"token": config.APIFY_API_TOKEN}
    log.info(
        "APIFY: starting %s for %s/@%s (limit=%d)", actor_id, platform, username, limit
    )
    log.debug("APIFY: %s input: %s", actor_id, actor_input)

    with httpx.Client(timeout=config.HTTP_TIMEOUT) as http:
        try:
            r = http.post(f"{API}/acts/{actor_id}/runs", params=params, json=actor_input)
            r.raise_for_status()
            d = r.json().get("data", {})
            run_id = d.get("id")
            dataset_id = d.get("defaultDatasetId")
            status = d.get("status", "READY")
            log.info("APIFY: run %s started for %s/@%s", run_id, platform, username)
        except Exception as e:  # noqa: BLE001
            log.warning("APIFY start failed for %s/%s: %s", platform, username, e)
            return []

        for attempt in range(config.APIFY_MAX_ATTEMPTS):
            if status in TERMINAL:
                break
            time.sleep(config.APIFY_POLL_INTERVAL)
            try:
                s = http.get(f"{API}/actor-runs/{run_id}", params=params)
                s.raise_for_status()
                d = s.json().get("data", {})
                status = d.get("status", status)
                dataset_id = d.get("defaultDatasetId") or dataset_id
                log.debug(
                    "APIFY: run %s poll %d/%d status=%s",
                    run_id, attempt + 1, config.APIFY_MAX_ATTEMPTS, status,
                )
            except Exception as e:  # noqa: BLE001
                log.warning("APIFY poll failed for %s: %s", run_id, e)

        if status != "SUCCEEDED" or not dataset_id:
            log.warning("APIFY run %s ended status=%s — no items", run_id, status)
            return []

        try:
            resp = http.get(
                f"{API}/datasets/{dataset_id}/items",
                params={**params, "clean": "true", "format": "json"},
            )
            resp.raise_for_status()
            items = _items(resp.json())[:limit]
            log.info(
                "APIFY: run %s SUCCEEDED — %d item(s) fetched for %s/@%s",
                run_id, len(items), platform, username,
            )
            return items
        except Exception as e:  # noqa: BLE001
            log.warning("APIFY fetch failed for dataset %s: %s", dataset_id, e)
            return []
