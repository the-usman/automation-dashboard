"""LangGraph assembly.

Two compiled graphs:

* ``CLIENT_GRAPH`` — one client's pipeline (Phases 3-7). START fans out to the
  five collectors in parallel (their ``raw_posts`` merge via an ``operator.add``
  reducer, the LangGraph equivalent of n8n's *Merge · Source Posts*), then a
  linear normalize → dedupe → RAG → trends → sentiment → ideas → store → report.

* ``MAIN_GRAPH`` — the run (Phases 1-2): init → config → mode routing → build
  client contexts → sequentially run the client subgraph per client (the n8n
  *Process Each Client* splitInBatches loop) → aggregate.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from langgraph.graph import END, START, StateGraph

from . import reporting
from .collectors import apify, buzzsumo, google_trends, reddit, youtube
from .config import DEFAULT_CONFIG
from .dedupe import dedupe
from .llm import idea_chain, sentiment_chain, trend_chain
from .normalize import normalize
from .rag import retrieve_context, upsert_records
from .schemas import Sentiment, Trend
from .state import ClientState, RunState
from .storage import (
    load_client00_tracked_accounts,
    load_clients,
    load_config_sheet,
    prepare_storage_records,
    write_all,
)

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _as_list(value) -> list[str]:
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    return [v.strip() for v in str(value or "").split(",") if v.strip()]


def _posts_for_prompt(posts, limit: int = 80) -> str:
    """Compact, token-bounded json-lines view of the top posts by engagement."""
    import json

    top = sorted(posts, key=lambda p: p.engagement_total, reverse=True)[:limit]
    lines = []
    for p in top:
        lines.append(json.dumps({
            "platform": p.platform, "author": p.author_handle, "url": p.post_url,
            "text": p.text[:400], "metrics": p.metrics.model_dump(),
            "hashtags": p.hashtags[:15], "posted_at": p.posted_at,
        }, ensure_ascii=False))
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# CLIENT SUBGRAPH nodes (Phase 3-7)
# --------------------------------------------------------------------------- #
def collect_apify(state: ClientState) -> dict:
    return {"raw_posts": apify.collect(state["client"], state["config"], state.get("deep_dive", False))}


def collect_youtube(state: ClientState) -> dict:
    return {"raw_posts": youtube.collect(state["client"], state["config"])}


def collect_reddit(state: ClientState) -> dict:
    return {"raw_posts": reddit.collect(state["client"], state["config"])}


def collect_google_trends(state: ClientState) -> dict:
    return {"raw_posts": google_trends.collect(state["client"], state["config"])}


def collect_buzzsumo(state: ClientState) -> dict:
    return {"raw_posts": buzzsumo.collect(state["client"], state["config"])}


def normalize_node(state: ClientState) -> dict:
    return {"posts": normalize(state.get("raw_posts", []))}


def dedupe_node(state: ClientState) -> dict:
    posts = dedupe(state.get("posts", []), state["config"], state["client"].get("client_id", ""))
    return {"posts": posts}


def rag_node(state: ClientState) -> dict:
    return {"rag_context": retrieve_context(state["client"], state["config"])}


def trends_node(state: ClientState) -> dict:
    client = state["client"]
    posts = state.get("posts", [])
    if not posts:
        return {"trends": []}
    rag = state.get("rag_context", "")
    try:
        result = trend_chain().invoke({
            "niche": client.get("niche", ""),
            "keywords": ", ".join(client.get("niche_keywords") or []),
            "platforms": ", ".join(client.get("target_platforms") or []),
            "rag_context": rag,
            "posts": _posts_for_prompt(posts),
        })
        return {"trends": result.trends}
    except Exception as e:  # noqa: BLE001
        log.warning("Trend extraction failed: %s", e)
        return {"trends": [], "errors": [{"node_name": "Trend Extractor", "error_message": str(e)}]}


def sentiment_node(state: ClientState) -> dict:
    posts = state.get("posts", [])
    if not posts:
        return {"sentiment": Sentiment()}
    try:
        result = sentiment_chain().invoke({"posts": _posts_for_prompt(posts, 60)})
        return {"sentiment": result}
    except Exception as e:  # noqa: BLE001
        log.warning("Sentiment failed: %s", e)
        return {"sentiment": Sentiment(), "errors": [{"node_name": "Sentiment Analyzer", "error_message": str(e)}]}


def prepare_ideas_node(state: ClientState) -> dict:
    client = state["client"]
    min_score = float(client.get("min_trend_score", state["config"].get("default_min_trend_score", 70)))
    eligible = [t for t in state.get("trends", []) if float(t.score or 0) >= min_score]
    return {"eligible_trends": eligible}


def ideas_node(state: ClientState) -> dict:
    client = state["client"]
    eligible = state.get("eligible_trends", [])
    if not eligible:
        return {"ideas": []}
    try:
        result = idea_chain().invoke({
            "client_name": client.get("client_name", ""),
            "niche": client.get("niche", ""),
            "brand_voice": client.get("brand_voice", ""),
            "max_ideas": int(client.get("max_ideas_per_trend", 3)),
            "trends": [t.model_dump() for t in eligible],
            "sentiment": state.get("sentiment", Sentiment()).model_dump(),
        })
        return {"ideas": result.ideas}
    except Exception as e:  # noqa: BLE001
        log.warning("Idea generation failed: %s", e)
        return {"ideas": [], "errors": [{"node_name": "Idea Generator", "error_message": str(e)}]}


def store_node(state: ClientState) -> dict:
    records = prepare_storage_records(
        run_id=state["run_id"], client=state["client"],
        trends=state.get("trends", []), sentiment=state.get("sentiment", Sentiment()),
        ideas=state.get("ideas", []), posts=state.get("posts", []),
        errors=state.get("errors", []),
    )
    write_all(records)  # Google Sheets dual-write
    upsert_records("trends_signals", records["trend_rows"], "description")  # Qdrant
    upsert_records("content_ideas", records["idea_rows"], "structured_brief")
    return records


def report_node(state: ClientState) -> dict:
    reporting.report(state["client"], state.get("trends", []),
                     state.get("ideas", []), state.get("sentiment", Sentiment()))
    return {}


def build_client_graph():
    g = StateGraph(ClientState)
    for name, fn in [
        ("collect_apify", collect_apify), ("collect_youtube", collect_youtube),
        ("collect_reddit", collect_reddit), ("collect_google_trends", collect_google_trends),
        ("collect_buzzsumo", collect_buzzsumo),
    ]:
        g.add_node(name, fn)
        g.add_edge(START, name)          # parallel fan-out
        g.add_edge(name, "normalize")    # all converge on normalize

    g.add_node("normalize", normalize_node)
    g.add_node("dedupe", dedupe_node)
    g.add_node("rag", rag_node)
    g.add_node("trends", trends_node)
    g.add_node("sentiment", sentiment_node)
    g.add_node("prepare_ideas", prepare_ideas_node)
    g.add_node("ideas", ideas_node)
    g.add_node("store", store_node)
    g.add_node("report", report_node)

    g.add_edge("normalize", "dedupe")
    g.add_edge("dedupe", "rag")
    g.add_edge("rag", "trends")
    g.add_edge("trends", "sentiment")
    g.add_edge("sentiment", "prepare_ideas")
    g.add_edge("prepare_ideas", "ideas")
    g.add_edge("ideas", "store")
    g.add_edge("store", "report")
    g.add_edge("report", END)
    return g.compile()


CLIENT_GRAPH = build_client_graph()


# --------------------------------------------------------------------------- #
# MAIN GRAPH nodes (Phase 1-2 + the per-client loop)
# --------------------------------------------------------------------------- #
def init_run(state: RunState) -> dict:
    trigger = state.get("trigger") or {}
    mode = "client00" if trigger.get("mode") == "client00" else "client"
    return {
        "run_id": str(uuid.uuid4()),
        "run_started_at": datetime.now(timezone.utc).isoformat(),
        "mode": mode,
        "deep_dive": trigger.get("deep_dive") is True,
        "platform_filter": trigger.get("platforms") if isinstance(trigger.get("platforms"), list) else [],
        "lookback_days_override": trigger.get("lookback_days_override"),
        "webhook_client_id": trigger.get("client_id", ""),
    }


def load_config_node(state: RunState) -> dict:
    config = {**DEFAULT_CONFIG, **load_config_sheet()}
    return {"config": config}


def route_mode(state: RunState) -> str:
    return "build_client00" if state.get("mode") == "client00" else "build_clients"


def _build_client_context(row: dict, config: dict) -> dict:
    cid = str(row.get("client_id", "")).strip()
    return {
        "client_id": cid,
        "client_name": row.get("client_name", ""),
        "niche": row.get("niche", ""),
        "brand_voice": row.get("brand_voice", ""),
        "target_platforms": _as_list(row.get("target_platforms")),
        "tracked_accounts": _as_list(row.get("tracked_accounts")),
        "niche_keywords": _as_list(row.get("niche_keywords")),
        "target_subreddits": _as_list(row.get("target_subreddits")),
        "region": row.get("region") or config.get("default_region", "US"),
        "min_trend_score": float(row.get("min_trend_score") or config.get("default_min_trend_score", 70)),
        "max_ideas_per_trend": int(row.get("max_ideas_per_trend") or 3),
        "rag_enabled": str(row.get("rag_enabled", "true")).lower() == "true",
        "qdrant_kb_namespace": row.get("qdrant_kb_namespace") or f"client_kb_{cid}",
        "report_email": str(row.get("report_email", "false")).lower() == "true",
        "report_notion": str(row.get("report_notion", "false")).lower() == "true",
        "report_slack": str(row.get("report_slack", "false")).lower() == "true",
        "recipient_email": row.get("recipient_email", ""),
        "notion_database_id": row.get("notion_database_id", ""),
        "slack_webhook_url": row.get("slack_webhook_url", ""),
    }


def build_clients(state: RunState) -> dict:
    config = state["config"]
    rows = load_clients()
    active = [r for r in rows if str(r.get("active", "true")).lower() == "true"]
    wanted = state.get("webhook_client_id")
    if wanted:
        active = [r for r in active if str(r.get("client_id")) == str(wanted)]
    return {"clients": [_build_client_context(r, config) for r in active]}


def build_client00(state: RunState) -> dict:
    config = state["config"]
    tracked = load_client00_tracked_accounts()
    client = {
        "client_id": "client00", "client_name": "Client00 Market Scan",
        "niche": "cross-niche social market scan", "brand_voice": "",
        "target_platforms": ["instagram", "tiktok", "facebook", "linkedin", "youtube", "reddit", "google_trends"],
        "tracked_accounts": tracked,
        "niche_keywords": _as_list(config.get("client00_keywords")) or ["viral", "trending", "popular"],
        "target_subreddits": _as_list(config.get("client00_subreddits")) or ["popular", "all"],
        "region": config.get("client00_region", "US"),
        "min_trend_score": float(config.get("default_min_trend_score", 70)),
        "max_ideas_per_trend": 3, "rag_enabled": False, "qdrant_kb_namespace": "",
        "report_email": False, "report_notion": False, "report_slack": False,
        "recipient_email": "", "notion_database_id": "", "slack_webhook_url": "",
    }
    return {"clients": [client]}


def process_clients(state: RunState) -> dict:
    """The n8n 'Process Each Client' loop: run the subgraph once per client."""
    results: list[dict[str, Any]] = []
    pf = state.get("platform_filter") or []
    for client in state.get("clients", []):
        if pf:  # webhook platform override
            client = {**client, "target_platforms": [p for p in client["target_platforms"] if p in pf]}
        out = CLIENT_GRAPH.invoke({
            "run_id": state["run_id"], "run_started_at": state["run_started_at"],
            "config": state["config"], "client": client, "deep_dive": state.get("deep_dive", False),
            "raw_posts": [], "errors": [],
        })
        results.append({
            "client_id": client["client_id"],
            "trends": [t.model_dump() if isinstance(t, Trend) else t for t in out.get("trends", [])],
            "ideas": [i.model_dump() for i in out.get("ideas", [])],
            "post_count": len(out.get("posts", [])),
            "sentiment": out.get("sentiment", Sentiment()).model_dump(),
        })
    return {"results": results}


def build_main_graph():
    g = StateGraph(RunState)
    g.add_node("init_run", init_run)
    g.add_node("load_config", load_config_node)
    g.add_node("build_clients", build_clients)
    g.add_node("build_client00", build_client00)
    g.add_node("process_clients", process_clients)

    g.add_edge(START, "init_run")
    g.add_edge("init_run", "load_config")
    g.add_conditional_edges("load_config", route_mode,
                            {"build_clients": "build_clients", "build_client00": "build_client00"})
    g.add_edge("build_clients", "process_clients")
    g.add_edge("build_client00", "process_clients")
    g.add_edge("process_clients", END)
    return g.compile()


MAIN_GRAPH = build_main_graph()


def run(trigger: dict | None = None) -> dict:
    """Entry point — invoke the whole run graph with a trigger payload."""
    return MAIN_GRAPH.invoke({"trigger": trigger or {}, "results": []})
