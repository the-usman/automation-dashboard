"""Request/response models for the description-driven growth Campaign API.

A campaign takes only a product description, discovers competitors, harvests
candidate users from those competitors, and follows them in the background.
Response models never leak credentials.
"""
from __future__ import annotations

from pydantic import BaseModel, Field, field_validator, model_validator

# Where candidate users to follow come from.
CANDIDATE_SOURCES = ("apify", "instagram_suggested")


class CampaignRequest(BaseModel):
    """Start a campaign that finds users and follows them in the background.

    Two ways to find people (``candidate_source``):

    * ``"apify"`` (default) — derive keywords from ``product_description``, discover
      competitors and harvest their post commenters via Apify (no account reads).
    * ``"instagram_suggested"`` — follow the logged-in account's own Instagram
      "Suggested for you" accounts, reloading the suggestion list until
      ``total_follows`` is reached (no description / Apify needed).

    Authenticate with EITHER a browser ``sessionid`` cookie (preferred — no stored
    credentials, avoids the login checkpoint) OR a previously stored ``credential_id``.
    At least one is required.
    """

    product_description: str = Field(
        "", description="what the product is / who it serves — required for candidate_source='apify'"
    )
    candidate_source: str = Field(
        "apify",
        description="how to find users to follow: 'apify' (competitor commenters) or 'instagram_suggested' (the account's own IG suggestions)",
    )
    sessionid: str = Field("", description="Instagram `sessionid` cookie from a logged-in browser; the account that will follow")
    credential_id: str = Field("", description="alternative to sessionid: a stored MongoDB credential_id")
    total_follows: int = Field(50, ge=1, le=500, description="how many users to follow this campaign")
    days: int = Field(1, ge=1, le=30, description="intended spread in days (full multi-day pacing is a later milestone)")
    sources: list[str] = Field(
        default_factory=lambda: ["commenters", "likers"],
        description="apify mode: engagement signals to harvest ('commenters' and/or 'likers'); ignored for instagram_suggested",
    )
    n_posts: int = Field(5, ge=1, le=20, description="apify mode: recent posts per competitor to scan for commenters")
    competitor_limit: int | None = Field(None, ge=1, le=50, description="apify mode: max competitors to use (default from config)")

    @field_validator("candidate_source", mode="before")
    @classmethod
    def _valid_source(cls, v: str) -> str:
        v = (v or "apify").strip().lower()
        if v not in CANDIDATE_SOURCES:
            raise ValueError(f"candidate_source must be one of {CANDIDATE_SOURCES}")
        return v

    @model_validator(mode="after")
    def _require_auth(self) -> "CampaignRequest":
        if not self.sessionid and not self.credential_id:
            raise ValueError("provide either 'sessionid' or 'credential_id'")
        # A description is only needed to discover competitors (apify mode).
        if self.candidate_source == "apify" and len(self.product_description.strip()) < 3:
            raise ValueError("product_description (min 3 chars) is required for candidate_source='apify'")
        return self


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
