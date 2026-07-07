"""Background runner for description-driven growth campaigns.

A campaign finds users and follows them in the background. There are two ways to
find people, chosen per-campaign via ``req.candidate_source``:

* **apify** (default) — derive keywords from the description, discover competitor
  accounts and harvest their post **commenters** via Apify (public scrape — the
  user's account is only touched for the follow writes at the end).
* **instagram_suggested** — log in, then follow the account's own Instagram
  "Suggested for you" accounts, **reloading** the suggestion list until
  ``total_follows`` is reached (or until suggestions run dry → report partial).

Stages: apify → discovering → harvesting → authenticating → following → done;
suggested → authenticating → following → done.

MongoDB (``instagram_campaigns``) is the single source of truth for progress.
Cancellation is checked between actions (no mid-action interruption).
"""
from __future__ import annotations

import asyncio
import logging
import random
import re
from datetime import datetime, timezone
from pathlib import Path

from app.src.models.campaign_models import CampaignRequest
from app.src.services.instagram_task_runner import _load_credential  # reuse, do not duplicate
from app.src.services.mongodb import get_db
from social_trend_analyzer.config import DEFAULT_CONFIG, get_settings
from social_trend_analyzer.growth.competitors import discover_competitors  # Apify hashtag scrape
from social_trend_analyzer.growth.discovery import competitor_commenters  # Apify commenters
from social_trend_analyzer.growth.instagram import InstagramClient
from social_trend_analyzer.growth.schemas import InstagramCredential

logger = logging.getLogger(__name__)

_campaigns: dict[str, asyncio.Task] = {}
_stop_events: dict[str, asyncio.Event] = {}


def _campaign_log_path(campaign_id: str) -> Path:
    """Path to a campaign's activity log file (dir created on demand)."""
    log_dir = Path(getattr(get_settings(), "growth_log_dir", "./logs/growth"))
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir / f"{campaign_id}.log"


def _activity_log(campaign_id: str, message: str) -> None:
    """Append one timestamped line to the campaign's activity log file.

    Human-readable audit trail of what the campaign did: who Apify/Instagram
    surfaced and every follow. Best-effort — a log failure never breaks a run.
    Lines are small and infrequent (per reload / per follow, minutes apart), so a
    synchronous append is fine from this background task.
    """
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    try:
        with _campaign_log_path(campaign_id).open("a", encoding="utf-8") as fh:
            fh.write(f"{ts} [{campaign_id}] {message}\n")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Campaign %s: failed to write activity log: %s", campaign_id, exc)


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


def _harvest_candidates_via_apify(
    competitors: list[dict], req: CampaignRequest, config: dict
) -> list[str]:
    """Deduped commenters of each competitor's recent posts, scraped via Apify.

    Apify reads public data with its own accounts/proxies, so this never touches
    — or logs out — the user's Instagram account.
    """
    seen: set[str] = set()
    out: list[str] = []
    comp_handles = {c["username"] for c in competitors}
    for comp in competitors:
        if len(out) >= req.total_follows:
            break
        for cand in competitor_commenters(comp["username"], req.n_posts, config):
            handle = cand.username
            if not handle or handle in seen or handle in comp_handles:
                continue
            seen.add(handle)
            out.append(handle)
            if len(out) >= req.total_follows:
                break
    return out


async def _record_follow(db, campaign_id: str, client, username: str, idx: int, total: int) -> dict:
    """Follow one user and persist the result to the campaign's progress doc.

    Shared by both sources. Lets ``asyncio.CancelledError`` propagate so the caller
    can finalize the campaign; the Mongo write is skipped on cancel.
    """
    try:
        result = await asyncio.to_thread(client.follow, username)
        row = result.model_dump()
        logger.info(
            "Campaign %s: [%d/%d] follow @%s -> %s%s",
            campaign_id, idx, total, username, row["status"],
            f" ({row['message']})" if row.get("message") else "",
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.exception("Campaign %s: [%d/%d] error on @%s", campaign_id, idx, total, username)
        row = {"username": username, "action": "follow", "status": "error", "user_id": "", "message": str(exc)}

    _activity_log(
        campaign_id,
        f"FOLLOW [{idx}/{total}] @{username} -> {row['status']}"
        + (f" ({row['message']})" if row.get("message") else ""),
    )
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
    return row


async def _paced_wait(campaign_id: str, stop_event: asyncio.Event, config: dict) -> None:
    """Long, randomized gap between follows so Instagram doesn't 429 / block the
    device. Default 5-10 min → a batch naturally spreads across the day.

    Interruptible: returns immediately if a stop is requested.
    """
    lo = float(config.get("ig_follow_min_seconds", 300))
    hi = float(config.get("ig_follow_max_seconds", 600))
    delay = random.uniform(min(lo, hi), max(lo, hi))
    logger.info("Campaign %s: pacing — waiting ~%.1f min before next follow", campaign_id, delay / 60)
    try:
        await asyncio.wait_for(stop_event.wait(), timeout=delay)
    except asyncio.TimeoutError:
        pass  # normal: waited the full gap without a stop


async def _run_campaign(campaign_id: str, req: CampaignRequest, stop_event: asyncio.Event) -> None:
    db = get_db()
    config = dict(DEFAULT_CONFIG)

    async def _set(**fields) -> None:
        await db.instagram_campaigns.update_one({"campaign_id": campaign_id}, {"$set": fields})

    async def _finish(status: str, **extra) -> None:
        await _set(status=status, completed_at=datetime.now(timezone.utc).isoformat(), **extra)

    logger.info(
        "Campaign %s: START — source=%s description=%r target=%d",
        campaign_id, req.candidate_source, req.product_description, req.total_follows,
    )
    _activity_log(
        campaign_id,
        f"START source={req.candidate_source} target={req.total_follows} "
        f"description={req.product_description!r}",
    )

    async def _authenticate():
        """Log in with sessionid (preferred) or a stored credential. Returns the
        logged-in client + credential, or (None, None) after finishing failed."""
        await _set(stage="authenticating")
        if req.sessionid:
            cred = InstagramCredential(
                credential_id=req.credential_id or "sessionid", username="", sessionid=req.sessionid
            )
        else:
            cred = await _load_credential(db, req.credential_id)
            if cred is None:
                logger.warning("Campaign %s: credential %r not found", campaign_id, req.credential_id)
                await _finish("failed", stage="authenticating", error=f"Credential {req.credential_id!r} not found")
                return None, None
        try:
            client = await asyncio.to_thread(
                lambda: InstagramClient(cred, config, get_settings().ig_session_dir).login()
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("Campaign %s: IG login failed", campaign_id)
            await _finish("failed", stage="authenticating", error=f"login failed: {exc}")
            return None, None
        logger.info("Campaign %s: authenticated as @%s", campaign_id, cred.username or "?")
        _activity_log(campaign_id, f"AUTH ok as @{cred.username or '?'}")
        return client, cred

    async def _cleanup_session(cred) -> None:
        """Terminal step: drop the stored session so it isn't reused."""
        try:
            await db.instagram_accounts.update_one(
                {"credential_id": req.credential_id}, {"$unset": {"session": ""}}
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Campaign %s: failed to clear stored session: %s", campaign_id, exc)
        try:
            safe = re.sub(r"[^A-Za-z0-9._-]", "_", cred.username or "session")
            Path(get_settings().ig_session_dir).joinpath(f"{safe}.json").unlink(missing_ok=True)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Campaign %s: failed to delete session file: %s", campaign_id, exc)
        logger.info("Campaign %s: session deleted for %s", campaign_id, cred.username)

    if req.candidate_source == "instagram_suggested":
        await _run_suggested(campaign_id, req, stop_event, db, config, _set, _finish, _authenticate, _cleanup_session)
    else:
        await _run_apify(campaign_id, req, stop_event, db, config, _set, _finish, _authenticate, _cleanup_session)


async def _run_apify(campaign_id, req, stop_event, db, config, _set, _finish, _authenticate, _cleanup_session) -> None:
    # 1. Discover competitors via Apify (public scrape — no account touched)
    try:
        await _set(stage="discovering")
        top_n = int(req.competitor_limit or config.get("growth_competitor_limit", 10))
        competitor_list = await asyncio.to_thread(
            discover_competitors, req.product_description, config, top_n
        )
        competitors = [c.model_dump() for c in competitor_list.competitors]
    except Exception as exc:  # noqa: BLE001
        logger.exception("Campaign %s: competitor discovery failed", campaign_id)
        await _finish("failed", stage="discovering", error=f"discovery failed: {exc}")
        return

    await _set(competitors=competitors, competitors_found=len(competitors))
    logger.info(
        "Campaign %s: [discovering] found %d competitors: %s",
        campaign_id, len(competitors), ", ".join(c["username"] for c in competitors[:10]) or "(none)",
    )
    _activity_log(
        campaign_id,
        f"APIFY discovered {len(competitors)} competitors: "
        + (", ".join("@" + c["username"] for c in competitors) or "(none)"),
    )
    if not competitors:
        await _finish("completed", stage="done",
                      error="no competitors found — set TREND_APIFY_TOKEN and try a more "
                            "specific, hashtag-friendly description")
        return

    # 2. Harvest candidate users (commenters) via Apify (still no account touched)
    try:
        await _set(stage="harvesting")
        usernames = await asyncio.to_thread(
            _harvest_candidates_via_apify, competitors, req, config
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Campaign %s: harvesting failed", campaign_id)
        await _finish("failed", stage="harvesting", error=f"harvesting failed: {exc}")
        return

    await _set(candidates_found=len(usernames))
    logger.info(
        "Campaign %s: [harvesting] harvested %d candidate commenters via Apify",
        campaign_id, len(usernames),
    )
    _activity_log(
        campaign_id,
        f"APIFY harvested {len(usernames)} candidate commenters: "
        + (", ".join("@" + u for u in usernames) or "(none)"),
    )
    if not usernames:
        await _finish("completed", stage="done", error="no candidate users harvested")
        return

    # 3. Authenticate — only now (we have people to follow)
    client, cred = await _authenticate()
    if client is None:
        return

    # 4. Follow loop
    await _set(stage="following")
    total = len(usernames)
    try:
        for idx, username in enumerate(usernames, start=1):
            if stop_event.is_set():
                logger.info("Campaign %s: [following] stop requested — halting at %d/%d", campaign_id, idx - 1, total)
                await _cleanup_session(cred)
                await _finish("stopped", stage="following")
                return
            await _record_follow(db, campaign_id, client, username, idx, total)
            # Space follows out (5-10 min) — skip the wait after the last one.
            if idx < total:
                await _paced_wait(campaign_id, stop_event, config)
    except asyncio.CancelledError:
        logger.info("Campaign %s: [following] cancelled", campaign_id)
        await _cleanup_session(cred)
        await _finish("stopped", stage="following")
        raise

    logger.info("Campaign %s: DONE — %d users processed", campaign_id, total)
    _activity_log(campaign_id, f"DONE — {total} users processed")
    await _cleanup_session(cred)
    await _finish("completed", stage="done")


async def _run_suggested(campaign_id, req, stop_event, db, config, _set, _finish, _authenticate, _cleanup_session) -> None:
    # 1. Authenticate first — suggestions are read from the logged-in account.
    client, cred = await _authenticate()
    if client is None:
        return

    max_empty = int(config.get("growth_suggested_max_empty_reloads", 5))
    max_reloads = int(config.get("growth_suggested_max_reloads", 40))
    target = req.total_follows

    # 2. Reload the "Suggested for you" list and follow, until the target is met
    #    or suggestions run dry (report partial).
    await _set(stage="following")
    seen: set[str] = set()
    done = 0
    empty_reloads = 0
    reloads = 0
    own = (cred.username or "").lower()

    try:
        while done < target and empty_reloads < max_empty and reloads < max_reloads:
            if stop_event.is_set():
                logger.info("Campaign %s: [following] stop requested — halting at %d/%d", campaign_id, done, target)
                await _cleanup_session(cred)
                await _finish("stopped", stage="following")
                return

            reloads += 1
            batch = await asyncio.to_thread(client.suggested_users)
            fresh = [u for u in batch if u and u.lower() != own and u not in seen]
            logger.info(
                "Campaign %s: [following] reload #%d — %d suggested, %d new",
                campaign_id, reloads, len(batch), len(fresh),
            )
            _activity_log(
                campaign_id,
                f"INSTAGRAM reload #{reloads}: {len(batch)} suggested "
                + ("[" + ", ".join("@" + u for u in batch) + "]" if batch else "[]")
                + f", {len(fresh)} new "
                + ("[" + ", ".join("@" + u for u in fresh) + "]" if fresh else "[]"),
            )
            if not fresh:
                empty_reloads += 1
                continue
            empty_reloads = 0
            await _set(candidates_found=len(seen) + len(fresh))

            capped = False
            for username in fresh:
                if done >= target:
                    break
                if stop_event.is_set():
                    logger.info("Campaign %s: [following] stop requested — halting at %d/%d", campaign_id, done, target)
                    await _cleanup_session(cred)
                    await _finish("stopped", stage="following")
                    return
                seen.add(username)
                row = await _record_follow(db, campaign_id, client, username, done + 1, target)
                done += 1
                if row["status"] == "skipped" and "daily cap" in (row.get("message") or ""):
                    logger.info("Campaign %s: [following] daily cap reached — stopping", campaign_id)
                    capped = True
                    break
                # Space follows out (5-10 min) — skip after hitting the target.
                if done < target:
                    await _paced_wait(campaign_id, stop_event, config)
            if capped:
                break
    except asyncio.CancelledError:
        logger.info("Campaign %s: [following] cancelled at %d/%d", campaign_id, done, target)
        await _cleanup_session(cred)
        await _finish("stopped", stage="following")
        raise

    logger.info(
        "Campaign %s: DONE — %d/%d followed via Instagram suggestions (%d reloads)",
        campaign_id, done, target, reloads,
    )
    _activity_log(
        campaign_id,
        f"DONE — {done}/{target} followed via Instagram suggestions ({reloads} reloads)",
    )
    await _cleanup_session(cred)
    await _finish("completed", stage="done")
