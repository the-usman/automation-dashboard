"""Google Trends collector — free daily-trends RSS feed (no key required)."""
from __future__ import annotations

import logging

import httpx

log = logging.getLogger(__name__)


def collect(client: dict, config: dict) -> list[dict]:
    if "google_trends" not in (client.get("target_platforms") or []):
        return []
    try:
        import feedparser
    except ImportError:
        log.warning("feedparser not installed — skipping Google Trends (pip install feedparser)")
        return []
    region = client.get("region") or config.get("default_region", "US")
    url = f"https://trends.google.com/trending/rss?geo={region}"
    try:
        resp = httpx.get(url, timeout=30, follow_redirects=True)
        resp.raise_for_status()
        feed = feedparser.parse(resp.text)
        out = []
        for entry in feed.entries:
            raw = {
                "title": entry.get("title", ""),
                "link": entry.get("link", ""),
                "description": entry.get("summary", ""),
                "pubDate": entry.get("published", ""),
                "ht:approx_traffic": entry.get("ht_approx_traffic", ""),
            }
            out.append({"platform": "google_trends", "source": "google_trends_rss",
                        "source_actor": "google_trends_rss", "raw": raw})
        return out
    except Exception as e:  # noqa: BLE001
        log.warning("Google Trends RSS failed: %s", e)
        return []
