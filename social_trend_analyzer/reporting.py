"""Phase 7 reporting — Email (LLM-written) / Notion / Slack, each gated by a
per-client flag exactly like the n8n "* Report Enabled?" IF nodes. All guarded."""
from __future__ import annotations

import logging

import httpx

from .config import get_settings
from .llm import email_chain
from .schemas import Idea, Sentiment, Trend

log = logging.getLogger(__name__)

try:
    from notion_client import Client as NotionClient
except Exception:  # noqa: BLE001
    NotionClient = None  # type: ignore


def _digest_text(trends: list[Trend], top: int = 5) -> str:
    lines = [f"*{t.name}* ({t.score}) — {t.description}" for t in trends[:top]]
    return "Top trends this run:\n" + "\n".join(lines) if lines else "No trends detected this run."


def report(client: dict, trends: list[Trend], ideas: list[Idea], sentiment: Sentiment) -> None:
    settings = get_settings()
    trends_json = [t.model_dump() for t in trends]
    ideas_json = [i.model_dump() for i in ideas]

    # --- Email (Gmail) ---
    if client.get("report_email") and client.get("recipient_email"):
        try:
            html = email_chain().invoke({
                "trends": trends_json, "ideas": ideas_json, "sentiment": sentiment.model_dump(),
            }).content
            _send_gmail(client["recipient_email"], f"Social trends — {client.get('client_name', '')}", html)
        except Exception as e:  # noqa: BLE001
            log.warning("Email report failed: %s", e)

    # --- Notion ---
    if client.get("report_notion") and client.get("notion_database_id") and settings.notion_token and NotionClient:
        try:
            notion = NotionClient(auth=settings.notion_token)
            notion.pages.create(
                parent={"database_id": client["notion_database_id"]},
                properties={"Name": {"title": [{"text": {"content": f"Trends — {client.get('client_name', '')}"}}]}},
                children=[{"object": "block", "type": "paragraph",
                           "paragraph": {"rich_text": [{"text": {"content": _digest_text(trends)[:1900]}}]}}],
            )
        except Exception as e:  # noqa: BLE001
            log.warning("Notion report failed: %s", e)

    # --- Slack (incoming webhook) ---
    if client.get("report_slack") and client.get("slack_webhook_url"):
        try:
            httpx.post(client["slack_webhook_url"], json={"text": _digest_text(trends)}, timeout=20)
        except Exception as e:  # noqa: BLE001
            log.warning("Slack report failed: %s", e)


def _send_gmail(to: str, subject: str, html: str) -> None:
    """Gmail send via the gmail MCP / API would go here. Logged stub so the
    pipeline never hard-fails on a missing mail transport."""
    log.info("EMAIL -> %s | %s (%d chars html)", to, subject, len(html))
