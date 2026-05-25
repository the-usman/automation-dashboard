"""
Follow-up email service.

Called by the scheduler every 2 hours. Finds every email that was opened
(status "viewed") but not yet replied to, where the original send was at
least 1 day ago and no follow-up has been sent yet. Sends one follow-up
per original email and marks it in the tracking record.

Lead status is intentionally left at "viewed" — the follow-up send must
never overwrite a higher-priority status.
"""

import logging
import uuid
from datetime import datetime, timedelta, timezone

from app.src.graphs.email_outreach.nodes import (
    _build_gmail_raw,
    _build_graph_payload,
    _inject_pixel,
    _refresh_oauth_token,
    _send_via_gmail,
    _send_via_outlook,
)
from app.src.services.mongodb import get_db

logger = logging.getLogger(__name__)

FOLLOWUP_DELAY_DAYS = 1


def _build_followup_html(name: str, original_subject: str) -> str:
    display = name or "there"
    return (
        f"<p>Hi {display},</p>"
        f"<p>I wanted to follow up on my previous email about "
        f"<strong>{original_subject}</strong>.</p>"
        f"<p>Did you get a chance to have a look? I'd love to hear your thoughts.</p>"
        f"<p>Best regards</p>"
    )


def _build_followup_text(name: str, original_subject: str) -> str:
    display = name or "there"
    return (
        f"Hi {display},\n\n"
        f"I wanted to follow up on my previous email about \"{original_subject}\".\n\n"
        f"Did you get a chance to have a look? I'd love to hear your thoughts.\n\n"
        f"Best regards"
    )


async def send_followup_emails() -> None:
    """Send follow-up emails to leads who viewed but haven't replied."""
    db = get_db()

    cutoff = (datetime.now(timezone.utc) - timedelta(days=FOLLOWUP_DELAY_DAYS)).isoformat()

    # Records that are:
    #   - opened (lead viewed the email)
    #   - not yet replied
    #   - not already sent a follow-up
    #   - not themselves a follow-up (avoid chains)
    #   - original email sent at least 1 day ago
    pending = await db.email_tracking.find({
        "opened": True,
        "replied": False,
        "follow_up_sent": {"$ne": True},
        "is_follow_up": {"$ne": True},
        "sent_at": {"$lte": cutoff},
    }).to_list(length=500)

    if not pending:
        logger.debug("Follow-up check: no records due for follow-up")
        return

    logger.info("Follow-up check: %d record(s) due", len(pending))

    for record in pending:
        try:
            cred = await db.project_db_client.find_one(
                {"credential_id": record["credential_id"]}
            )
            if not cred:
                continue

            # Refresh token if needed
            oauth = cred["oauth"]
            expiry = datetime.fromisoformat(
                oauth.get("token_expiry", "").replace("Z", "+00:00")
            )
            if datetime.now(timezone.utc) >= expiry - timedelta(seconds=60):
                oauth = await _refresh_oauth_token(oauth, cred["provider"])
                await db.project_db_client.update_one(
                    {"credential_id": record["credential_id"]},
                    {"$set": {
                        "oauth.access_token": oauth["access_token"],
                        "oauth.token_expiry": oauth["token_expiry"],
                    }},
                )

            recipient_email = record["recipient_email"]
            recipient_name  = record.get("recipient_name", "")
            original_subject = record.get("subject", "")

            followup_subject = (
                f"Re: {original_subject}"
                if not original_subject.lower().startswith("re:")
                else original_subject
            )
            followup_tracking_id = str(uuid.uuid4())
            body_html = _inject_pixel(
                _build_followup_html(recipient_name, original_subject),
                followup_tracking_id,
            )
            body_text = _build_followup_text(recipient_name, original_subject)

            from_name  = cred.get("display_name", "")
            from_email = cred["email_address"]
            from_addr  = f"{from_name} <{from_email}>" if from_name else from_email
            to_addr    = (
                f"{recipient_name} <{recipient_email}>"
                if recipient_name else recipient_email
            )
            sent_at = datetime.now(timezone.utc).isoformat()

            if cred["provider"] == "gmail":
                raw = _build_gmail_raw(
                    from_addr, to_addr,
                    followup_subject, body_html, body_text,
                    [], [], "",
                )
                message_id, thread_id = await _send_via_gmail(oauth["access_token"], raw)
            else:
                payload = _build_graph_payload(
                    from_email, from_name,
                    recipient_email, recipient_name,
                    followup_subject, body_html,
                    [], [], "",
                )
                await _send_via_outlook(oauth["access_token"], payload)
                message_id, thread_id = None, None

            # Create a tracking record for the follow-up email itself
            await db.email_tracking.insert_one({
                "tracking_id":    followup_tracking_id,
                "credential_id":  record["credential_id"],
                "recipient_email": recipient_email,
                "recipient_name":  recipient_name,
                "subject":         followup_subject,
                "message_id":      message_id,
                "thread_id":       thread_id,
                "sent_at":         sent_at,
                "opened":          False,
                "opened_at":       None,
                "replied":         False,
                "replied_at":      None,
                "follow_up_sent":  False,
                "is_follow_up":    True,   # marks this as a follow-up, not an original
            })

            # Mark the original record so we don't send another follow-up
            await db.email_tracking.update_one(
                {"tracking_id": record["tracking_id"]},
                {"$set": {"follow_up_sent": True, "follow_up_sent_at": sent_at}},
            )

            # NOTE: we intentionally do NOT touch the leads collection here.
            # Sending a follow-up is our action, not the lead's — so the lead
            # status stays exactly as it was (e.g. "viewed"). Only the reply
            # checker is allowed to advance the status to "replied".

            logger.info(
                "Follow-up sent to %s (original tracking_id: %s)",
                recipient_email, record["tracking_id"],
            )

        except Exception:
            logger.exception(
                "Failed to send follow-up for tracking_id %s", record.get("tracking_id")
            )
