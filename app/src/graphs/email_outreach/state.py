import operator
from typing import Annotated, TypedDict


class EmailOutreachState(TypedDict):
    # ── Request payload ────────────────────────────────────────────────────────
    credential_id: str
    recipients: list[dict]      # [{"email": str, "name": str, "description": str}]

    # Template mode (subject + body apply to all recipients with {{name}} substitution)
    subject: str
    body_html: str
    body_text: str

    # AI generation mode
    campaign_description: str   # describes what the campaign is about
    knowledge_base_id: str      # MongoDB _id of a knowledge_documents record

    # Shared send options
    from_name: str
    reply_to: str
    cc: list[str]
    bcc: list[str]

    # ── Runtime ────────────────────────────────────────────────────────────────
    credentials: dict | None            # document fetched from MongoDB project_db_client
    knowledge_base_content: str | None  # extracted text from the KB document
    generated_emails: list[dict] | None # per-recipient [{subject, body_html, body_text, ...}]
    current_index: int                  # which recipient we are currently sending to

    # Reducer: each iteration appends its [result_dict] → accumulated list
    send_results: Annotated[list[dict], operator.add]

    # Optional: if set, a calendar event is auto-created when the lead replies
    calendar_credential_id: str

    # ── Final output ───────────────────────────────────────────────────────────
    summary: dict | None
    error: str | None
    error_code: int | None
