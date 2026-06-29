"""Apify collector — Instagram / TikTok / X / Facebook / LinkedIn.

Port of the n8n async pipeline (Build Apify Run Specs → Start Runs → poll loop →
Fetch Dataset Items → Map Apify Posts). Because Python can block, the n8n
single-Wait-node gymnastics collapse into one function: start every run, poll all
of them until terminal (or max attempts), then fetch each finished dataset.
"""
from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from ..config import get_settings

log = logging.getLogger(__name__)

API = "https://api.apify.com/v2"
TERMINAL = {"SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT"}

# Verified default actors (May 2026). Apify API uses '~' between org and actor.
# Any matching config.apify_actor_* value overrides the default.
DEFAULT_ACTORS = {
    "instagram": "apify~instagram-scraper",
    "tiktok": "clockworks~tiktok-scraper",
    "twitter": "apidojo~tweet-scraper",
    "facebook": "apify~facebook-posts-scraper",
    "linkedin": "apimaestro~linkedin-profile-detail",
}


def _actor(config: dict, platform: str) -> str:
    return config.get(f"apify_actor_{platform}") or DEFAULT_ACTORS[platform]


def _handles(tracked: list[dict], platform: str) -> list[str]:
    out = []
    for t in tracked:
        pf = t.get("platform")
        if not pf or pf in ("auto", platform):
            h = str(t.get("handle", "")).lstrip("@")
            if h:
                out.append(h)
    return out


def build_run_specs(client: dict, config: dict, deep_dive: bool) -> list[dict[str, Any]]:
    """One run spec per platform/handle. YouTube + Reddit are intentionally
    absent — they stay on the free official APIs."""
    platforms = client.get("target_platforms") or []
    keywords = [str(k).lstrip("#") for k in (client.get("niche_keywords") or [])]
    raw_tracked = client.get("tracked_accounts") or []
    tracked = [{"platform": "auto", "handle": t} if isinstance(t, str) else t for t in raw_tracked]
    limit = int(config.get("apify_results_limit", 50))
    want_deep = deep_dive or len(tracked) > 0
    specs: list[dict[str, Any]] = []

    def add(platform, source, actor_id, actor_input):
        if actor_id:
            specs.append({"platform": platform, "source": source, "actor_id": actor_id, "actor_input": actor_input})

    if "instagram" in platforms:
        tag_urls = [f"https://www.instagram.com/explore/tags/{k}/" for k in keywords]
        if tag_urls:
            add("instagram", "instagram_hashtag", _actor(config, "instagram"),
                {"resultsType": "posts", "directUrls": tag_urls, "resultsLimit": limit})
        if want_deep:
            users = _handles(tracked, "instagram")
            if users:
                add("instagram", "instagram_profile", _actor(config, "instagram"),
                    {"resultsType": "posts",
                     "directUrls": [f"https://www.instagram.com/{u}/" for u in users],
                     "resultsLimit": limit})

    if "tiktok" in platforms:
        add("tiktok", "tiktok", _actor(config, "tiktok"),
            {"hashtags": keywords, "searchQueries": keywords, "profiles": _handles(tracked, "tiktok"),
             "resultsPerPage": limit, "searchSection": "/video"})

    if "x" in platforms or "twitter" in platforms:
        add("x", "twitter", _actor(config, "twitter"),
            {"searchTerms": keywords,
             "twitterHandles": _handles(tracked, "x") + _handles(tracked, "twitter"),
             "sort": "Latest", "maxItems": limit})

    if "facebook" in platforms:
        fb = _handles(tracked, "facebook")
        if fb:
            add("facebook", "facebook", _actor(config, "facebook"),
                {"startUrls": [{"url": h if h.startswith("http") else f"https://www.facebook.com/{h}"} for h in fb],
                 "resultsLimit": limit})

    if "linkedin" in platforms:
        for h in _handles(tracked, "linkedin"):
            add("linkedin", "linkedin_profile", _actor(config, "linkedin"),
                {"username": h, "includeEmail": False})

    return specs


def _post_array(payload: Any) -> list[dict]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("items", "data"):
            if isinstance(payload.get(key), list):
                return payload[key]
        return [payload] if payload else []
    return []


def run_actor_sync(
    actor_id: str,
    actor_input: dict[str, Any],
    *,
    poll_interval: int = 10,
    max_attempts: int = 30,
) -> list[dict]:
    """Run a single Apify actor and return its dataset items.

    A focused, reusable version of :func:`collect`'s start→poll→fetch loop for
    callers that drive one actor with a bespoke input (e.g. the growth agent's
    discovery scrapes). Guarded: returns ``[]`` when no token / actor is set.
    """
    settings = get_settings()
    if not settings.apify_token or not actor_id:
        log.info("APIFY: no token/actor — skipping run_actor_sync(%s)", actor_id)
        return []

    params = {"token": settings.apify_token}
    with httpx.Client(timeout=60) as http:
        try:
            r = http.post(f"{API}/acts/{actor_id}/runs", params=params, json=actor_input)
            r.raise_for_status()
            d = r.json().get("data", {})
            run_id = d.get("id")
            dataset_id = d.get("defaultDatasetId")
            status = d.get("status", "READY")
        except Exception as e:  # noqa: BLE001
            log.warning("APIFY start failed for %s: %s", actor_id, e)
            return []

        for _ in range(max_attempts):
            if status in TERMINAL:
                break
            time.sleep(poll_interval)
            try:
                s = http.get(f"{API}/actor-runs/{run_id}", params=params)
                s.raise_for_status()
                d = s.json().get("data", {})
                status = d.get("status", status)
                dataset_id = d.get("defaultDatasetId") or dataset_id
            except Exception as e:  # noqa: BLE001
                log.warning("APIFY poll failed for %s: %s", run_id, e)

        if status != "SUCCEEDED" or not dataset_id:
            log.warning("APIFY run %s ended status=%s — no items", run_id, status)
            return []

        try:
            resp = http.get(f"{API}/datasets/{dataset_id}/items",
                            params={**params, "clean": "true", "format": "json"})
            resp.raise_for_status()
            return _post_array(resp.json())
        except Exception as e:  # noqa: BLE001
            log.warning("APIFY fetch failed for dataset %s: %s", dataset_id, e)
            return []


def collect(client: dict, config: dict, deep_dive: bool) -> list[dict[str, Any]]:
    """Return raw_posts envelopes: {platform, source, source_actor, raw}."""
    settings = get_settings()
    if not settings.apify_token:
        log.info("APIFY: no token configured — skipping paid scrapers")
        return []

    specs = build_run_specs(client, config, deep_dive)
    if not specs:
        return []

    params = {"token": settings.apify_token}
    interval = int(config.get("apify_poll_interval_seconds", 20))
    max_attempts = int(config.get("apify_poll_max_attempts", 15))
    runs: list[dict] = []

    with httpx.Client(timeout=60) as http:
        # 1) start every run (async)
        for spec in specs:
            try:
                r = http.post(f"{API}/acts/{spec['actor_id']}/runs", params=params, json=spec["actor_input"])
                r.raise_for_status()
                d = r.json().get("data", {})
                runs.append({**spec, "run_id": d.get("id"), "dataset_id": d.get("defaultDatasetId"),
                             "status": d.get("status", "READY")})
            except Exception as e:  # noqa: BLE001
                log.warning("APIFY start failed for %s: %s", spec["actor_id"], e)

        # 2) poll until all terminal or max attempts
        for attempt in range(max_attempts):
            pending = [r for r in runs if r["status"] not in TERMINAL and r.get("run_id")]
            if not pending:
                break
            time.sleep(interval)
            for run in pending:
                try:
                    s = http.get(f"{API}/actor-runs/{run['run_id']}", params=params)
                    s.raise_for_status()
                    d = s.json().get("data", {})
                    run["status"] = d.get("status", run["status"])
                    run["dataset_id"] = d.get("defaultDatasetId") or run.get("dataset_id")
                except Exception as e:  # noqa: BLE001
                    log.warning("APIFY poll failed for %s: %s", run["run_id"], e)

        # 3) fetch finished datasets
        out: list[dict[str, Any]] = []
        for run in runs:
            if run["status"] != "SUCCEEDED" or not run.get("dataset_id"):
                continue
            try:
                resp = http.get(f"{API}/datasets/{run['dataset_id']}/items",
                                params={**params, "clean": "true", "format": "json"})
                resp.raise_for_status()
                for raw in _post_array(resp.json()):
                    out.append({"platform": run["platform"], "source": run["source"],
                                "source_actor": run["actor_id"], "raw": raw})
            except Exception as e:  # noqa: BLE001
                log.warning("APIFY fetch failed for dataset %s: %s", run["dataset_id"], e)

    return out
