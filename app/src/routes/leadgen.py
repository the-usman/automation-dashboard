"""Lead Generation API.

Flow: describe the product/services → the LLM proposes Apify actor filters →
review/edit/approve → run the selected actor(s) in the background → leads land in
MongoDB.

Endpoints:
- POST   /leadgen/filters              — description → generated filters + draft_id (sync)
- GET    /leadgen/filters/{draft_id}   — fetch a saved draft
- POST   /leadgen/runs                 — approve & run selected actor(s) (background)
- GET    /leadgen/runs/{run_id}        — poll progress
- GET    /leadgen/runs/{run_id}/results— fetch stored leads
- POST   /leadgen/runs/{run_id}/cancel — stop a running run

Thin router → service, MongoDB as the source of truth — mirrors ``growth.py``.
``generate_filters`` is synchronous (OpenAI), so it is dispatched via
``asyncio.to_thread``.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException

from app.src.models.leadgen_models import (
    GenerateFiltersRequest,
    GenerateFiltersResponse,
    GoogleMapsFiltersModel,
    LeadsFinderFiltersModel,
    RunCreatedResponse,
    RunListItem,
    RunListResponse,
    RunRequest,
    RunResultsResponse,
    RunStatusResponse,
)
from app.src.services.leadgen_runner import launch_run, stop_run
from app.src.services.mongodb import get_db
from social_trend_analyzer.config import DEFAULT_CONFIG
from leadgen import generate_filters

router = APIRouter(prefix="/leadgen", tags=["Lead Generation"])

_FILTER_MODELS = {"google_maps": GoogleMapsFiltersModel, "leads_finder": LeadsFinderFiltersModel}


@router.post("/filters", response_model=GenerateFiltersResponse)
async def create_filters(req: GenerateFiltersRequest) -> GenerateFiltersResponse:
    """Generate Apify actor filters from a product/services description.

    Returns the proposed filters for both requested actors plus a `draft_id` and a
    recommendation of which actor(s) fit. Edit the filters freely, then pass them
    (or the `draft_id`) to POST /leadgen/runs to approve and launch.
    """
    config = dict(DEFAULT_CONFIG)
    fs = await asyncio.to_thread(generate_filters, req.product_description, config, req.actors, req.location)

    gm = GoogleMapsFiltersModel(**fs.google_maps.model_dump()) if fs.google_maps else None
    lf = LeadsFinderFiltersModel(**fs.leads_finder.model_dump()) if fs.leads_finder else None

    draft_id = str(uuid.uuid4())
    db = get_db()
    await db.leadgen_filter_drafts.insert_one({
        "draft_id": draft_id,
        "product_description": fs.product_description,
        "recommended_actors": fs.recommended_actors,
        "rationale": fs.rationale,
        "google_maps": gm.model_dump() if gm else None,
        "leads_finder": lf.model_dump() if lf else None,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })

    return GenerateFiltersResponse(
        draft_id=draft_id,
        product_description=fs.product_description,
        recommended_actors=fs.recommended_actors,
        rationale=fs.rationale,
        google_maps=gm,
        leads_finder=lf,
    )


@router.get("/filters/{draft_id}", response_model=GenerateFiltersResponse)
async def get_filters(draft_id: str) -> GenerateFiltersResponse:
    """Fetch a previously generated filter draft."""
    db = get_db()
    doc = await db.leadgen_filter_drafts.find_one({"draft_id": draft_id}, {"_id": 0})
    if not doc:
        raise HTTPException(status_code=404, detail=f"Draft {draft_id!r} not found")
    return GenerateFiltersResponse(
        draft_id=doc["draft_id"],
        product_description=doc.get("product_description", ""),
        recommended_actors=doc.get("recommended_actors", []),
        rationale=doc.get("rationale", ""),
        google_maps=GoogleMapsFiltersModel(**doc["google_maps"]) if doc.get("google_maps") else None,
        leads_finder=LeadsFinderFiltersModel(**doc["leads_finder"]) if doc.get("leads_finder") else None,
    )


async def _resolve_run(req: RunRequest) -> RunRequest:
    """Fill any actor filters missing from the request from the referenced draft.

    Inline (edited) filters always win over the draft. Raises 400/404 if a selected
    actor ends up with no filters.
    """
    draft = None
    if req.draft_id:
        db = get_db()
        draft = await db.leadgen_filter_drafts.find_one({"draft_id": req.draft_id}, {"_id": 0})
        if not draft:
            raise HTTPException(status_code=404, detail=f"Draft {req.draft_id!r} not found")

    resolved: dict = {}
    for actor in req.actors:
        model = getattr(req, actor)
        if model is None and draft and draft.get(actor):
            model = _FILTER_MODELS[actor](**draft[actor])
        if model is None:
            raise HTTPException(
                status_code=400,
                detail=f"no filters for actor {actor!r}: provide them inline or a draft_id that has them",
            )
        resolved[actor] = model

    return req.model_copy(update={
        "google_maps": resolved.get("google_maps"),
        "leads_finder": resolved.get("leads_finder"),
        "product_description": req.product_description or (draft or {}).get("product_description", ""),
    })


@router.post("/runs", response_model=RunCreatedResponse, status_code=202)
async def create_run(req: RunRequest) -> RunCreatedResponse:
    """Approve & run the selected actor(s) in the background. Returns a run_id.

    `actors` selects which to run — pass one (e.g. `["google_maps"]`) to run a
    single actor, or both. Poll GET /leadgen/runs/{id}; fetch leads at
    .../results; stop with .../cancel.
    """
    resolved = await _resolve_run(req)

    run_id = str(uuid.uuid4())
    started_at = datetime.now(timezone.utc).isoformat()
    db = get_db()
    await db.lead_runs.insert_one({
        "run_id": run_id,
        "campaign_id": resolved.campaign_id,          # foreign key → the campaign
        "status": "running",
        "stage": "queued",
        "actors": resolved.actors,
        "product_description": resolved.product_description,
        "filters": {a: getattr(resolved, a).model_dump() for a in resolved.actors},
        "progress": {a: {"status": "pending", "count": 0, "error": None} for a in resolved.actors},
        "total_leads": 0,
        "started_at": started_at,
        "completed_at": None,
        "errors": [],
    })

    launch_run(run_id, resolved)

    return RunCreatedResponse(
        run_id=run_id, campaign_id=resolved.campaign_id, status="running", actors=resolved.actors
    )


@router.get("/campaigns/{campaign_id}/runs", response_model=RunListResponse)
async def list_campaign_runs(campaign_id: str) -> RunListResponse:
    """All lead-gen runs for a campaign, newest first — populate the run dropdown.

    Filter the dropdown to `status == "completed"` runs when the user wants to view
    stored leads (those are the runs that have leads in the `leads` collection).
    """
    db = get_db()
    items: list[RunListItem] = []
    cursor = db.lead_runs.find(
        {"campaign_id": campaign_id},
        {"_id": 0, "run_id": 1, "status": 1, "stage": 1, "actors": 1,
         "total_leads": 1, "started_at": 1, "completed_at": 1},
    ).sort("started_at", -1)
    async for d in cursor:
        items.append(RunListItem(**d))
    return RunListResponse(campaign_id=campaign_id, count=len(items), runs=items)


@router.get("/runs/{run_id}", response_model=RunStatusResponse)
async def get_run_status(run_id: str) -> RunStatusResponse:
    """Progress of a lead-gen run: stage, per-actor status, and leads stored."""
    db = get_db()
    doc = await db.lead_runs.find_one({"run_id": run_id}, {"_id": 0})
    if not doc:
        raise HTTPException(status_code=404, detail=f"Run {run_id!r} not found")
    return RunStatusResponse(**doc)


@router.get("/runs/{run_id}/results", response_model=RunResultsResponse)
async def get_run_results(run_id: str) -> RunResultsResponse:
    """The leads stored by a run (normalized), plus any per-actor errors.

    Call this once the run's `status` is `completed` (see GET /leadgen/runs/{id});
    it reads the `leads` collection filtered by this `run_id`.
    """
    db = get_db()
    run = await db.lead_runs.find_one({"run_id": run_id}, {"_id": 0})
    if not run:
        raise HTTPException(status_code=404, detail=f"Run {run_id!r} not found")

    leads: list[dict] = []
    async for d in db.leads.find({"run_id": run_id}, {"_id": 0, "raw": 0}).sort("created_at", 1):
        leads.append({"source": d.get("source", ""), "created_at": d.get("created_at", ""), **d.get("lead", {})})

    return RunResultsResponse(
        run_id=run_id,
        status=run.get("status", ""),
        total_leads=run.get("total_leads", 0),
        leads=leads,
        errors=run.get("errors", []),
    )


@router.post("/runs/{run_id}/cancel")
async def cancel_run(run_id: str):
    """Send a stop signal to a running run (stops before the next actor)."""
    db = get_db()
    doc = await db.lead_runs.find_one({"run_id": run_id}, {"_id": 0, "status": 1})
    if not doc:
        raise HTTPException(status_code=404, detail=f"Run {run_id!r} not found")
    if doc["status"] != "running":
        return {"run_id": run_id, "status": doc["status"], "message": "Run is not running"}

    stopped = stop_run(run_id)
    if not stopped:
        updated = await db.lead_runs.find_one({"run_id": run_id}, {"_id": 0, "status": 1})
        return {"run_id": run_id, "status": (updated or {}).get("status", "completed"),
                "message": "Run already finished"}

    return {"run_id": run_id, "status": "stopping",
            "message": "Stop signal sent. Poll GET /leadgen/runs/{id} to confirm."}
