"""Background follow/unfollow runner.

Each follow/unfollow batch runs as an asyncio.Task. The client gets an immediate
202 with a task_id and polls GET /growth/tasks/{id}. MongoDB (instagram_tasks
collection) is the single source of truth for progress.

instagrapi is synchronous and rate-limited (it sleeps between actions), so each
action is executed via ``asyncio.to_thread`` to avoid blocking the event loop.
Cancellation is checked between actions (no mid-action interruption), exactly like
the email campaign runner.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from app.src.services.mongodb import get_db
from social_trend_analyzer.config import DEFAULT_CONFIG, get_settings
from social_trend_analyzer.growth.credentials import _decrypt
from social_trend_analyzer.growth.instagram import InstagramClient
from social_trend_analyzer.growth.schemas import InstagramCredential

logger = logging.getLogger(__name__)

_tasks: dict[str, asyncio.Task] = {}
_stop_events: dict[str, asyncio.Event] = {}

_CRED_COLLECTION = "instagram_accounts"


def launch_action_task(task_id: str, credential_id: str, action: str, usernames: list[str]) -> None:
    stop_event = asyncio.Event()
    _stop_events[task_id] = stop_event
    task = asyncio.create_task(
        _run_action_task(task_id, credential_id, action, usernames, stop_event)
    )
    _tasks[task_id] = task
    task.add_done_callback(lambda _: (_tasks.pop(task_id, None), _stop_events.pop(task_id, None)))


def launch_unfollow_all_task(
    task_id: str, sessionid: str, credential_id: str, keep: list[str], max_unfollows: int
) -> None:
    """Unfollow everyone the account follows except ``keep`` (background task).

    Registered in the same task/stop registries, so the existing
    GET /growth/tasks/{id} and .../cancel endpoints work unchanged.
    """
    stop_event = asyncio.Event()
    _stop_events[task_id] = stop_event
    task = asyncio.create_task(
        _run_unfollow_all(task_id, sessionid, credential_id, keep, max_unfollows, stop_event)
    )
    _tasks[task_id] = task
    task.add_done_callback(lambda _: (_tasks.pop(task_id, None), _stop_events.pop(task_id, None)))


def stop_action_task(task_id: str) -> bool:
    """Signal a running task to stop. Returns False if already finished/unknown."""
    stop_event = _stop_events.get(task_id)
    task = _tasks.get(task_id)
    if not stop_event or not task:
        return False
    stop_event.set()
    task.cancel()
    return True


async def _load_credential(db, credential_id: str) -> InstagramCredential | None:
    doc = await db[_CRED_COLLECTION].find_one({"credential_id": credential_id})
    if not doc:
        return None
    return InstagramCredential(
        credential_id=credential_id,
        username=doc.get("username", ""),
        password=_decrypt(doc.get("password") or doc.get("enc_password")),
        totp_seed=_decrypt(doc.get("totp_seed")) or None,
        session=doc.get("session") or None,
    )


async def _mark_task(db, task_id: str, status: str, **extra) -> None:
    await db.instagram_tasks.update_one(
        {"task_id": task_id},
        {"$set": {"status": status,
                  "completed_at": datetime.now(timezone.utc).isoformat(),
                  **extra}},
    )


async def _login_client(db, task_id: str, cred) -> InstagramClient | None:
    """Login once (blocking → thread). Marks the task failed and returns None on error."""
    try:
        return await asyncio.to_thread(
            lambda: InstagramClient(cred, dict(DEFAULT_CONFIG), get_settings().ig_session_dir).login()
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("IG login failed for task %s", task_id)
        await _mark_task(db, task_id, "failed", error=f"login failed: {exc}")
        return None


async def _process_usernames(
    db, task_id: str, client: InstagramClient, action: str,
    usernames: list[str], stop_event: asyncio.Event,
) -> bool:
    """Act on each username and record progress. Returns False if stopped early.

    Lets ``asyncio.CancelledError`` propagate after marking the task stopped.
    """
    total = len(usernames)
    for idx, username in enumerate(usernames, start=1):
        if stop_event.is_set():
            logger.info("Task %s: stop requested — halting at %d/%d", task_id, idx - 1, total)
            await _mark_task(db, task_id, "stopped")
            return False
        try:
            fn = client.follow if action == "follow" else client.unfollow
            result = await asyncio.to_thread(fn, username)
            row = result.model_dump()
            logger.info(
                "Task %s: [%d/%d] %s @%s -> %s%s",
                task_id, idx, total, action, username, row["status"],
                f" ({row['message']})" if row.get("message") else "",
            )
        except asyncio.CancelledError:
            logger.info("Task %s: cancelled at %d/%d", task_id, idx, total)
            await _mark_task(db, task_id, "stopped")
            raise
        except Exception as exc:  # noqa: BLE001
            logger.exception("Task %s: [%d/%d] error on @%s", task_id, idx, total, username)
            row = {"username": username, "action": action, "status": "error", "user_id": "", "message": str(exc)}

        await db.instagram_tasks.update_one(
            {"task_id": task_id},
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
    return True


async def _run_action_task(
    task_id: str,
    credential_id: str,
    action: str,
    usernames: list[str],
    stop_event: asyncio.Event,
) -> None:
    db = get_db()
    logger.info("Task %s: START — action=%s users=%d", task_id, action, len(usernames))

    # 1. Load + decrypt credential
    cred = await _load_credential(db, credential_id)
    if cred is None:
        logger.warning("Task %s: credential %r not found", task_id, credential_id)
        await _mark_task(db, task_id, "failed", error=f"Credential {credential_id!r} not found")
        return

    # 2. Login once
    client = await _login_client(db, task_id, cred)
    if client is None:
        return

    # 3. Per-username loop: act → record progress
    if not await _process_usernames(db, task_id, client, action, usernames, stop_event):
        return

    # 4. Done
    logger.info("Task %s: DONE — %d users processed", task_id, len(usernames))
    await _mark_task(db, task_id, "completed")


async def _run_unfollow_all(
    task_id: str,
    sessionid: str,
    credential_id: str,
    keep: list[str],
    max_unfollows: int,
    stop_event: asyncio.Event,
) -> None:
    """Fetch the account's following list, drop the keep-list, unfollow the rest."""
    db = get_db()
    logger.info("Task %s: START — unfollow_all keep=%d max=%d", task_id, len(keep), max_unfollows)

    # 1. Credential: sessionid (preferred, like campaigns) or stored credential
    if sessionid:
        cred = InstagramCredential(
            credential_id=credential_id or "sessionid", username="", sessionid=sessionid
        )
    else:
        cred = await _load_credential(db, credential_id)
        if cred is None:
            logger.warning("Task %s: credential %r not found", task_id, credential_id)
            await _mark_task(db, task_id, "failed", error=f"Credential {credential_id!r} not found")
            return

    # 2. Login once
    client = await _login_client(db, task_id, cred)
    if client is None:
        return

    # 3. Read the following list, then remove the keep-list and the account itself
    try:
        following = await asyncio.to_thread(client.following_usernames)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Task %s: could not read following list", task_id)
        await _mark_task(db, task_id, "failed", error=f"could not read following list: {exc}")
        return
    keep_set = set(keep)
    own = (cred.username or "").lower()
    targets = [u for u in following if u.lower() not in keep_set and u.lower() != own]
    if max_unfollows:
        targets = targets[:max_unfollows]
    await db.instagram_tasks.update_one(
        {"task_id": task_id},
        {"$set": {"total": len(targets), "following_count": len(following)}},
    )
    logger.info(
        "Task %s: following=%d keep=%d → unfollowing %d",
        task_id, len(following), len(keep), len(targets),
    )
    if not targets:
        await _mark_task(db, task_id, "completed")
        return

    # 4. Unfollow loop (same pacing/cap as any unfollow batch)
    if not await _process_usernames(db, task_id, client, "unfollow", targets, stop_event):
        return

    logger.info("Task %s: DONE — %d users unfollowed/processed", task_id, len(targets))
    await _mark_task(db, task_id, "completed")
