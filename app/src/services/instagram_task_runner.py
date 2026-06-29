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


async def _run_action_task(
    task_id: str,
    credential_id: str,
    action: str,
    usernames: list[str],
    stop_event: asyncio.Event,
) -> None:
    db = get_db()

    async def _mark(status: str, **extra) -> None:
        await db.instagram_tasks.update_one(
            {"task_id": task_id},
            {"$set": {"status": status,
                      "completed_at": datetime.now(timezone.utc).isoformat(),
                      **extra}},
        )

    # 1. Load + decrypt credential
    cred = await _load_credential(db, credential_id)
    if cred is None:
        await _mark("failed", error=f"Credential {credential_id!r} not found")
        return

    # 2. Login once (blocking → thread)
    try:
        client = await asyncio.to_thread(
            lambda: InstagramClient(cred, dict(DEFAULT_CONFIG), get_settings().ig_session_dir).login()
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("IG login failed for task %s", task_id)
        await _mark("failed", error=f"login failed: {exc}")
        return

    # 3. Per-username loop: act → record progress
    for username in usernames:
        if stop_event.is_set():
            await _mark("stopped")
            return
        try:
            fn = client.follow if action == "follow" else client.unfollow
            result = await asyncio.to_thread(fn, username)
            row = result.model_dump()
        except asyncio.CancelledError:
            await _mark("stopped")
            raise
        except Exception as exc:  # noqa: BLE001
            logger.exception("Task %s: error on %s", task_id, username)
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

    # 4. Done
    await _mark("completed")
