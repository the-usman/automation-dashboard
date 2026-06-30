"""Background runner for description-driven growth campaigns.

A campaign is created with only a product description. It then, in the background:

1. **discovering** — derives keywords and finds competitor accounts
   (``discover_competitors``).
2. **harvesting**  — collects candidate users from each competitor's recent-post
   commenters (``competitor_commenters``), deduped and capped to ``total_follows``.
3. **following**   — logs in once and follows each candidate, respecting the
   instagrapi per-action delay and daily cap.

MongoDB (``instagram_campaigns``) is the single source of truth for progress, so
the GET endpoints just read the document. Cancellation is checked between actions
(no mid-action interruption), exactly like ``instagram_task_runner``.

NOTE: full human-like randomized multi-day scheduling and follower-list harvesting
are later milestones; this runner uses the existing per-action delay/cap and the
'commenters' source.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from app.src.models.campaign_models import CampaignRequest
from app.src.services.instagram_task_runner import _load_credential  # reuse, do not duplicate
from app.src.services.mongodb import get_db
from social_trend_analyzer.config import DEFAULT_CONFIG, get_settings
from social_trend_analyzer.growth.competitors import discover_competitors
from social_trend_analyzer.growth.discovery import competitor_commenters
from social_trend_analyzer.growth.instagram import InstagramClient

logger = logging.getLogger(__name__)

_campaigns: dict[str, asyncio.Task] = {}
_stop_events: dict[str, asyncio.Event] = {}


def launch_campaign(campaign_id: str, req: CampaignRequest) -> None:
    stop_event = asyncio.Event()
    _stop_events[campaign_id] = stop_event
    task = asyncio.create_task(_run_campaign(campaign_id, req, stop_event))
    _campaigns[campaign_id] = task
    task.add_done_callback(
        lambda _: (_campaigns.pop(campaign_id, None), _stop_events.pop(campaign_id, None))
    )


def stop_campaign(campaign_id: str) -> bool:
    """Signal a running campaign to stop. False if already finished/unknown."""
    stop_event = _stop_events.get(campaign_id)
    task = _campaigns.get(campaign_id)
    if not stop_event or not task:
        return False
    stop_event.set()
    task.cancel()
    return True


def _profile_url(username: str) -> str:
    return f"https://www.instagram.com/{username.strip().lstrip('@').strip('/')}/"


def _harvest_candidates(competitors, req: CampaignRequest, config: dict) -> list[str]:
    """Collect deduped candidate usernames from competitors' commenters (blocking)."""
    seen: set[str] = set()
    out: list[str] = []
    for comp in competitors:
        if len(out) >= req.total_follows:
            break
        candidates = competitor_commenters(_profile_url(comp.username), req.n_posts, config)
        for cand in candidates:
            handle = cand.username
            if not handle or handle in seen or handle == comp.username:
                continue
            seen.add(handle)
            out.append(handle)
            if len(out) >= req.total_follows:
                break
    return out


async def _run_campaign(campaign_id: str, req: CampaignRequest, stop_event: asyncio.Event) -> None:
    db = get_db()
    config = dict(DEFAULT_CONFIG)

    async def _set(**fields) -> None:
        await db.instagram_campaigns.update_one({"campaign_id": campaign_id}, {"$set": fields})

    async def _finish(status: str, **extra) -> None:
        await _set(status=status, completed_at=datetime.now(timezone.utc).isoformat(), **extra)

    # 1. Discover competitors (blocking Apify/LLM → thread)
    try:
        await _set(stage="discovering")
        comp_list = await asyncio.to_thread(
            discover_competitors, req.product_description, config, req.competitor_limit
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Campaign %s: competitor discovery failed", campaign_id)
        await _finish("failed", stage="discovering", error=f"discovery failed: {exc}")
        return

    competitors = comp_list.competitors
    await _set(
        competitors=[
            {"username": c.username, "score": c.score, "reason": c.reason, "followers": c.followers}
            for c in competitors
        ],
        competitors_found=len(competitors),
    )
    if not competitors:
        await _finish("completed", stage="done", error="no competitors found for this description")
        return

    # 2. Harvest candidate users (blocking → thread)
    try:
        await _set(stage="harvesting")
        usernames = await asyncio.to_thread(_harvest_candidates, competitors, req, config)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Campaign %s: harvesting failed", campaign_id)
        await _finish("failed", stage="harvesting", error=f"harvesting failed: {exc}")
        return

    await _set(candidates_found=len(usernames))
    if not usernames:
        await _finish("completed", stage="done", error="no candidate users harvested")
        return

    # 3. Load credential + login once
    cred = await _load_credential(db, req.credential_id)
    if cred is None:
        await _finish("failed", stage="following", error=f"Credential {req.credential_id!r} not found")
        return
    try:
        await _set(stage="following")
        client = await asyncio.to_thread(
            lambda: InstagramClient(cred, config, get_settings().ig_session_dir).login()
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Campaign %s: IG login failed", campaign_id)
        await _finish("failed", stage="following", error=f"login failed: {exc}")
        return

    # 4. Follow loop — record progress per user
    for username in usernames:
        if stop_event.is_set():
            await _finish("stopped", stage="following")
            return
        try:
            result = await asyncio.to_thread(client.follow, username)
            row = result.model_dump()
        except asyncio.CancelledError:
            await _finish("stopped", stage="following")
            raise
        except Exception as exc:  # noqa: BLE001
            logger.exception("Campaign %s: error on %s", campaign_id, username)
            row = {"username": username, "action": "follow", "status": "error", "user_id": "", "message": str(exc)}

        await db.instagram_campaigns.update_one(
            {"campaign_id": campaign_id},
            {
                "$push": {"results": row},
                "$inc": {
                    "done": 1,
                    "current_index": 1,
                    "success": 1 if row["status"] == "success" else 0,
                    "skipped": 1 if row["status"] == "skipped" else 0,
                    "failed": 1 if row["status"] == "error" else 0,
                },
            },
        )

    await _finish("completed", stage="done")
