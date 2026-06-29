"""Pydantic models: the unified Post schema + the structured-output contracts
the LLM must satisfy (these replace the n8n ``Parse * JSON`` nodes — the model
is bound with ``.with_structured_output`` so parsing can't drift)."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

# --------------------------------------------------------------------------- #
# Unified post schema (port of the n8n "Normalize Posts" buildPost output)
# --------------------------------------------------------------------------- #


class Metrics(BaseModel):
    likes: int = 0
    comments: int = 0
    shares: int = 0
    views: int = 0


class Post(BaseModel):
    platform: str
    post_id: str
    post_url: str = ""
    author_handle: str = ""
    author_id: str = ""
    posted_at: str = ""
    text: str = ""
    media_urls: list[str] = Field(default_factory=list)
    metrics: Metrics = Field(default_factory=Metrics)
    hashtags: list[str] = Field(default_factory=list)
    engagement_total: int = 0
    scraped_at: str = ""
    source_actor: str = ""
    # raw upstream payload kept for debugging; excluded from LLM prompts
    raw: dict[str, Any] = Field(default_factory=dict, exclude=True)


# --------------------------------------------------------------------------- #
# LLM structured-output contracts
# --------------------------------------------------------------------------- #


class Trend(BaseModel):
    name: str = Field(description="Short, distinctive name of the trend")
    description: str = Field(description="What the trend is and why it is emerging")
    platforms_observed: list[str] = Field(default_factory=list)
    format_type: str = Field(default="", description="e.g. short-form video, carousel, meme, thread")
    format_template_description: str = Field(default="", description="Reusable template a creator could follow")
    growth_signal: str = Field(default="", description="emerging | accelerating | peaking | declining")
    score: int = Field(default=0, ge=0, le=100, description="0-100 opportunity score")
    supporting_post_urls: list[str] = Field(default_factory=list)


class TrendList(BaseModel):
    trends: list[Trend] = Field(default_factory=list)


class OverallSentiment(BaseModel):
    positive_pct: float = 0
    neutral_pct: float = 0
    negative_pct: float = 0


class Sentiment(BaseModel):
    overall_sentiment: OverallSentiment = Field(default_factory=OverallSentiment)
    dominant_keywords: list[str] = Field(default_factory=list)
    summary: str = ""


class QuickDraft(BaseModel):
    caption: str = ""
    hashtags: list[str] = Field(default_factory=list)


class Idea(BaseModel):
    trend_ref: str = Field(description="name of the trend this idea maps to")
    platform: str = ""
    format: str = ""
    structured_brief: str = ""
    quick_draft: QuickDraft = Field(default_factory=QuickDraft)
    visual_brief: str = ""
    cta: str = ""
    recommended_pipeline: str = ""
    delivery_mode: str = "both"
    estimated_effort: str = ""


class IdeaList(BaseModel):
    ideas: list[Idea] = Field(default_factory=list)
