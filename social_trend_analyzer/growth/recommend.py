"""LLM ranking of candidate users against the product description.

Mirrors ``social_trend_analyzer/llm.py``: a prompt piped into
``get_chat(...).with_structured_output(RecommendationList)``. Guarded: with no
OpenAI key configured it falls back to a neutral pass-through so the graph still
runs offline (every candidate scored 50, action decided by ``min_score``).
"""
from __future__ import annotations

import json
import logging

from langchain_core.prompts import ChatPromptTemplate

from ..config import get_settings
from ..llm import get_chat
from .schemas import CandidateUser, RecommendationList, UserRecommendation

log = logging.getLogger(__name__)

_RECOMMEND_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "You are an Instagram growth strategist. Given a PRODUCT description and a "
            "list of candidate users (each with context — a comment they left or a post "
            "caption), score each user 0-100 on how valuable and relevant they are as a "
            "follow target for this product's audience. Set action='follow' when score "
            ">= {min_score}, otherwise 'skip'. Give a one-line reason per user. Only use "
            "the usernames provided; do not invent users.",
        ),
        (
            "human",
            "PRODUCT:\n{product}\n\nCANDIDATES (json lines):\n{candidates}\n\nRank them.",
        ),
    ]
)


def recommend_chain():
    return _RECOMMEND_PROMPT | get_chat(0.3).with_structured_output(RecommendationList)


def _candidates_for_prompt(candidates: list[CandidateUser], limit: int = 100) -> str:
    lines = []
    for c in candidates[:limit]:
        lines.append(json.dumps({
            "username": c.username, "full_name": c.full_name,
            "source": c.source, "context": c.context,
        }, ensure_ascii=False))
    return "\n".join(lines)


def _fallback(candidates: list[CandidateUser], min_score: float) -> list[UserRecommendation]:
    score = 50
    action = "follow" if score >= min_score else "skip"
    return [
        UserRecommendation(username=c.username, score=score, action=action,
                           reason="LLM disabled — neutral default")
        for c in candidates
    ]


def recommend(product_description: str, candidates: list[CandidateUser],
              min_score: float = 60) -> list[UserRecommendation]:
    if not candidates:
        return []
    if not get_settings().has_openai():
        log.info("Recommend: OpenAI not configured — using neutral fallback")
        return _fallback(candidates, min_score)
    try:
        result = recommend_chain().invoke({
            "product": product_description or "(no description provided)",
            "candidates": _candidates_for_prompt(candidates),
            "min_score": int(min_score),
        })
        return result.recommendations
    except Exception as e:  # noqa: BLE001
        log.warning("Recommendation LLM failed: %s", e)
        return _fallback(candidates, min_score)
