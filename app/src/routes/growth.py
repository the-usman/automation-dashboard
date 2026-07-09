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

from app.src.models.campaign_models import (
    CampaignCandidatesResponse,
    CampaignCreatedResponse,
    CampaignRequest,
    CampaignResultsResponse,
    CampaignStatusResponse,
    CandidateModel,
    FlagListResponse,
    FlagModel,
    FlagRequest,
    FlagResponse,
)
from app.src.models.growth_models import (
    ActionTaskCreatedResponse,
    ActionTaskRequest,
    ActionTaskStatusResponse,
    Recommendation,
    RecommendRequest,
    RecommendResponse,
    SessionCreatedResponse,
    SessionCreateRequest,
    UnfollowAllCreatedResponse,
    UnfollowAllRequest,
)
from app.src.services.instagram_campaign_runner import launch_campaign, stop_campaign
from app.src.services.instagram_session import build_session_from_sessionid
from app.src.services.instagram_task_runner import (
    launch_action_task,
    launch_unfollow_all_task,
    stop_action_task,
)
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


@router.post("/sessions", response_model=SessionCreatedResponse, status_code=201)
async def create_session(req: SessionCreateRequest) -> SessionCreatedResponse:
    """
    Create/refresh a stored Instagram session from a browser `sessionid` cookie.

    Log in to instagram.com in a browser, copy the `sessionid` cookie value, and
    POST it here. The backend validates it, resolves the username, and stores the
    session on the credential document (`instagram_accounts`). Campaigns/tasks then
    reuse this session instead of a password login — avoiding the checkpoint.

    The `sessionid` is a full account credential; it is used only to build the
    session and is never returned in the response.
    """
    try:
        username, settings = await asyncio.to_thread(build_session_from_sessionid, req.sessionid)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"sessionid login failed: {exc}")

    credential_id = req.credential_id or username
    db = get_db()
    await db.instagram_accounts.update_one(
        {"credential_id": credential_id},
        {"$set": {"credential_id": credential_id, "username": username, "session": settings}},
        upsert=True,
    )

    return SessionCreatedResponse(
        credential_id=credential_id,
        username=username,
        status="saved",
        message="Session stored. Use this credential_id in POST /growth/campaigns or /growth/tasks.",
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


@router.post("/unfollow-all", response_model=UnfollowAllCreatedResponse, status_code=202)
async def unfollow_all(req: UnfollowAllRequest) -> UnfollowAllCreatedResponse:
    """
    Unfollow **everyone** the account follows, except the `keep` usernames.

    The backend logs in once (with `sessionid` or a stored `credential_id`),
    reads the account's following list, removes the `keep` handles (and the
    account itself), and unfollows the rest in the background with the usual
    per-action delay and daily cap. `total` is 0 until the list is fetched.

    Poll GET /growth/tasks/{task_id} for progress; stop via POST .../cancel.
    """
    task_id = str(uuid.uuid4())
    started_at = datetime.now(timezone.utc).isoformat()

    db = get_db()
    await db.instagram_tasks.insert_one({
        "task_id": task_id,
        "credential_id": req.credential_id,
        "action": "unfollow_all",
        "status": "running",
        "total": 0,                      # set once the following list is fetched
        "keep": req.keep,
        "max_unfollows": req.max_unfollows,
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

    launch_unfollow_all_task(task_id, req.sessionid, req.credential_id, req.keep, req.max_unfollows)

    return UnfollowAllCreatedResponse(
        task_id=task_id,
        status="running",
        action="unfollow_all",
        keep_count=len(req.keep),
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


# ── Campaigns: description → competitors → auto-follow (background) ────────────
@router.post("/campaigns", response_model=CampaignCreatedResponse, status_code=202)
async def create_campaign(req: CampaignRequest) -> CampaignCreatedResponse:
    """
    Start a growth campaign. Returns immediately with a campaign_id; work runs in
    the background. Two ways to find users (`candidate_source`):

    * `apify` (default): discover competitors → harvest their post commenters →
      follow up to `total_follows` (needs `product_description` and/or a manual
      `competitors` username list — manual ones are harvested first, and with a
      manual list alone discovery is skipped).
    * `instagram_suggested`: follow the account's own Instagram "Suggested for you"
      accounts, reloading until `total_follows` is reached (no description needed).

    Both respect the per-action delay and daily cap. Poll GET /growth/campaigns/{id}
    for progress, GET .../candidates for live competitors + candidates,
    GET .../results for the full outcome, and POST .../cancel to stop.
    """
    campaign_id = str(uuid.uuid4())
    started_at = datetime.now(timezone.utc).isoformat()
    initial_stage = "authenticating" if req.candidate_source == "instagram_suggested" else "discovering"
    # Manual competitors are known now — seed them so the frontend sees them
    # immediately (the runner re-writes the list once discovery adds more).
    manual_competitors = [
        {"username": u, "score": 100, "reason": "added manually", "followers": 0}
        for u in req.competitors
    ]

    db = get_db()
    await db.instagram_campaigns.insert_one({
        "campaign_id": campaign_id,
        "credential_id": req.credential_id,
        "product_description": req.product_description,
        "status": "running",
        "stage": initial_stage,
        "total_target": req.total_follows,
        "competitors": manual_competitors,
        "competitors_found": len(manual_competitors),
        "candidates_found": 0,
        "results": [],
        "done": 0,
        "success": 0,
        "skipped": 0,
        "failed": 0,
        "current_index": 0,
        "started_at": started_at,
        "completed_at": None,
        "error": None,
    })

    launch_campaign(campaign_id, req)

    return CampaignCreatedResponse(
        campaign_id=campaign_id,
        status="running",
        total_target=req.total_follows,
    )


@router.get("/campaigns/{campaign_id}", response_model=CampaignStatusResponse)
async def get_campaign_status(campaign_id: str) -> CampaignStatusResponse:
    """Progress of a campaign: stage, competitors found, and follows done so far."""
    db = get_db()
    doc = await db.instagram_campaigns.find_one(
        {"campaign_id": campaign_id},
        {"_id": 0, "competitors": 0, "results": 0, "product_description": 0, "candidates": 0},
    )
    if not doc:
        raise HTTPException(status_code=404, detail=f"Campaign {campaign_id!r} not found")
    return CampaignStatusResponse(**doc)


@router.get("/campaigns/{campaign_id}/candidates", response_model=CampaignCandidatesResponse)
async def get_campaign_candidates(campaign_id: str) -> CampaignCandidatesResponse:
    """
    Competitors and candidates discovered so far — available **while the campaign
    is still running**.

    Competitors appear as soon as they are known (manual ones instantly, discovered
    ones right after discovery); candidates are streamed the moment they are
    harvested (per competitor in apify mode, per reload in instagram_suggested
    mode), so poll this to render both live. Each candidate carries `flagged` so
    the UI can show do-not-follow state; flag/unflag via POST /growth/flags and
    DELETE /growth/flags/{username}.
    """
    db = get_db()
    doc = await db.instagram_campaigns.find_one(
        {"campaign_id": campaign_id},
        {"_id": 0, "campaign_id": 1, "status": 1, "stage": 1, "candidates": 1,
         "candidates_found": 1, "competitors": 1, "competitors_found": 1},
    )
    if not doc:
        raise HTTPException(status_code=404, detail=f"Campaign {campaign_id!r} not found")

    candidates = doc.get("candidates", [])
    handles = [c["username"].lower() for c in candidates]
    flagged: set[str] = set()
    if handles:
        async for f in db.instagram_do_not_follow.find(
            {"username": {"$in": handles}}, {"_id": 0, "username": 1}
        ):
            flagged.add(f["username"])

    return CampaignCandidatesResponse(
        campaign_id=doc["campaign_id"],
        status=doc.get("status", ""),
        stage=doc.get("stage", ""),
        competitors_found=doc.get("competitors_found", 0),
        competitors=doc.get("competitors", []),
        candidates_found=doc.get("candidates_found", 0),
        candidates=[
            CandidateModel(
                username=c["username"],
                source=c.get("source", ""),
                discovered_at=c.get("discovered_at", ""),
                flagged=c["username"].lower() in flagged,
            )
            for c in candidates
        ],
    )


# ── Do-not-follow flags ─────────────────────────────────────────────────────────
@router.post("/flags", response_model=FlagResponse, status_code=201)
async def flag_user(req: FlagRequest) -> FlagResponse:
    """
    Flag an Instagram user as **do not follow**.

    Running and future campaigns check this list right before each follow, so a
    flag set while a campaign is running takes effect immediately — the user is
    recorded as `skipped` with message "flagged: do not follow" and never touched.
    """
    db = get_db()
    now = datetime.now(timezone.utc).isoformat()
    await db.instagram_do_not_follow.update_one(
        {"username": req.username},
        {"$set": {"username": req.username, "reason": req.reason, "flagged_at": now}},
        upsert=True,
    )
    return FlagResponse(username=req.username, flagged=True, reason=req.reason)


@router.get("/flags", response_model=FlagListResponse)
async def list_flags() -> FlagListResponse:
    """All users currently flagged as do-not-follow (newest first)."""
    db = get_db()
    docs = await db.instagram_do_not_follow.find({}, {"_id": 0}).sort(
        "flagged_at", -1
    ).to_list(length=1000)
    return FlagListResponse(count=len(docs), flags=[FlagModel(**d) for d in docs])


@router.delete("/flags/{username}", response_model=FlagResponse)
async def unflag_user(username: str) -> FlagResponse:
    """Remove a user from the do-not-follow list (they become followable again)."""
    handle = (username or "").strip().lstrip("@").lower()
    db = get_db()
    result = await db.instagram_do_not_follow.delete_one({"username": handle})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail=f"@{handle} is not flagged")
    return FlagResponse(username=handle, flagged=False)


@router.get("/campaigns/{campaign_id}/results", response_model=CampaignResultsResponse)
async def get_campaign_results(campaign_id: str) -> CampaignResultsResponse:
    """Full results: competitors discovered and every user this campaign followed."""
    db = get_db()
    doc = await db.instagram_campaigns.find_one({"campaign_id": campaign_id}, {"_id": 0})
    if not doc:
        raise HTTPException(status_code=404, detail=f"Campaign {campaign_id!r} not found")
    return CampaignResultsResponse(
        campaign_id=doc["campaign_id"],
        status=doc["status"],
        product_description=doc.get("product_description", ""),
        total_target=doc.get("total_target", 0),
        competitors=doc.get("competitors", []),
        results=doc.get("results", []),
        success=doc.get("success", 0),
        skipped=doc.get("skipped", 0),
        failed=doc.get("failed", 0),
        started_at=doc.get("started_at", ""),
        completed_at=doc.get("completed_at"),
    )


@router.post("/campaigns/{campaign_id}/cancel")
async def cancel_campaign(campaign_id: str):
    """Send a stop signal to a running campaign (stops before the next follow)."""
    db = get_db()
    doc = await db.instagram_campaigns.find_one({"campaign_id": campaign_id}, {"_id": 0, "status": 1})
    if not doc:
        raise HTTPException(status_code=404, detail=f"Campaign {campaign_id!r} not found")

    if doc["status"] != "running":
        return {"campaign_id": campaign_id, "status": doc["status"], "message": "Campaign is not running"}

    stopped = stop_campaign(campaign_id)
    if not stopped:
        updated = await db.instagram_campaigns.find_one({"campaign_id": campaign_id}, {"_id": 0, "status": 1})
        return {
            "campaign_id": campaign_id,
            "status": (updated or {}).get("status", "completed"),
            "message": "Campaign already finished",
        }

    return {
        "campaign_id": campaign_id,
        "status": "stopping",
        "message": "Stop signal sent. Poll GET /growth/campaigns/{id} to confirm.",
    }
