"""
Email tracking routes:
  GET  /track/open/{tracking_id}   — tracking pixel endpoint (marks email opened)
  POST /track/check-replies        — polls inbox for replies to tracked emails
  GET  /track/status/{credential_id} — returns open/reply status for all tracked emails
"""

import base64
import logging
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from app.src.models.calendar_models import CreateEventRequest, EventAttendee
from app.src.models.tracking_models import (
    ReplyCheckRequest,
    ReplyCheckResponse,
    ReplyInfo,
    TrackingRecord,
    TrackingStatusResponse,
)
from app.src.services.mongodb import get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/track", tags=["Email Tracking"])

# 1×1 transparent GIF (GIF89a)
_PIXEL_GIF = base64.b64decode("R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7")


@router.get("/open/{tracking_id}", include_in_schema=False)
async def track_open(tracking_id: str):
    """Called by email clients when the tracking pixel loads. Marks the email as opened."""
    db = get_db()
    now = datetime.now(timezone.utc).isoformat()

    # Fetch first so we have the recipient email; only process if not already opened
    record = await db.email_tracking.find_one(
        {"tracking_id": tracking_id, "opened": False},
        {"recipient_email": 1},
    )
    if record:
        await db.email_tracking.update_one(
            {"tracking_id": tracking_id},
            {"$set": {"opened": True, "opened_at": now}},
        )
        # Update lead status → "viewed" (never downgrade from "replied")
        await db.leads.update_one(
            {"email": record["recipient_email"], "status": {"$ne": "replied"}},
            {"$set": {"status": "viewed"}},
        )

    return Response(
        content=_PIXEL_GIF,
        media_type="image/gif",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


@router.post("/check-replies", response_model=ReplyCheckResponse)
async def check_replies(request: ReplyCheckRequest):
    """
    Polls the inbox for replies to emails sent in the last N days.
    Updates email_tracking records where a reply is found.
    """
    db = get_db()

    cred = await db.project_db_client.find_one({"credential_id": request.credential_id})
    if not cred:
        raise HTTPException(status_code=404, detail="Credential not found")

    access_token = cred["oauth"]["access_token"]
    provider = cred["provider"]

    since = (datetime.now(timezone.utc) - timedelta(days=request.days)).isoformat()
    cursor = db.email_tracking.find({
        "credential_id": request.credential_id,
        "replied": False,
        "sent_at": {"$gte": since},
    })
    records = await cursor.to_list(length=500)

    if provider == "gmail":
        new_replies = await _check_gmail_replies(access_token, records, db)
    else:
        new_replies = await _check_outlook_replies(access_token, records, db)

    return ReplyCheckResponse(
        credential_id=request.credential_id,
        new_replies=new_replies,
        total_checked=len(records),
    )


@router.get("/status/{credential_id}", response_model=TrackingStatusResponse)
async def tracking_status(credential_id: str, days: int = 30):
    """Returns open and reply status for all emails sent from this credential."""
    db = get_db()

    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    cursor = db.email_tracking.find(
        {"credential_id": credential_id, "sent_at": {"$gte": since}},
        {"_id": 0},
    )
    docs = await cursor.to_list(length=1000)

    records = [TrackingRecord(**d) for d in docs]
    return TrackingStatusResponse(
        credential_id=credential_id,
        records=records,
        total=len(records),
        opened=sum(1 for r in records if r.opened),
        replied=sum(1 for r in records if r.replied),
    )


# ── Provider-specific reply checkers ──────────────────────────────────────────

async def _check_gmail_replies(
    access_token: str, records: list[dict], db
) -> list[ReplyInfo]:
    """
    Checks the exact thread we sent for each tracked Gmail email.
    Only emails we sent have a thread_id stored, so this is already scoped
    to our outgoing emails — no cross-contamination with unrelated inbox mail.
    """
    replies: list[ReplyInfo] = []

    async with httpx.AsyncClient(timeout=30) as client:
        for record in records:
            thread_id = record.get("thread_id")
            if not thread_id:
                # No thread_id means we couldn't record it at send time; skip.
                continue

            resp = await client.get(
                f"https://gmail.googleapis.com/gmail/v1/users/me/threads/{thread_id}",
                params={
                    "format": "metadata",
                    "metadataHeaders": ["From", "Subject", "Date"],
                },
                headers={"Authorization": f"Bearer {access_token}"},
            )
            if resp.status_code != 200:
                continue

            messages = resp.json().get("messages", [])
            if len(messages) <= 1:
                continue

            # Find the first message in the thread whose From is the recipient.
            # This correctly ignores our own follow-up emails in the same thread.
            recipient_email_lc = record["recipient_email"].lower()
            reply_msg = None
            for msg in messages:
                hdrs_map = {
                    h["name"]: h["value"]
                    for h in msg.get("payload", {}).get("headers", [])
                }
                if recipient_email_lc in hdrs_map.get("From", "").lower():
                    reply_msg = msg
                    hdrs = hdrs_map
                    break

            if reply_msg is None:
                continue  # no message from recipient — thread only has our own messages

            reply_subject = hdrs.get("Subject", record.get("subject", ""))
            reply_preview = reply_msg.get("snippet", "")
            now = datetime.now(timezone.utc).isoformat()

            await db.email_tracking.update_one(
                {"tracking_id": record["tracking_id"]},
                {"$set": {
                    "replied": True,
                    "replied_at": now,
                    "reply_subject": reply_subject,
                    "reply_preview": reply_preview,
                }},
            )
            # Update lead status → "replied" (highest priority, always set)
            await db.leads.update_one(
                {"email": record["recipient_email"]},
                {"$set": {"status": "replied"}},
            )

            meeting_link = await _maybe_create_meeting(record, db)

            replies.append(ReplyInfo(
                tracking_id=record["tracking_id"],
                recipient_email=record["recipient_email"],
                reply_subject=reply_subject,
                reply_preview=reply_preview,
                replied_at=now,
                meeting_link=meeting_link,
            ))

    return replies


async def _check_outlook_replies(
    access_token: str, records: list[dict], db
) -> list[ReplyInfo]:
    """
    For Outlook, finds the conversationId of the email we sent (via sentItems),
    then checks whether the recipient replied inside that same conversation.
    This scopes the reply check strictly to our outgoing email thread.
    """
    replies: list[ReplyInfo] = []

    async with httpx.AsyncClient(timeout=30) as client:
        for record in records:
            recipient_email = record["recipient_email"]
            subject = record.get("subject", "")

            # Step 1 — find the email we sent in sentItems to get its conversationId.
            # Escape single quotes for OData string literals.
            safe_subject = subject.replace("'", "''")
            safe_recipient = recipient_email.replace("'", "''")

            resp_sent = await client.get(
                "https://graph.microsoft.com/v1.0/me/sentItems",
                params={
                    "$filter": (
                        f"subject eq '{safe_subject}'"
                        f" and toRecipients/any(r: r/emailAddress/address eq '{safe_recipient}')"
                    ),
                    "$select": "id,conversationId",
                    "$top": "1",
                    "$orderby": "sentDateTime desc",
                },
                headers={"Authorization": f"Bearer {access_token}"},
            )
            if resp_sent.status_code != 200:
                continue

            sent_items = resp_sent.json().get("value", [])
            if not sent_items:
                continue  # our sent email not found (might not be indexed yet)

            conversation_id = sent_items[0].get("conversationId")
            if not conversation_id:
                continue

            # Step 2 — look for a reply from the recipient in that same conversation.
            # Any message in the conversation whose sender is the recipient is a reply.
            safe_conv = conversation_id.replace("'", "''")
            resp_reply = await client.get(
                "https://graph.microsoft.com/v1.0/me/messages",
                params={
                    "$filter": (
                        f"conversationId eq '{safe_conv}'"
                        f" and from/emailAddress/address eq '{safe_recipient}'"
                    ),
                    "$select": "id,subject,from,bodyPreview,receivedDateTime",
                    "$top": "1",
                    "$orderby": "receivedDateTime asc",
                },
                headers={"Authorization": f"Bearer {access_token}"},
            )
            if resp_reply.status_code != 200:
                continue

            reply_msgs = resp_reply.json().get("value", [])
            if not reply_msgs:
                continue  # no reply yet

            reply = reply_msgs[0]
            reply_subject = reply.get("subject", "")
            reply_preview = reply.get("bodyPreview", "")
            now = datetime.now(timezone.utc).isoformat()

            await db.email_tracking.update_one(
                {"tracking_id": record["tracking_id"]},
                {"$set": {
                    "replied": True,
                    "replied_at": now,
                    "reply_subject": reply_subject,
                    "reply_preview": reply_preview,
                }},
            )
            # Update lead status → "replied" (highest priority, always set)
            await db.leads.update_one(
                {"email": recipient_email},
                {"$set": {"status": "replied"}},
            )

            meeting_link = await _maybe_create_meeting(record, db)

            replies.append(ReplyInfo(
                tracking_id=record["tracking_id"],
                recipient_email=recipient_email,
                reply_subject=reply_subject,
                reply_preview=reply_preview,
                replied_at=now,
                meeting_link=meeting_link,
            ))

    return replies


# ── Calendar auto-create ──────────────────────────────────────────────────────

async def _maybe_create_meeting(record: dict, db) -> str | None:
    """
    If a calendar_credential_id is stored on the tracking record, create a
    30-minute meeting event and store the meeting link back on the record.
    Returns the meeting link or None.
    """
    cal_cred_id = record.get("calendar_credential_id")
    if not cal_cred_id:
        return None

    from app.src.services import calendar_service
    from datetime import timedelta

    try:
        now = datetime.now(timezone.utc)
        # Default: schedule 1 day from now, 30 minutes
        start = (now + timedelta(days=1)).replace(minute=0, second=0, microsecond=0)
        end   = start + timedelta(minutes=30)

        event = await calendar_service.create_event(
            credential_id=cal_cred_id,
            title=f"Meeting with {record.get('recipient_name') or record['recipient_email']}",
            description=f"Follow-up meeting regarding: {record.get('subject', '')}",
            start_datetime=start.isoformat(),
            end_datetime=end.isoformat(),
            timezone_str="UTC",
            attendees=[
                EventAttendee(
                    email=record["recipient_email"],
                    name=record.get("recipient_name", ""),
                )
            ],
            add_meeting_link=True,
        )

        meeting_link = event.meeting_link or event.calendar_link
        await db.email_tracking.update_one(
            {"tracking_id": record["tracking_id"]},
            {"$set": {"meeting_link": meeting_link}},
        )
        return meeting_link

    except Exception:
        logger.exception(
            "Failed to auto-create calendar event for tracking_id %s",
            record.get("tracking_id"),
        )
        return None
