"""Request/response models for the description-driven growth Campaign API.

A campaign takes only a product description, discovers competitors, harvests
candidate users from those competitors, and follows them in the background.
Response models never leak credentials.
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class CampaignRequest(BaseModel):
    """Start a campaign from a product description alone."""

    product_description: str = Field(..., min_length=3, description="what the product is / who it serves")
    credential_id: str = Field(..., description="MongoDB credential_id for the IG account that will follow")
    total_follows: int = Field(50, ge=1, le=500, description="how many users to follow this campaign")
    days: int = Field(1, ge=1, le=30, description="intended spread in days (full multi-day pacing is a later milestone)")
    sources: list[str] = Field(
        default_factory=lambda: ["commenters"],
        description="candidate sources; currently 'commenters' is supported (followers pending)",
    )
    n_posts: int = Field(5, ge=1, le=20, description="recent posts per competitor to scan for commenters")
    competitor_limit: int | None = Field(None, ge=1, le=50, description="max competitors to use (default from config)")


class CampaignCreatedResponse(BaseModel):
    campaign_id: str
    status: str
    total_target: int


class CompetitorModel(BaseModel):
    username: str
    score: int = 0
    reason: str = ""
    followers: int = 0


class FollowedUserModel(BaseModel):
    username: str
    action: str             # "follow"
    status: str             # "success" | "skipped" | "error"
    user_id: str = ""
    message: str = ""


class CampaignStatusResponse(BaseModel):
    """Progress view — how far the campaign has got."""

    campaign_id: str
    status: str             # "running" | "completed" | "stopped" | "failed"
    stage: str              # "discovering" | "harvesting" | "following" | "done"
    total_target: int
    competitors_found: int
    candidates_found: int
    done: int               # users acted on so far
    success: int
    skipped: int
    failed: int
    current_index: int
    started_at: str
    completed_at: str | None = None
    error: str | None = None


class CampaignResultsResponse(BaseModel):
    """Full results — competitors discovered and every user followed."""

    campaign_id: str
    status: str
    product_description: str
    total_target: int
    competitors: list[CompetitorModel]
    results: list[FollowedUserModel]
    success: int
    skipped: int
    failed: int
    started_at: str
    completed_at: str | None = None
