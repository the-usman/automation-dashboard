"""Orchestrator: request -> scrape each platform -> normalize -> score.

Each platform is scraped independently and wrapped so one platform failing (bad
username, actor error) marks it null and the request continues with the rest.
"""
from __future__ import annotations

import logging
import time
import uuid

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
    check_id = uuid.uuid4().hex[:8]  # correlates every log line of one request
    started = time.monotonic()
    provided = req.provided()  # {platform: username}, validated non-empty
    platform_posts: dict[str, list[NormalizedPost]] = {}

    log.info(
        "[%s] check started — %s",
        check_id,
        ", ".join(f"{p}/@{u}" for p, u in provided.items()),
    )

    for platform, username in provided.items():
        t0 = time.monotonic()
        try:
            raw = apify_client.scrape_profile(platform, username)
            posts = normalize.normalize(platform, raw)
            if posts:
                platform_posts[platform] = posts
                log.info(
                    "[%s] %s/@%s: %d raw items -> %d posts in %.1fs",
                    check_id, platform, username, len(raw), len(posts),
                    time.monotonic() - t0,
                )
            else:
                log.info(
                    "[%s] %s/@%s: no posts scraped (%.1fs)",
                    check_id, platform, username, time.monotonic() - t0,
                )
        except Exception as e:  # noqa: BLE001 — isolate one platform's failure
            log.warning(
                "[%s] %s/@%s failed after %.1fs: %s",
                check_id, platform, username, time.monotonic() - t0, e,
            )

    if not platform_posts:
        log.warning(
            "[%s] no data on any platform — returning overall_score=0 (%.1fs)",
            check_id, time.monotonic() - started,
        )
        return ReputationResponse(
            overall_score=0,
            platform_scores=PlatformScores(),
            strong_points=[],
            weak_points=[
                "No public posts could be retrieved for the provided account(s)."
            ],
        )

    log.info(
        "[%s] scoring %d platform(s): %s",
        check_id,
        len(platform_posts),
        ", ".join(f"{p}({len(posts)} posts)" for p, posts in platform_posts.items()),
    )
    result = scorer.score(platform_posts)

    # Enforce: only platforms we actually scored may have a non-null score.
    scored = result.platform_scores.model_dump()
    result.platform_scores = PlatformScores(
        instagram=scored.get("instagram") if "instagram" in platform_posts else None,
        x=scored.get("x") if "x" in platform_posts else None,
        tiktok=scored.get("tiktok") if "tiktok" in platform_posts else None,
    )
    log.info(
        "[%s] done in %.1fs — result: %s",
        check_id, time.monotonic() - started, result.model_dump_json(),
    )
    return result
