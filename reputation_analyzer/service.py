"""Orchestrator: request -> scrape each platform -> normalize -> score.

Each platform is scraped independently and wrapped so one platform failing (bad
username, actor error) marks it null and the request continues with the rest.
"""
from __future__ import annotations

import logging

from . import apify_client, normalize, scorer
from .schemas import (
    NormalizedPost,
    PlatformScores,
    ReputationRequest,
    ReputationResponse,
)

log = logging.getLogger(__name__)


def check_reputation(req: ReputationRequest) -> ReputationResponse:
    """Run the full reputation check for every provided platform."""
    provided = req.provided()  # {platform: username}, validated non-empty
    platform_posts: dict[str, list[NormalizedPost]] = {}

    for platform, username in provided.items():
        try:
            raw = apify_client.scrape_profile(platform, username)
            posts = normalize.normalize(platform, raw)
            if posts:
                platform_posts[platform] = posts
            else:
                log.info("reputation: no posts scraped for %s/%s", platform, username)
        except Exception as e:  # noqa: BLE001 — isolate one platform's failure
            log.warning("reputation: %s/%s failed: %s", platform, username, e)

    if not platform_posts:
        return ReputationResponse(
            overall_score=0,
            platform_scores=PlatformScores(),
            strong_points=[],
            weak_points=[
                "No public posts could be retrieved for the provided account(s)."
            ],
        )

    result = scorer.score(platform_posts)

    # Enforce: only platforms we actually scored may have a non-null score.
    scored = result.platform_scores.model_dump()
    result.platform_scores = PlatformScores(
        instagram=scored.get("instagram") if "instagram" in platform_posts else None,
        x=scored.get("x") if "x" in platform_posts else None,
        tiktok=scored.get("tiktok") if "tiktok" in platform_posts else None,
    )
    return result
