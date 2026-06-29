"""Graph state for the Instagram Growth Agent.

One ``GrowthState`` covers all three modes (discover-by-product,
discover-by-competitor, execute-actions). Mirrors the ``TypedDict(total=False)``
+ ``operator.add`` reducer convention from ``social_trend_analyzer/state.py``.
"""
from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict

from .schemas import ActionResult, CandidateUser, UserRecommendation


class GrowthState(TypedDict, total=False):
    # raw trigger payload (CLI / webhook / manual)
    trigger: dict[str, Any]

    run_id: str
    run_started_at: str
    mode: str  # "discover_product" | "discover_competitor" | "execute_actions"
    config: dict[str, Any]

    # --- discovery inputs ---
    product_description: str
    keywords: list[str]
    competitor_url: str
    n_posts: int
    min_score: float

    # --- action inputs ---
    action: str            # "follow" | "unfollow"
    usernames: list[str]
    credential_id: str

    # --- outputs ---
    candidates: list[CandidateUser]
    recommendations: list[UserRecommendation]
    action_results: Annotated[list[ActionResult], operator.add]

    # accumulated soft errors (continueOnFail equivalent)
    errors: Annotated[list[dict[str, Any]], operator.add]
