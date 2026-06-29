"""Instagram Growth API.

Endpoints:
- POST /growth/recommend          — extract + rank users from a profile/post link or keywords (sync)
- POST /growth/tasks              — trigger a follow/unfollow batch (returns task_id, runs in background)
- GET  /growth/tasks/{task_id}    — poll progress
- POST /growth/tasks/{task_id}/cancel — stop a running batch

Mirrors the conventions of `app/src/routes/email_outreach.py` (thin router,
background work via a runner service, MongoDB as the progress source of truth).
The growth agent's `run()` is synchronous, so it is dispatched via asyncio.to_thread.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException

from app.src.models.growth_models import (
    ActionTaskCreatedResponse,
    ActionTaskRequest,
    ActionTaskStatusResponse,
    Recommendation,
    RecommendRequest,
    RecommendResponse,
)
from app.src.services.instagram_task_runner import launch_action_task, stop_action_task
from app.src.services.mongodb import get_db
from social_trend_analyzer.growth import run as run_growth

router = APIRouter(prefix="/growth", tags=["Instagram Growth"])


@router.post("/recommend", response_model=RecommendResponse)
async def recommend(req: RecommendRequest) -> RecommendResponse:
    """
    Extract candidate users and rank them as follow targets.

    Provide either:
    - `source_url`: an Instagram **profile** link (scans its recent `n_posts`
      posts' commenters) or a single **post** link (scans that post's commenters), or
    - `keywords`: hashtags/keywords (scans posts under them and takes the authors).

    `product_description` is used by the LLM to score relevance. Returns each
    candidate with a 0-100 score, a suggested action (follow/skip), and a reason.
    """
    if not req.source_url and not req.keywords:
        raise HTTPException(status_code=400, detail="Provide either source_url (profile/post link) or keywords")

    if req.source_url:
        trigger = {
            "mode": "discover_competitor",
            "competitor_url": req.source_url,
            "n_posts": req.n_posts,
            "product_description": req.product_description,
            "min_score": req.min_score,
        }
    else:
        trigger = {
            "mode": "discover_product",
            "keywords": req.keywords,
            "product_description": req.product_description,
            "min_score": req.min_score,
        }

    # run_growth is synchronous/blocking (Apify + OpenAI) → run off the event loop
    result = await asyncio.to_thread(run_growth, trigger)

    recs = [Recommendation(**r.model_dump()) for r in result.get("recommendations", [])]
    return RecommendResponse(
        run_id=result.get("run_id", ""),
        mode=result.get("mode", ""),
        count=len(recs),
        recommendations=recs,
        errors=result.get("errors", []),
    )


@router.post("/tasks", response_model=ActionTaskCreatedResponse, status_code=202)
async def create_action_task(req: ActionTaskRequest) -> ActionTaskCreatedResponse:
    """
    Trigger a follow/unfollow batch. Returns immediately with a task_id.

    The batch runs in the background (instagrapi logs in once, then acts on each
    username with a rate-limit delay between actions, respecting the daily cap).
    Poll GET /growth/tasks/{task_id} for progress; stop via .../cancel.
    """
    task_id = str(uuid.uuid4())
    started_at = datetime.now(timezone.utc).isoformat()

    db = get_db()
    await db.instagram_tasks.insert_one({
        "task_id": task_id,
        "credential_id": req.credential_id,
        "action": req.action,
        "status": "running",
        "total": len(req.usernames),
        "done": 0,
        "success": 0,
        "skipped": 0,
        "failed": 0,
        "current_index": 0,
        "results": [],
        "started_at": started_at,
        "completed_at": None,
        "error": None,
    })

    launch_action_task(task_id, req.credential_id, req.action, req.usernames)

    return ActionTaskCreatedResponse(
        task_id=task_id,
        status="running",
        action=req.action,
        total=len(req.usernames),
    )


@router.get("/tasks/{task_id}", response_model=ActionTaskStatusResponse)
async def get_task_status(task_id: str) -> ActionTaskStatusResponse:
    """Poll the progress of a running or finished follow/unfollow task."""
    db = get_db()
    doc = await db.instagram_tasks.find_one({"task_id": task_id}, {"_id": 0})
    if not doc:
        raise HTTPException(status_code=404, detail=f"Task {task_id!r} not found")
    return ActionTaskStatusResponse(**doc)


@router.post("/tasks/{task_id}/cancel")
async def cancel_task(task_id: str):
    """
    Send a stop signal to a running task.

    The task stops after the current action finishes (no mid-action interruption).
    Poll GET /growth/tasks/{id} until status = "stopped" to confirm.
    """
    db = get_db()
    doc = await db.instagram_tasks.find_one({"task_id": task_id}, {"_id": 0, "status": 1})
    if not doc:
        raise HTTPException(status_code=404, detail=f"Task {task_id!r} not found")

    if doc["status"] != "running":
        return {"task_id": task_id, "status": doc["status"], "message": "Task is not running"}

    stopped = stop_action_task(task_id)
    if not stopped:
        updated = await db.instagram_tasks.find_one({"task_id": task_id}, {"_id": 0, "status": 1})
        return {
            "task_id": task_id,
            "status": (updated or {}).get("status", "completed"),
            "message": "Task already finished",
        }

    return {
        "task_id": task_id,
        "status": "stopping",
        "message": "Stop signal sent. Poll GET /growth/tasks/{id} to confirm.",
    }
