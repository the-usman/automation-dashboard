"""Pydantic request/response schemas for the reputation analyzer."""
from __future__ import annotations

from pydantic import BaseModel, Field, model_validator

PLATFORMS = ("instagram", "x", "tiktok")


class ReputationRequest(BaseModel):
    """Up to three optional usernames — at least one must be provided."""

    instagram_username: str | None = None
    x_username: str | None = None
    tiktok_username: str | None = None

    @model_validator(mode="after")
    def _require_one(self) -> "ReputationRequest":
        if not self.provided():
            raise ValueError(
                "provide at least one of: instagram_username, x_username, tiktok_username"
            )
        return self

    def provided(self) -> dict[str, str]:
        """Map of platform -> cleaned username for the fields that were given."""
        raw = {
            "instagram": self.instagram_username,
            "x": self.x_username,
            "tiktok": self.tiktok_username,
        }
        return {p: u.strip().lstrip("@") for p, u in raw.items() if u and u.strip()}


class NormalizedPost(BaseModel):
    """One post reduced to the only fields the scorer needs."""

    text: str = ""
    post_url: str = ""
    date: str = ""
    likes: int = 0
    comments: int = 0


class PlatformScores(BaseModel):
    """Per-platform score, null where the platform was skipped or failed."""

    instagram: int | None = None
    x: int | None = None
    tiktok: int | None = None


class ReputationResponse(BaseModel):
    overall_score: int = Field(ge=0, le=100)
    platform_scores: PlatformScores
    strong_points: list[str] = Field(default_factory=list)
    weak_points: list[str] = Field(default_factory=list)
