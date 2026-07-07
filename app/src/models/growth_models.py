"""Request/response models for the Instagram Growth API.

Mirrors the style of `app/src/models/email_models.py`: separate request and
response schemas, response models never leak secrets (passwords/sessions).
"""
from __future__ import annotations

from pydantic import BaseModel, Field


# ── Recommendation (synchronous) ──────────────────────────────────────────────
class RecommendRequest(BaseModel):
    """Give a profile/post link OR keywords. At least one is required."""

    source_url: str = Field("", description="Instagram profile or post URL to extract commenters from")
    keywords: list[str] = Field(default_factory=list, description="hashtags/keywords (used when no source_url)")
    product_description: str = Field("", description="product context used to rank candidates")
    n_posts: int = Field(5, ge=1, le=20, description="recent posts to scan when source_url is a profile")
    min_score: float = Field(60, ge=0, le=100, description="score >= this is suggested as 'follow'")


class Recommendation(BaseModel):
    username: str
    score: int
    action: str            # "follow" | "skip"
    reason: str


class RecommendResponse(BaseModel):
    run_id: str
    mode: str
    count: int
    recommendations: list[Recommendation]
    errors: list[dict] = Field(default_factory=list)


# ── Session bootstrap from a browser sessionid cookie ─────────────────────────
class SessionCreateRequest(BaseModel):
    """Create/refresh a stored IG session from a logged-in browser's cookie.

    Reusing the browser's trusted ``sessionid`` avoids the password-login
    checkpoint (``ChallengeRequired``).
    """

    sessionid: str = Field(..., min_length=8, description="value of the `sessionid` cookie from a logged-in instagram.com browser session")
    credential_id: str = Field("", description="credential_id to store under; defaults to the resolved IG username")


class SessionCreatedResponse(BaseModel):
    credential_id: str
    username: str
    status: str            # "saved"
    message: str = ""


# ── Follow / Unfollow background task ─────────────────────────────────────────
class ActionTaskRequest(BaseModel):
    action: str = Field(..., pattern="^(follow|unfollow)$")
    usernames: list[str] = Field(..., min_length=1, description="handles to follow/unfollow")
    credential_id: str = Field(..., description="MongoDB credential_id for the IG account")


class ActionTaskCreatedResponse(BaseModel):
    task_id: str
    status: str
    action: str
    total: int


class ActionResultModel(BaseModel):
    username: str
    action: str
    status: str            # "success" | "skipped" | "error"
    user_id: str = ""
    message: str = ""


class ActionTaskStatusResponse(BaseModel):
    task_id: str
    status: str            # "running" | "completed" | "stopped" | "failed"
    action: str
    total: int
    done: int
    success: int
    skipped: int
    failed: int
    current_index: int
    results: list[ActionResultModel]
    started_at: str
    completed_at: str | None = None
    error: str | None = None
