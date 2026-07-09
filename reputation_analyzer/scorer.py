"""OpenAI reputation scorer.

Sends all normalized posts (across every scraped platform) in one batch and gets
back a guaranteed-valid structured object via LangChain's
``.with_structured_output`` (no manual JSON parsing). Scores on a general
sentiment/quality lens.
"""
from __future__ import annotations

import json
import logging

from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI

from . import config
from .schemas import NormalizedPost, ReputationResponse

log = logging.getLogger(__name__)

_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "You are a social-media reputation analyst. You are given a brand or person's "
            "recent public posts, grouped by platform. Judge their online reputation on a "
            "general sentiment and quality lens: tone, professionalism, content quality, "
            "consistency, audience engagement, and anything controversial or risky.\n\n"
            "Return:\n"
            "- overall_score: 0-100 for the overall reputation across all platforms.\n"
            "- platform_scores: a 0-100 score for EACH platform that has posts below; use "
            "null for any platform with no posts.\n"
            "- strong_points: concrete strengths (2-5 short bullet strings).\n"
            "- weak_points: concrete shortcomings to improve (2-5 short bullet strings).\n"
            "Base every judgement only on the supplied posts; do not invent content.",
        ),
        ("human", "POSTS BY PLATFORM (json):\n{posts}"),
    ]
)


def _posts_payload(platform_posts: dict[str, list[NormalizedPost]]) -> str:
    return json.dumps(
        {p: [post.model_dump() for post in posts] for p, posts in platform_posts.items()},
        ensure_ascii=False,
    )


def score(platform_posts: dict[str, list[NormalizedPost]]) -> ReputationResponse:
    """Score the collected posts. Raises RuntimeError if OpenAI isn't configured."""
    if not config.has_openai():
        raise RuntimeError("OPENAI_API_KEY is not configured")

    payload = _posts_payload(platform_posts)
    log.info(
        "OPENAI: scoring with %s — %d platform(s), payload %d chars",
        config.OPENAI_MODEL, len(platform_posts), len(payload),
    )
    chat = ChatOpenAI(
        model=config.OPENAI_MODEL, api_key=config.OPENAI_API_KEY, temperature=0.2
    )
    chain = _PROMPT | chat.with_structured_output(ReputationResponse)
    result = chain.invoke({"posts": payload})
    log.info(
        "OPENAI: scored — overall=%s platform_scores=%s",
        result.overall_score, result.platform_scores.model_dump(),
    )
    return result
