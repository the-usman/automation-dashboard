"""Google Sheets I/O + storage-record preparation.

Replaces every n8n ``Sheets ·`` node plus ``Load Config`` / ``Load Clients`` /
``Load Client00 Tracked Accounts`` / ``Load Recent History``. All sheet access is
guarded: with no service-account / sheet id configured the loaders return empty
and the appenders are no-ops, so the graph still runs end-to-end offline.

``prepare_storage_records`` is the faithful port of the n8n node of the same name.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any

from .config import get_settings
from .schemas import Idea, Post, Sentiment, Trend

log = logging.getLogger(__name__)

try:  # optional dependency
    import gspread
    from google.oauth2.service_account import Credentials
except Exception:  # noqa: BLE001
    gspread = None  # type: ignore

_SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


@lru_cache
def _spreadsheet():
    settings = get_settings()
    if gspread is None or not settings.google_service_account_json or not settings.google_sheet_id:
        return None
    try:
        creds = Credentials.from_service_account_file(settings.google_service_account_json, scopes=_SCOPES)
        return gspread.authorize(creds).open_by_key(settings.google_sheet_id)
    except Exception as e:  # noqa: BLE001
        log.warning("Google Sheets auth failed: %s", e)
        return None


def _records(tab: str) -> list[dict]:
    ss = _spreadsheet()
    if not ss:
        return []
    try:
        return ss.worksheet(tab).get_all_records()
    except Exception as e:  # noqa: BLE001
        log.warning("Sheet read '%s' failed: %s", tab, e)
        return []


# --------------------------------------------------------------------------- #
# Loaders (replace n8n Load * nodes)
# --------------------------------------------------------------------------- #
def load_config_sheet() -> dict[str, Any]:
    """Overlay the Config sheet's key/value rows. Empty if no sheet."""
    out: dict[str, Any] = {}
    for row in _records("Config"):
        key = row.get("key") or row.get("Key") or row.get("name")
        value = row.get("value", row.get("Value", row.get("setting")))
        if key:
            out[str(key).strip()] = value
    return out


def load_clients() -> list[dict]:
    return _records("Clients")


def load_client00_tracked_accounts() -> list[dict]:
    rows = _records("Client00_Tracked_Accounts")
    return [{"platform": r.get("platform"), "handle": r.get("handle"), "category": r.get("category", "general")}
            for r in rows if r.get("platform") and r.get("handle")]


def load_recent_history(client_id: str, window_days: int) -> list[dict]:
    cutoff = datetime.now(timezone.utc).timestamp() - window_days * 86400
    out = []
    for r in _records("Trend History"):
        if str(r.get("client_id")) != str(client_id):
            continue
        ts = r.get("captured_at")
        try:
            when = datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp()
        except Exception:  # noqa: BLE001
            when = cutoff
        if when >= cutoff:
            out.append(r)
    return out


# --------------------------------------------------------------------------- #
# Appenders (replace n8n Sheets · Append * nodes)
# --------------------------------------------------------------------------- #
def _append(tab: str, rows: list[dict]) -> None:
    if not rows:
        return
    ss = _spreadsheet()
    if not ss:
        log.info("Sheets disabled — would append %d rows to '%s'", len(rows), tab)
        return
    try:
        ws = ss.worksheet(tab)
        header = ws.row_values(1)
        if not header:
            header = list(rows[0].keys())
            ws.update("A1", [header])
        ws.append_rows([[r.get(col, "") for col in header] for r in rows], value_input_option="RAW")
    except Exception as e:  # noqa: BLE001
        log.warning("Sheet append '%s' failed: %s", tab, e)


def write_all(state: dict) -> None:
    _append("Trends", state.get("trend_rows", []))
    _append("Ideas", state.get("idea_rows", []))
    _append("Trend History", state.get("history_rows", []))
    _append("Run Errors", state.get("error_rows", []))


# --------------------------------------------------------------------------- #
# prepare_storage_records — faithful port of the n8n node
# --------------------------------------------------------------------------- #
def prepare_storage_records(
    *,
    run_id: str,
    client: dict,
    trends: list[Trend],
    sentiment: Sentiment,
    ideas: list[Idea],
    posts: list[Post],
    errors: list[dict],
) -> dict[str, list[dict]]:
    client_id = client.get("client_id", "unknown")
    now = datetime.now(timezone.utc).isoformat()
    overall = sentiment.overall_sentiment
    dominant = sentiment.dominant_keywords

    trend_rows = []
    for t in trends:
        trend_id = str(uuid.uuid4())
        trend_rows.append({
            "trend_id": trend_id, "client_id": client_id, "run_id": run_id, "captured_at": now,
            "name": t.name, "description": t.description,
            "platforms_observed": _json(t.platforms_observed),
            "format_type": t.format_type, "format_template_description": t.format_template_description,
            "growth_signal": t.growth_signal, "score": int(t.score),
            "sentiment_positive_pct": overall.positive_pct, "sentiment_neutral_pct": overall.neutral_pct,
            "sentiment_negative_pct": overall.negative_pct, "dominant_keywords": _json(dominant),
            "supporting_post_urls": _json(t.supporting_post_urls), "qdrant_point_id": trend_id,
        })

    idea_rows = []
    for idea in ideas:
        idea_id = str(uuid.uuid4())
        trend = next((t for t in trend_rows if t["name"] == idea.trend_ref), trend_rows[0] if trend_rows else {})
        idea_rows.append({
            "idea_id": idea_id, "trend_id": trend.get("trend_id", ""), "client_id": client_id,
            "run_id": run_id, "created_at": now, "platform": idea.platform, "format": idea.format,
            "structured_brief": idea.structured_brief, "quick_draft_caption": idea.quick_draft.caption,
            "quick_draft_hashtags": _json(idea.quick_draft.hashtags), "visual_brief": idea.visual_brief,
            "cta": idea.cta, "recommended_pipeline": idea.recommended_pipeline,
            "delivery_mode": idea.delivery_mode or "both", "estimated_effort": idea.estimated_effort,
            "status": "new", "qdrant_point_id": idea_id,
        })

    history_rows = [{
        "history_id": str(uuid.uuid4()), "run_id": run_id, "client_id": client_id, "captured_at": now,
        "platform": p.platform, "post_id": p.post_id, "post_url": p.post_url,
        "engagement_total": p.engagement_total, "raw_score": p.engagement_total,
    } for p in posts]

    error_rows = [{
        "error_id": str(uuid.uuid4()), "run_id": run_id, "client_id": client_id, "occurred_at": now,
        "node_name": e.get("node_name", "Unknown"), "error_message": e.get("error_message", ""),
        "payload_snippet": e.get("payload_snippet", ""),
    } for e in errors]

    return {"trend_rows": trend_rows, "idea_rows": idea_rows, "history_rows": history_rows, "error_rows": error_rows}


def _json(v) -> str:
    import json
    return json.dumps(v, ensure_ascii=False)
