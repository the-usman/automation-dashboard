"""Graph state definitions.

Two graphs, two states:

* :class:`RunState` — the top-level run (trigger → config → mode routing →
  fan-out over clients → aggregate). Mirrors n8n Phase 1-2 + the per-client loop.
* :class:`ClientState` — one client's pipeline (collect → normalize → dedupe →
  RAG → AI analysis → storage → reporting). Mirrors n8n Phase 3-7.
"""
from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict

from .schemas import Idea, Post, Sentiment, Trend


class ClientState(TypedDict, total=False):
    # --- context carried from the run ---
    run_id: str
    run_started_at: str
    config: dict[str, Any]
    client: dict[str, Any]
    deep_dive: bool

    # --- Phase 3: collection. Each collector appends; operator.add merges the
    #     parallel branches the way n8n's "Merge · Source Posts" did. ---
    raw_posts: Annotated[list[dict[str, Any]], operator.add]

    # --- Phase 3/4: normalized + deduped ---
    posts: list[Post]

    # --- Phase 4: RAG grounding ---
    rag_context: str

    # --- Phase 5: AI analysis ---
    trends: list[Trend]
    sentiment: Sentiment
    eligible_trends: list[Trend]
    ideas: list[Idea]

    # --- Phase 6: storage records (dual write) ---
    trend_rows: list[dict[str, Any]]
    idea_rows: list[dict[str, Any]]
    history_rows: list[dict[str, Any]]
    error_rows: list[dict[str, Any]]

    # accumulated soft errors (continueOnFail equivalent)
    errors: Annotated[list[dict[str, Any]], operator.add]


class RunState(TypedDict, total=False):
    # raw trigger payload (webhook body / manual / schedule)
    trigger: dict[str, Any]

    run_id: str
    run_started_at: str
    mode: str  # "client" | "client00"
    deep_dive: bool
    platform_filter: list[str]
    lookback_days_override: int | None
    webhook_client_id: str

    config: dict[str, Any]
    clients: list[dict[str, Any]]

    # per-client results aggregated via reducer as the fan-out completes
    results: Annotated[list[dict[str, Any]], operator.add]
