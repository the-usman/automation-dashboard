"""Pydantic models for the Instagram Growth Agent.

Mirrors the structured-output style of ``social_trend_analyzer/schemas.py`` —
plain ``BaseModel`` subclasses with ``Field(description=...)`` so the LLM
recommendation step can be bound with ``.with_structured_output``.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class InstagramCredential(BaseModel):
    """One Instagram account's login material, loaded from MongoDB.

    ``session`` (an instagrapi settings blob) is preferred over ``password`` to
    avoid repeat logins. ``totp_seed`` enables 2FA without manual entry.
    """

    credential_id: str
    username: str
    password: str = ""
    totp_seed: str | None = None
    session: dict[str, Any] | None = None


class CandidateUser(BaseModel):
    """A discovered user, before LLM ranking."""

    username: str
    full_name: str = ""
    source: str = ""        # "competitor" | "search"
    source_ref: str = ""    # competitor url or the keywords used
    context: str = ""       # their comment text, or the post caption


class UserRecommendation(BaseModel):
    username: str = Field(description="the candidate's Instagram username")
    score: int = Field(default=0, ge=0, le=100, description="0-100 relevance/value to follow")
    reason: str = Field(default="", description="one-line justification")
    action: str = Field(default="skip", description="follow | skip")


class RecommendationList(BaseModel):
    recommendations: list[UserRecommendation] = Field(default_factory=list)


class ActionResult(BaseModel):
    username: str
    action: str             # "follow" | "unfollow"
    status: str             # "success" | "skipped" | "error"
    user_id: str = ""
    message: str = ""


class KeywordSet(BaseModel):
    """LLM structured-output target for keyword extraction from a description."""

    keywords: list[str] = Field(
        default_factory=list,
        description="search keywords/hashtags derived from the product description",
    )


class CompetitorAccount(BaseModel):
    """A discovered competitor Instagram account, ranked by reach/relevance."""

    username: str = Field(description="the competitor's Instagram username")
    full_name: str = Field(default="", description="display name if known")
    score: int = Field(default=0, ge=0, le=100, description="0-100 reach/relevance rank")
    reason: str = Field(default="", description="one-line justification for the ranking")
    followers: int = Field(default=0, description="follower count if the scraper returned it")
    source_ref: str = Field(default="", description="the keywords/hashtags this surfaced under")


class CompetitorList(BaseModel):
    competitors: list[CompetitorAccount] = Field(default_factory=list)
