"""LangGraph assembly for the Instagram Growth Agent.

One compiled ``GROWTH_GRAPH``:

    START → init_run → (route by mode)
        discover_product    → recommend → END
        discover_competitor → recommend → END
        execute_actions     → END

Mirrors the ``StateGraph`` + module-level compiled-graph convention in
``social_trend_analyzer/graph.py`` (init → route → nodes), including the
soft-error accumulation pattern (catch, log, append to ``errors``).
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from langgraph.graph import END, START, StateGraph

from ..config import DEFAULT_CONFIG, get_settings
from . import discovery
from .credentials import load_instagram_credential
from .instagram import InstagramClient
from .recommend import recommend
from .state import GrowthState

log = logging.getLogger(__name__)

_MODES = {"discover_product", "discover_competitor", "execute_actions"}


def _as_list(value) -> list[str]:
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    return [v.strip() for v in str(value or "").split(",") if v.strip()]


def _as_int(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_float(value, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


# --------------------------------------------------------------------------- #
# nodes
# --------------------------------------------------------------------------- #
def init_run(state: GrowthState) -> dict:
    t = state.get("trigger") or {}
    mode = t.get("mode", "discover_product")
    return {
        "run_id": str(uuid.uuid4()),
        "run_started_at": datetime.now(timezone.utc).isoformat(),
        "mode": mode if mode in _MODES else "discover_product",
        "config": {**DEFAULT_CONFIG},
        "product_description": t.get("product_description", ""),
        "keywords": _as_list(t.get("keywords")),
        "competitor_url": t.get("competitor_url", ""),
        "n_posts": _as_int(t.get("n_posts", 5), 5),
        "min_score": _as_float(t.get("min_score", DEFAULT_CONFIG.get("growth_min_score", 60)),
                               DEFAULT_CONFIG.get("growth_min_score", 60)),
        "action": t.get("action", "follow"),
        "usernames": _as_list(t.get("usernames")),
        "credential_id": t.get("credential_id", ""),
    }


def route_mode(state: GrowthState) -> str:
    return state.get("mode", "discover_product")


def discover_product_node(state: GrowthState) -> dict:
    try:
        cands = discovery.search_users(state.get("keywords", []), state["config"])
        return {"candidates": cands}
    except Exception as e:  # noqa: BLE001
        log.warning("discover_product failed: %s", e)
        return {"candidates": [], "errors": [{"node_name": "discover_product", "error_message": str(e)}]}


def discover_competitor_node(state: GrowthState) -> dict:
    try:
        cands = discovery.competitor_commenters(
            state.get("competitor_url", ""), state.get("n_posts", 5), state["config"]
        )
        return {"candidates": cands}
    except Exception as e:  # noqa: BLE001
        log.warning("discover_competitor failed: %s", e)
        return {"candidates": [], "errors": [{"node_name": "discover_competitor", "error_message": str(e)}]}


def recommend_node(state: GrowthState) -> dict:
    recs = recommend(
        state.get("product_description", ""),
        state.get("candidates", []),
        state.get("min_score", 60),
    )
    return {"recommendations": recs}


def execute_actions_node(state: GrowthState) -> dict:
    cred = load_instagram_credential(state.get("credential_id", ""))
    if cred is None:
        return {"errors": [{"node_name": "execute_actions",
                            "error_message": f"no credential for id={state.get('credential_id')!r}"}]}
    try:
        client = InstagramClient(cred, state["config"], get_settings().ig_session_dir).login()
    except Exception as e:  # noqa: BLE001
        log.warning("IG login failed: %s", e)
        return {"errors": [{"node_name": "execute_actions", "error_message": f"login failed: {e}"}]}

    action = state.get("action", "follow")
    results = []
    for username in state.get("usernames", []):
        results.append(client.follow(username) if action == "follow" else client.unfollow(username))
    return {"action_results": results}


# --------------------------------------------------------------------------- #
# assembly
# --------------------------------------------------------------------------- #
def build_growth_graph():
    g = StateGraph(GrowthState)
    g.add_node("init_run", init_run)
    g.add_node("discover_product", discover_product_node)
    g.add_node("discover_competitor", discover_competitor_node)
    g.add_node("recommend", recommend_node)
    g.add_node("execute_actions", execute_actions_node)

    g.add_edge(START, "init_run")
    g.add_conditional_edges("init_run", route_mode, {
        "discover_product": "discover_product",
        "discover_competitor": "discover_competitor",
        "execute_actions": "execute_actions",
    })
    g.add_edge("discover_product", "recommend")
    g.add_edge("discover_competitor", "recommend")
    g.add_edge("recommend", END)
    g.add_edge("execute_actions", END)
    return g.compile()


GROWTH_GRAPH = build_growth_graph()


def run(trigger: dict | None = None) -> dict:
    """Entry point — invoke the growth graph with a trigger payload."""
    return GROWTH_GRAPH.invoke({"trigger": trigger or {}, "action_results": [], "errors": []})
