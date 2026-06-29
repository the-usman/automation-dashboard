"""YouTube collector — free official Data API v3 (search → enrich with statistics)."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import httpx

from ..config import get_settings

log = logging.getLogger(__name__)
BASE = "https://www.googleapis.com/youtube/v3"


def collect(client: dict, config: dict, lookback_days_override: int | None = None) -> list[dict]:
    settings = get_settings()
    if not settings.youtube_api_key or "youtube" not in (client.get("target_platforms") or []):
        return []

    keywords = client.get("niche_keywords") or []
    lookback = int(lookback_days_override or config.get("lookback_days", 14))
    published_after = (datetime.now(timezone.utc) - timedelta(days=lookback)).isoformat()
    key = settings.youtube_api_key

    try:
        with httpx.Client(timeout=30) as http:
            search = http.get(f"{BASE}/search", params={
                "part": "snippet", "order": "viewCount", "publishedAfter": published_after,
                "q": " ".join(keywords), "maxResults": 25, "type": "video", "key": key,
            })
            search.raise_for_status()
            items = search.json().get("items", [])
            video_ids = [i["id"]["videoId"] for i in items if i.get("id", {}).get("videoId")]
            if not video_ids:
                return []

            enrich = http.get(f"{BASE}/videos", params={
                "part": "statistics,snippet", "id": ",".join(video_ids), "key": key,
            })
            enrich.raise_for_status()
            return [{"platform": "youtube", "source": "youtube_search", "source_actor": "youtube_api", "raw": v}
                    for v in enrich.json().get("items", [])]
    except Exception as e:  # noqa: BLE001
        log.warning("YouTube collect failed: %s", e)
        return []
