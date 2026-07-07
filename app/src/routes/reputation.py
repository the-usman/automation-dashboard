"""Reputation API — POST /check-reputation.

Thin router: validate the request (Pydantic rejects all-null usernames), run the
blocking scrape+score off the event loop, return the reputation JSON.
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException

from reputation_analyzer.schemas import ReputationRequest, ReputationResponse
from reputation_analyzer.service import check_reputation

router = APIRouter(tags=["Reputation"])


@router.post("/check-reputation", response_model=ReputationResponse)
async def check_reputation_endpoint(req: ReputationRequest) -> ReputationResponse:
    """
    Score the reputation of one or more social accounts.

    Provide any combination of `instagram_username`, `x_username`, `tiktok_username`
    (at least one). Each platform is scraped and scored independently — one failing
    (e.g. username not found) marks it null and the rest still return.
    """
    try:
        return await asyncio.to_thread(check_reputation, req)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"reputation check failed: {exc}")
