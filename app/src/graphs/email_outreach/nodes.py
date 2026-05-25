"""
LangGraph node functions for the email outreach workflow.

Each node receives the full EmailOutreachState and returns a dict of fields to
merge back into state.  Returning {} means "no changes".
"""

import base64
import json
import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import httpx

from app.src.graphs.email_outreach.state import EmailOutreachState
from app.src.services.mongodb import get_db


# ── Private helpers ────────────────────────────────────────────────────────────

async def _refresh_oauth_token(oauth: dict, provider: str) -> dict:
    """Calls the provider's token endpoint and returns the updated oauth dict."""
    is_gmail = provider == "gmail"
    url = (
        "https://oauth2.googleapis.com/token"
        if is_gmail
        else (
            f"https://login.microsoftonline.com/"
            f"{oauth.get('tenant_id', 'common')}/oauth2/v2.0/token"
        )
    )
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            url,
            data={
                "client_id": oauth["client_id"],
                "client_secret": oauth["client_secret"],
                "refresh_token": oauth["refresh_token"],
                "grant_type": "refresh_token",
            },
        )
        resp.raise_for_status()
        tokens = resp.json()

    if "access_token" not in tokens:
        raise RuntimeError(f"Token refresh failed: {tokens}")

    new_expiry = (
        datetime.now(timezone.utc) + timedelta(seconds=tokens["expires_in"])
    ).isoformat()

    return {**oauth, "access_token": tokens["access_token"], "token_expiry": new_expiry}


async def _qdrant_search(collection_name: str, query_text: str, limit: int = 6) -> list[str]:
    """Embeds query_text and returns the top-k text chunks from a Qdrant collection."""
    from langchain_openai import OpenAIEmbeddings
    from app.src.services.qdrant import get_qdrant_client

    embeddings = OpenAIEmbeddings(model="text-embedding-3-small")
    query_vector = await embeddings.aembed_query(query_text)

    qdrant = get_qdrant_client()
    hits = await qdrant.search(
        collection_name=collection_name,
        query_vector=query_vector,
        limit=limit,
        with_payload=True,
    )

    chunks: list[str] = []
    for hit in hits:
        payload = hit.payload or {}
        text = (
            payload.get("text")
            or payload.get("content")
            or payload.get("page_content")
            or payload.get("chunk")
            or ""
        )
        if text:
            chunks.append(str(text))

    return chunks


def _build_gmail_raw(
    from_addr: str,
    to_addr: str,
    subject: str,
    body_html: str,
    body_text: str,
    cc: list[str],
    bcc: list[str],
    reply_to: str,
) -> str:
    """Builds a base64url-encoded RFC 2822 message ready for the Gmail API."""
    msg = MIMEMultipart("alternative")
    msg["From"] = from_addr
    msg["To"] = to_addr
    if cc:
        msg["Cc"] = ", ".join(cc)
    if bcc:
        msg["Bcc"] = ", ".join(bcc)
    if reply_to:
        msg["Reply-To"] = reply_to
    msg["Subject"] = subject

    if body_text:
        msg.attach(MIMEText(body_text, "plain", "utf-8"))
    msg.attach(MIMEText(body_html, "html", "utf-8"))

    return base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")


def _build_graph_payload(
    from_email: str,
    from_name: str,
    to_email: str,
    to_name: str,
    subject: str,
    body_html: str,
    cc: list[str],
    bcc: list[str],
    reply_to: str,
) -> dict:
    """Builds the Microsoft Graph API sendMail JSON payload."""
    return {
        "message": {
            "subject": subject,
            "body": {"contentType": "HTML", "content": body_html},
            "toRecipients": [
                {"emailAddress": {"address": to_email, "name": to_name}}
            ],
            "ccRecipients": [
                {"emailAddress": {"address": a}} for a in cc
            ],
            "bccRecipients": [
                {"emailAddress": {"address": a}} for a in bcc
            ],
            "replyTo": (
                [{"emailAddress": {"address": reply_to}}] if reply_to else []
            ),
            "from": {
                "emailAddress": {"address": from_email, "name": from_name}
            },
        },
        "saveToSentItems": True,
    }


def _inject_pixel(body_html: str, tracking_id: str) -> str:
    """Appends a 1×1 transparent tracking pixel to the HTML email body."""
    base_url = os.getenv("BASE_URL", "http://localhost:8000").rstrip("/")
    pixel = (
        f'<img src="{base_url}/track/open/{tracking_id}" '
        'width="1" height="1" border="0" '
        'style="display:none!important;width:1px!important;height:1px!important;'
        'max-height:1px!important;max-width:1px!important;opacity:0!important" alt="" />'
    )
    if "</body>" in body_html:
        return body_html.replace("</body>", f"{pixel}</body>", 1)
    return body_html + pixel


async def _send_via_gmail(
    access_token: str, raw_message: str
) -> tuple[str | None, str | None]:
    """Returns (message_id, thread_id)."""
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            "https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            },
            json={"raw": raw_message},
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("id"), data.get("threadId")


async def _send_via_outlook(access_token: str, payload: dict) -> None:
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            "https://graph.microsoft.com/v1.0/me/sendMail",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            },
            json=payload,
        )
        resp.raise_for_status()  # 202 Accepted — no body returned


# ── Graph nodes ────────────────────────────────────────────────────────────────

async def validate_input(state: EmailOutreachState) -> dict:
    if not state.get("credential_id"):
        return {"error": "credential_id is required", "error_code": 400}

    if not state.get("recipients"):
        return {"error": "recipients must be a non-empty list", "error_code": 400}

    for r in state["recipients"]:
        if not r.get("email"):
            return {"error": "Each recipient must have an email field", "error_code": 400}

    has_template = bool(state.get("subject") and state.get("body_html"))
    has_campaign = bool(state.get("campaign_description", "").strip())
    has_generated = bool(state.get("generated_emails"))

    if not has_template and not has_campaign and not has_generated:
        return {
            "error": (
                "Provide either: subject+body_html (template mode), "
                "campaign_description (AI generation mode), "
                "or generated_emails (pre-generated mode)"
            ),
            "error_code": 400,
        }

    return {}


async def fetch_credentials(state: EmailOutreachState) -> dict:
    db = get_db()
    cred = await db.project_db_client.find_one(
        {"credential_id": state["credential_id"]}
    )
    if not cred:
        return {"error": "Credential not found", "error_code": 404}
    cred["_id"] = str(cred["_id"])
    return {"credentials": cred}


async def check_and_refresh_token(state: EmailOutreachState) -> dict:
    creds = state["credentials"]
    oauth = creds["oauth"]

    expiry_str = oauth.get("token_expiry", "")
    expiry = datetime.fromisoformat(expiry_str.replace("Z", "+00:00"))
    now = datetime.now(timezone.utc)

    if now < expiry - timedelta(seconds=60):
        return {}  # token still valid — no state change needed

    refreshed_oauth = await _refresh_oauth_token(oauth, creds["provider"])

    db = get_db()
    await db.project_db_client.update_one(
        {"credential_id": creds["credential_id"]},
        {
            "$set": {
                "oauth.access_token": refreshed_oauth["access_token"],
                "oauth.token_expiry": refreshed_oauth["token_expiry"],
            }
        },
    )

    return {"credentials": {**creds, "oauth": refreshed_oauth}}


async def fetch_knowledge_base(state: EmailOutreachState) -> dict:
    """Semantic search in Qdrant using the campaign description as the query.

    knowledge_base_id must be the Qdrant collection name (usually the clientId).
    Returns the top-k relevant text chunks joined as knowledge_base_content.
    """
    kb_id = state.get("knowledge_base_id", "").strip()
    if not kb_id:
        return {"knowledge_base_content": ""}

    campaign_description = state.get("campaign_description") or kb_id

    try:
        chunks = await _qdrant_search(kb_id, campaign_description)
        content = "\n\n".join(chunks)[:4000]
    except Exception as exc:
        # Non-fatal: generation still runs, just without KB context
        content = f"[Knowledge base unavailable: {exc}]"

    return {"knowledge_base_content": content}


async def generate_personalized_emails(state: EmailOutreachState) -> dict:
    """Uses LangChain + GPT-4o-mini to generate a personalized email per recipient.

    Per-recipient Qdrant search (recipient description as query) is layered on top of
    the general campaign KB context so each email gets the most relevant knowledge chunks.
    """
    from langchain_openai import ChatOpenAI
    from langchain_core.prompts import ChatPromptTemplate

    llm = ChatOpenAI(model="gpt-4o-mini", temperature=0.7)

    general_kb = (state.get("knowledge_base_content") or "")[:2000]
    campaign_description = state.get("campaign_description") or ""
    kb_id = state.get("knowledge_base_id", "").strip()

    prompt = ChatPromptTemplate.from_messages([
        ("system", """You are an expert B2B email copywriter.

Campaign objective: {campaign_description}

Relevant knowledge base content:
{kb_content}

Write a short, professional, highly personalized outreach email for the recipient below.
Return ONLY a valid JSON object with exactly these three keys:
- "subject": a compelling subject line (max 60 characters)
- "body_html": full HTML email body using <p> and <strong> tags
- "body_text": plain-text version of the email

No markdown, no code fences, no extra keys — pure JSON only."""),
        ("human", "Name: {name}\nEmail: {email}\nBackground: {description}"),
    ])

    generated: list[dict] = []
    for r in state["recipients"]:
        # Per-recipient Qdrant search for tailored context (best-effort)
        recipient_chunks: list[str] = []
        if kb_id:
            try:
                query = f"{campaign_description} {r.get('description', '')}".strip()
                recipient_chunks = await _qdrant_search(kb_id, query, limit=4)
            except Exception:
                pass

        # Combine per-recipient chunks with general KB context (deduplicated)
        combined_chunks = recipient_chunks or [general_kb] if general_kb else recipient_chunks
        kb_content = "\n\n".join(combined_chunks)[:3000]

        chain = prompt | llm
        response = await chain.ainvoke({
            "campaign_description": campaign_description,
            "kb_content": kb_content,
            "name": r.get("name", ""),
            "email": r.get("email", ""),
            "description": r.get("description") or "No specific details provided.",
        })

        text = response.content.strip()
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)

        try:
            email_data = json.loads(text)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", text, re.DOTALL)
            if not match:
                raise ValueError(
                    f"LLM did not return valid JSON for {r.get('email')}: {text[:200]}"
                )
            email_data = json.loads(match.group())

        generated.append({
            "recipient_email": r["email"],
            "recipient_name": r.get("name", ""),
            "subject": email_data.get("subject", "Hello"),
            "body_html": email_data.get("body_html", ""),
            "body_text": email_data.get("body_text", ""),
        })

    return {"generated_emails": generated}


async def send_email_to_recipient(state: EmailOutreachState) -> dict:
    idx = state["current_index"]
    recipient = state["recipients"][idx]
    creds = state["credentials"]
    oauth = creds["oauth"]

    name = recipient.get("name", "")

    # Use per-recipient generated content if available; fall back to template
    generated = state.get("generated_emails") or []
    if idx < len(generated):
        gen = generated[idx]
        subject = gen["subject"]
        body_html = gen["body_html"]
        body_text = gen.get("body_text", "")
    else:
        subject = (state.get("subject") or "").replace("{{name}}", name)
        body_html = (state.get("body_html") or "").replace("{{name}}", name)
        body_text = (state.get("body_text") or "").replace("{{name}}", name)

    from_name = state.get("from_name") or creds.get("display_name", "")
    from_email = creds["email_address"]
    from_addr = f"{from_name} <{from_email}>" if from_name else from_email
    to_addr = f"{name} <{recipient['email']}>" if name else recipient["email"]

    # Generate a unique tracking ID and inject the open-tracking pixel
    tracking_id = str(uuid.uuid4())
    body_html = _inject_pixel(body_html, tracking_id)

    sent_at = datetime.now(timezone.utc).isoformat()

    try:
        if creds["provider"] == "gmail":
            raw = _build_gmail_raw(
                from_addr,
                to_addr,
                subject,
                body_html,
                body_text,
                state.get("cc") or [],
                state.get("bcc") or [],
                state.get("reply_to") or "",
            )
            message_id, thread_id = await _send_via_gmail(oauth["access_token"], raw)
        else:
            payload = _build_graph_payload(
                from_email,
                from_name,
                recipient["email"],
                name,
                subject,
                body_html,
                state.get("cc") or [],
                state.get("bcc") or [],
                state.get("reply_to") or "",
            )
            await _send_via_outlook(oauth["access_token"], payload)
            message_id = None
            thread_id = None

        # Persist tracking record so opens and replies can be checked later
        db = get_db()
        await db.email_tracking.insert_one({
            "tracking_id":           tracking_id,
            "credential_id":         state["credential_id"],
            "recipient_email":       recipient["email"],
            "recipient_name":        name,
            "subject":               subject,
            "message_id":            message_id,
            "thread_id":             thread_id,
            "sent_at":               sent_at,
            "opened":                False,
            "opened_at":             None,
            "replied":               False,
            "replied_at":            None,
            "follow_up_sent":        False,
            "is_follow_up":          False,
            "calendar_credential_id": state.get("calendar_credential_id") or None,
            "meeting_link":          None,
        })

        # Update lead status → "sent" (only if not already at a higher status)
        await db.leads.update_one(
            {"email": recipient["email"], "status": {"$nin": ["viewed", "replied"]}},
            {"$set": {"status": "sent"}},
        )

        result = {
            "recipient_email": recipient["email"],
            "recipient_name": name,
            "provider": creds["provider"],
            "status": "sent",
            "message_id": message_id,
            "subject": subject,
            "tracking_id": tracking_id,
            "sent_at": sent_at,
        }
    except Exception as exc:
        result = {
            "recipient_email": recipient["email"],
            "recipient_name": name,
            "provider": creds["provider"],
            "status": "failed",
            "error": str(exc),
            "subject": subject,
            "tracking_id": tracking_id,
            "sent_at": sent_at,
        }

    return {"send_results": [result], "current_index": idx + 1}


async def aggregate_results(state: EmailOutreachState) -> dict:
    results = state["send_results"]
    return {
        "summary": {
            "total": len(results),
            "sent": sum(1 for r in results if r["status"] == "sent"),
            "failed": sum(1 for r in results if r["status"] == "failed"),
        }
    }


async def log_activity(state: EmailOutreachState) -> dict:
    db = get_db()
    await db.email_activity_log.insert_one(
        {
            "credential_id": state["credential_id"],
            "client_name": state["credentials"].get("client_name", ""),
            "provider": state["credentials"]["provider"],
            "sent_at": datetime.now(timezone.utc).isoformat(),
            "summary": state["summary"],
            "results": state["send_results"],
        }
    )
    return {}


# ── Routing functions ──────────────────────────────────────────────────────────

def route_after_validate(state: EmailOutreachState) -> str:
    from langgraph.graph import END
    return END if state.get("error") else "fetch_credentials"


def route_after_fetch(state: EmailOutreachState) -> str:
    from langgraph.graph import END
    return END if state.get("error") else "check_token"


def route_content_mode(state: EmailOutreachState) -> str:
    """After token check: decide whether to run KB fetch+LLM or go straight to send."""
    if state.get("generated_emails"):
        return "send_email"   # pre-generated content provided — skip LLM
    if state.get("campaign_description", "").strip():
        return "fetch_kb"     # need AI generation
    return "send_email"       # template mode


def route_recipient_loop(state: EmailOutreachState) -> str:
    if state["current_index"] < len(state["recipients"]):
        return "send_email"   # loop back for next recipient
    return "aggregate"        # all recipients processed


# Preview-specific routing

def route_after_validate_preview(state: EmailOutreachState) -> str:
    from langgraph.graph import END
    return END if state.get("error") else "fetch_credentials"


def route_after_fetch_preview(state: EmailOutreachState) -> str:
    from langgraph.graph import END
    return END if state.get("error") else "fetch_kb"
