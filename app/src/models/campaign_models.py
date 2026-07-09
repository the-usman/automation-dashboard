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
      Optionally pass ``competitors`` — manually chosen usernames harvested first;
      with a manual list the description becomes optional (discovery is skipped
      when it is blank).
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
    competitors: list[str] = Field(
        default_factory=list,
        max_length=50,
        description="apify mode: manually chosen competitor usernames (with or without @); "
        "harvested first, ahead of any discovered competitors",
    )

    @field_validator("candidate_source", mode="before")
    @classmethod
    def _valid_source(cls, v: str) -> str:
        v = (v or "apify").strip().lower()
        if v not in CANDIDATE_SOURCES:
            raise ValueError(f"candidate_source must be one of {CANDIDATE_SOURCES}")
        return v

    @field_validator("competitors")
    @classmethod
    def _norm_competitors(cls, v: list[str]) -> list[str]:
        seen: set[str] = set()
        cleaned: list[str] = []
        for raw in v:
            handle = _normalize_handle(raw)
            if handle and handle not in seen:
                seen.add(handle)
                cleaned.append(handle)
        return cleaned

    @model_validator(mode="after")
    def _require_auth(self) -> "CampaignRequest":
        if not self.sessionid and not self.credential_id:
            raise ValueError("provide either 'sessionid' or 'credential_id'")
        # apify mode needs something to harvest from: a description to discover
        # competitors, and/or a manual competitor list.
        if (
            self.candidate_source == "apify"
            and len(self.product_description.strip()) < 3
            and not self.competitors
        ):
            raise ValueError(
                "candidate_source='apify' needs product_description (min 3 chars) "
                "and/or a 'competitors' username list"
            )
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


def _normalize_handle(value: str) -> str:
    return (value or "").strip().lstrip("@").lower()


class CandidateModel(BaseModel):
    """One discovered follow candidate, streamed to Mongo as soon as it is found."""

    username: str
    source: str = ""        # e.g. "commenters:@competitor" | "instagram_suggested"
    discovered_at: str = ""
    flagged: bool = False   # true if on the do-not-follow list (merged at read time)


class CampaignCandidatesResponse(BaseModel):
    """Candidates (and the competitors they came from) found so far — polled by
    the frontend while the campaign runs."""

    campaign_id: str
    status: str
    stage: str
    competitors_found: int = 0
    competitors: list[CompetitorModel] = Field(default_factory=list)
    candidates_found: int
    candidates: list[CandidateModel]


class FlagRequest(BaseModel):
    """Flag an Instagram user as 'do not follow' (with or without a leading @)."""

    username: str = Field(..., min_length=1, description="Instagram username to never follow")
    reason: str = Field("", description="optional note about why the user is flagged")

    @field_validator("username")
    @classmethod
    def _norm_username(cls, v: str) -> str:
        v = _normalize_handle(v)
        if not v:
            raise ValueError("username must not be blank")
        return v


class FlagModel(BaseModel):
    username: str
    reason: str = ""
    flagged_at: str = ""


class FlagResponse(BaseModel):
    username: str
    flagged: bool
    reason: str = ""


class FlagListResponse(BaseModel):
    count: int
    flags: list[FlagModel]
