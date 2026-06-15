import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException

from app.src.graphs.email_outreach import email_outreach_graph, email_preview_graph
from app.src.models.email_models import (
    CampaignCreateRequest,
    CampaignCreateResponse,
    CampaignStatusResponse,
    EmailOutreachRequest,
    EmailOutreachResponse,
    EmailPreviewRequest,
    EmailPreviewResponse,
    GenerateAndSendRequest,
    GeneratedEmail,
    OutreachSummary,
    SendResult,
)
from app.src.services.mongodb import get_db

router = APIRouter(prefix="/outreach", tags=["Email Outreach"])


def _base_state() -> dict:
    """Returns zeroed-out runtime fields required by the state schema."""
    return {
        "credentials": None,
        "knowledge_base_content": None,
        "generated_emails": None,
        "current_index": 0,
        "send_results": [],
        "summary": None,
        "error": None,
        "error_code": None,
        # Optional content fields — defaults
        "subject": "",
        "body_html": "",
        "body_text": "",
        "campaign_description": "",
        "knowledge_base_id": "",
        "from_name": "",
        "reply_to": "",
        "cc": [],
        "bcc": [],
        "calendar_credential_id": "",
    }


@router.post("/preview", response_model=EmailPreviewResponse)
async def preview_outreach(request: EmailPreviewRequest) -> EmailPreviewResponse:
    """
    Generate AI-personalized emails without sending them.

    Workflow:
    1. Validates the credential exists in MongoDB
    2. Fetches the knowledge base document (if knowledge_base_id is provided)
    3. Uses GPT-4o-mini to write a personalized email per recipient based on
       campaign description + KB content + recipient description

    Returns the draft emails for review. Pass them back to POST /outreach/send
    via the `generated_emails` field to send after approval.
    """
    initial_state = {
        **_base_state(),
        "credential_id": request.credential_id,
        "recipients": [r.model_dump() for r in request.recipients],
        "campaign_description": request.campaign_description,
        "knowledge_base_id": request.knowledge_base_id,
    }

    result = await email_preview_graph.ainvoke(initial_state)

    if result.get("error"):
        raise HTTPException(
            status_code=result.get("error_code") or 500,
            detail=result["error"],
        )

    emails = [GeneratedEmail(**e) for e in (result.get("generated_emails") or [])]
    return EmailPreviewResponse(emails=emails)


@router.post("/send", response_model=EmailOutreachResponse)
async def send_outreach(request: EmailOutreachRequest) -> EmailOutreachResponse:
    """
    Send outreach emails on behalf of a client account.

    Three content modes (mutually exclusive, in priority order):
    - **Pre-generated** (`generated_emails`): pass emails from POST /outreach/preview
    - **AI generation** (`campaign_description`): generates per-recipient emails on the fly
    - **Template** (`subject` + `body_html`): same content sent to all recipients ({{name}} replaced)

    In all cases the workflow:
    1. Fetches and decrypts OAuth2 credentials from MongoDB by credential_id
    2. Auto-refreshes the access token if within 60s of expiry
    3. Sends via Gmail API or Microsoft Graph API
    4. Logs results to the email_activity_log collection
    """
    initial_state = {
        **_base_state(),
        "credential_id": request.credential_id,
        "recipients": [r.model_dump() for r in request.recipients],
        "subject": request.subject,
        "body_html": request.body_html,
        "body_text": request.body_text,
        "campaign_description": request.campaign_description,
        "knowledge_base_id": request.knowledge_base_id,
        "generated_emails": [e.model_dump() for e in request.generated_emails] or None,
        "from_name": request.from_name,
        "reply_to": request.reply_to,
        "cc": request.cc,
        "bcc": request.bcc,
        "calendar_credential_id": request.calendar_credential_id,
    }

    result = await email_outreach_graph.ainvoke(initial_state)

    if result.get("error"):
        raise HTTPException(
            status_code=result.get("error_code") or 500,
            detail=result["error"],
        )

    return EmailOutreachResponse(
        success=True,
        summary=OutreachSummary(**result["summary"]),
        results=[SendResult(**r) for r in result["send_results"]],
    )


@router.post("/generate-and-send", response_model=EmailOutreachResponse)
async def generate_and_send(request: GenerateAndSendRequest) -> EmailOutreachResponse:
    """
    Generate AI-personalized emails and send them immediately — no preview step.

    Workflow:
    1. Validates credential and recipients
    2. Fetches and decrypts OAuth2 credentials from MongoDB
    3. Auto-refreshes access token if within 60s of expiry
    4. Fetches relevant knowledge base chunks from Qdrant (if knowledge_base_id provided)
    5. Uses GPT-4o-mini to write a personalized email per recipient
    6. Sends each email via Gmail or Outlook
    7. Logs results to email_activity_log
    """
    initial_state = {
        **_base_state(),
        "credential_id": request.credential_id,
        "recipients": [r.model_dump() for r in request.recipients],
        "campaign_description": request.campaign_description,
        "knowledge_base_id": request.knowledge_base_id,
        "from_name": request.from_name,
        "reply_to": request.reply_to,
        "cc": request.cc,
        "bcc": request.bcc,
        "calendar_credential_id": request.calendar_credential_id,
    }

    result = await email_outreach_graph.ainvoke(initial_state)

    if result.get("error"):
        raise HTTPException(
            status_code=result.get("error_code") or 500,
            detail=result["error"],
        )

    return EmailOutreachResponse(
        success=True,
        summary=OutreachSummary(**result["summary"]),
        results=[SendResult(**r) for r in result["send_results"]],
    )


# ── Background campaign endpoints ─────────────────────────────────────────────

@router.post("/campaigns", response_model=CampaignCreateResponse, status_code=202)
async def create_campaign(request: CampaignCreateRequest) -> CampaignCreateResponse:
    """
    Launch a background email campaign. Returns immediately with a campaign_id.

    The campaign runs in the background — AI generation and sending happen
    per-recipient (generate one → send it → next). Poll GET /outreach/campaigns/{id}
    to track progress. Stop via POST /outreach/campaigns/{id}/stop.
    """
    from app.src.services.campaign_runner import launch_campaign

    campaign_id = str(uuid.uuid4())
    recipients = [r.model_dump() for r in request.recipients]
    started_at = datetime.now(timezone.utc).isoformat()

    db = get_db()
    await db.email_campaigns.insert_one({
        "campaign_id": campaign_id,
        "credential_id": request.credential_id,
        "status": "running",
        "total": len(recipients),
        "sent": 0,
        "failed": 0,
        "current_index": 0,
        "results": [],
        "started_at": started_at,
        "completed_at": None,
    })

    launch_campaign(
        campaign_id=campaign_id,
        credential_id=request.credential_id,
        campaign_description=request.campaign_description,
        recipients=recipients,
        kb_id=request.knowledge_base_id,
        from_name=request.from_name,
        reply_to=request.reply_to,
        cc=request.cc,
        bcc=request.bcc,
        calendar_credential_id=request.calendar_credential_id,
    )

    return CampaignCreateResponse(
        campaign_id=campaign_id,
        status="running",
        total=len(recipients),
    )


@router.get("/campaigns/{campaign_id}", response_model=CampaignStatusResponse)
async def get_campaign_status(campaign_id: str) -> CampaignStatusResponse:
    """Poll the progress of a running or finished campaign."""
    db = get_db()
    doc = await db.email_campaigns.find_one({"campaign_id": campaign_id}, {"_id": 0})
    if not doc:
        raise HTTPException(status_code=404, detail=f"Campaign {campaign_id!r} not found")
    return CampaignStatusResponse(
        campaign_id=doc["campaign_id"],
        status=doc["status"],
        total=doc["total"],
        sent=doc["sent"],
        failed=doc["failed"],
        current_index=doc["current_index"],
        results=[SendResult(**r) for r in doc.get("results", [])],
        started_at=doc["started_at"],
        completed_at=doc.get("completed_at"),
    )


@router.post("/campaigns/{campaign_id}/stop")
async def stop_campaign(campaign_id: str):
    """
    Send a stop signal to a running campaign.

    The campaign stops after the current recipient finishes (no mid-send interruption).
    Poll GET /outreach/campaigns/{id} until status = "stopped" to confirm.
    """
    from app.src.services.campaign_runner import stop_campaign as _stop_campaign

    db = get_db()
    doc = await db.email_campaigns.find_one({"campaign_id": campaign_id}, {"_id": 0, "status": 1})
    if not doc:
        raise HTTPException(status_code=404, detail=f"Campaign {campaign_id!r} not found")

    if doc["status"] != "running":
        return {"campaign_id": campaign_id, "status": doc["status"], "message": "Campaign is not running"}

    stopped = _stop_campaign(campaign_id)
    if not stopped:
        # Task finished naturally between the DB check and the stop call
        updated = await db.email_campaigns.find_one({"campaign_id": campaign_id}, {"_id": 0, "status": 1})
        return {
            "campaign_id": campaign_id,
            "status": (updated or {}).get("status", "completed"),
            "message": "Campaign already finished",
        }

    return {
        "campaign_id": campaign_id,
        "status": "stopping",
        "message": "Stop signal sent. Poll GET /outreach/campaigns/{id} to confirm.",
    }
