"""HH.kz Vacancy Scrape API.

Endpoints:
- POST /jobs/scrape-hh                  — start a scrape job (returns job_id, runs in background)
- GET  /jobs/scrape-hh/{job_id}         — poll progress
- GET  /jobs/scrape-hh/{job_id}/results — full vacancy list once completed

Mirrors the conventions of `app/src/routes/growth.py` (thin router, background
work via a runner service, MongoDB as the progress source of truth). The
underlying scrape (`jobs/scrape_hh.py`) is a multi-page Playwright pagination
loop that can take minutes, so it runs as a background asyncio task rather than
blocking the request.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException

from app.src.models.job_models import (
    ScrapeHHCreatedResponse,
    ScrapeHHRequest,
    ScrapeHHResultsResponse,
    ScrapeHHStatusResponse,
)
from app.src.services.hh_scrape_runner import launch_scrape_job
from app.src.services.mongodb import get_db
from jobs.scrape_hh import SEARCH_URL

router = APIRouter(prefix="/jobs", tags=["Jobs"])


@router.post("/scrape-hh", response_model=ScrapeHHCreatedResponse, status_code=202)
async def create_scrape_hh_job(req: ScrapeHHRequest) -> ScrapeHHCreatedResponse:
    """
    Start a scrape of hh.kz internship/junior/entry-level vacancies posted in the
    last 24 hours. Runs in the background (multiple paginated page loads via
    Playwright). Poll GET /jobs/scrape-hh/{job_id} for progress and
    GET .../results for the full vacancy list.
    """
    job_id = str(uuid.uuid4())
    started_at = datetime.now(timezone.utc).isoformat()

    db = get_db()
    await db.hh_scrape_jobs.insert_one({
        "job_id": job_id,
        "status": "running",
        "current_page": 0,
        "total_pages": 0,
        "found_so_far": 0,
        "total_found": 0,
        "internships": 0,
        "juniors": 0,
        "fulltime": 0,
        "results": [],
        "started_at": started_at,
        "completed_at": None,
        "error": None,
        "sheet_error": None,
    })

    launch_scrape_job(job_id, req.search_url or SEARCH_URL, req.upload_to_sheets)

    return ScrapeHHCreatedResponse(job_id=job_id, status="running")


@router.get("/scrape-hh/{job_id}", response_model=ScrapeHHStatusResponse)
async def get_scrape_hh_status(job_id: str) -> ScrapeHHStatusResponse:
    """Poll the progress of a running or finished scrape job."""
    db = get_db()
    doc = await db.hh_scrape_jobs.find_one({"job_id": job_id}, {"_id": 0, "results": 0})
    if not doc:
        raise HTTPException(status_code=404, detail=f"Job {job_id!r} not found")
    return ScrapeHHStatusResponse(**doc)


@router.get("/scrape-hh/{job_id}/results", response_model=ScrapeHHResultsResponse)
async def get_scrape_hh_results(job_id: str) -> ScrapeHHResultsResponse:
    """Full vacancy list once the job has completed."""
    db = get_db()
    doc = await db.hh_scrape_jobs.find_one({"job_id": job_id}, {"_id": 0})
    if not doc:
        raise HTTPException(status_code=404, detail=f"Job {job_id!r} not found")
    return ScrapeHHResultsResponse(
        job_id=doc["job_id"],
        status=doc["status"],
        total_found=doc.get("total_found", 0),
        results=doc.get("results", []),
    )
