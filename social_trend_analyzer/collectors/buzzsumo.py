"""BuzzSumo collector — optional, gated by config['buzzsumo_enabled']."""
from __future__ import annotations

import logging

import httpx

from ..config import get_settings

log = logging.getLogger(__name__)


def collect(client: dict, config: dict) -> list[dict]:
    settings = get_settings()
    if not config.get("buzzsumo_enabled") or not settings.buzzsumo_api_key:
        return []
    keywords = client.get("niche_keywords") or []
    try:
        r = httpx.get(
            "https://api.buzzsumo.com/search/articles.json",
            params={"q": " ".join(keywords), "num_results": 25, "api_key": settings.buzzsumo_api_key},
            timeout=30,
        )
        r.raise_for_status()
        results = r.json().get("results", [])
        return [{"platform": "buzzsumo", "source": "buzzsumo", "source_actor": "buzzsumo_api", "raw": a}
                for a in results]
    except Exception as e:  # noqa: BLE001
        log.warning("BuzzSumo failed: %s", e)
        return []
