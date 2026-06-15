"""
Background campaign runner.

Each campaign runs as an asyncio.Task. The client gets an immediate 202 response
with a campaign_id and polls GET /outreach/campaigns/{id} for progress.
MongoDB (email_campaigns collection) is the single source of truth for progress.
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from app.src.services.mongodb import get_db

logger = logging.getLogger(__name__)

_tasks: dict[str, asyncio.Task] = {}
_stop_events: dict[str, asyncio.Event] = {}


def launch_campaign(
    campaign_id: str,
    credential_id: str,
    campaign_description: str,
    recipients: list[dict],
    kb_id: str,
    from_name: str,
    reply_to: str,
    cc: list[str],
    bcc: list[str],
    calendar_credential_id: str,
) -> None:
    stop_event = asyncio.Event()
    _stop_events[campaign_id] = stop_event
    task = asyncio.create_task(
        _run_campaign(
            campaign_id=campaign_id,
            credential_id=credential_id,
            campaign_description=campaign_description,
            recipients=recipients,
            kb_id=kb_id,
            from_name=from_name,
            reply_to=reply_to,
            cc=cc,
            bcc=bcc,
            calendar_credential_id=calendar_credential_id,
            stop_event=stop_event,
        )
    )
    _tasks[campaign_id] = task
    task.add_done_callback(lambda _: (_tasks.pop(campaign_id, None), _stop_events.pop(campaign_id, None)))


def stop_campaign(campaign_id: str) -> bool:
    """Signal a running campaign to stop. Returns False if already finished or unknown."""
    stop_event = _stop_events.get(campaign_id)
    task = _tasks.get(campaign_id)
    if not stop_event or not task:
        return False
    stop_event.set()
    task.cancel()
    return True


async def _run_campaign(
    campaign_id: str,
    credential_id: str,
    campaign_description: str,
    recipients: list[dict],
    kb_id: str,
    from_name: str,
    reply_to: str,
    cc: list[str],
    bcc: list[str],
    calendar_credential_id: str,
    stop_event: asyncio.Event,
) -> None:
    # Imports here to avoid circular imports at module load time
    from app.src.graphs.email_outreach.nodes import (
        _refresh_oauth_token,
        fetch_knowledge_base,
        generate_one_email,
        send_email_to_recipient,
    )

    db = get_db()

    async def _mark_failed(msg: str) -> None:
        await db.email_campaigns.update_one(
            {"campaign_id": campaign_id},
            {"$set": {
                "status": "failed",
                "completed_at": datetime.now(timezone.utc).isoformat(),
                "error": msg,
            }},
        )

    # 1. Fetch credential
    cred = await db.project_db_client.find_one({"credential_id": credential_id})
    if not cred:
        await _mark_failed(f"Credential {credential_id!r} not found")
        return
    cred["_id"] = str(cred["_id"])

    # 2. Initial token refresh if near/past expiry
    cred = await _maybe_refresh(cred, db, credential_id, _refresh_oauth_token)

    # 3. Fetch knowledge base once before the loop
    kb_result = await fetch_knowledge_base({
        "knowledge_base_id": kb_id,
        "campaign_description": campaign_description,
    })
    general_kb = (kb_result.get("knowledge_base_content") or "")[:2000]

    # 4. Per-recipient loop: generate → send → record
    for recipient in recipients:
        if stop_event.is_set():
            await db.email_campaigns.update_one(
                {"campaign_id": campaign_id},
                {"$set": {"status": "stopped", "completed_at": datetime.now(timezone.utc).isoformat()}},
            )
            return

        try:
            # Re-check token expiry before each send
            cred = await _maybe_refresh(cred, db, credential_id, _refresh_oauth_token)

            gen = await generate_one_email(recipient, campaign_description, kb_id, general_kb)

            send_state = {
                "credential_id": credential_id,
                "credentials": cred,
                "recipients": [recipient],
                "generated_emails": [gen],
                "current_index": 0,
                "from_name": from_name,
                "reply_to": reply_to,
                "cc": cc,
                "bcc": bcc,
                "calendar_credential_id": calendar_credential_id,
                "subject": "",
                "body_html": "",
                "body_text": "",
                "send_results": [],
                "summary": None,
                "error": None,
                "error_code": None,
                "knowledge_base_content": general_kb,
                "knowledge_base_id": kb_id,
                "campaign_description": campaign_description,
            }
            send_result_state = await send_email_to_recipient(send_state)
            result = send_result_state["send_results"][0]

            await db.email_campaigns.update_one(
                {"campaign_id": campaign_id},
                {
                    "$push": {"results": result},
                    "$inc": {
                        "sent": 1 if result["status"] == "sent" else 0,
                        "failed": 1 if result["status"] == "failed" else 0,
                        "current_index": 1,
                    },
                },
            )

        except asyncio.CancelledError:
            await db.email_campaigns.update_one(
                {"campaign_id": campaign_id},
                {"$set": {"status": "stopped", "completed_at": datetime.now(timezone.utc).isoformat()}},
            )
            raise
        except Exception as exc:
            logger.exception(
                "Campaign %s: error sending to %s", campaign_id, recipient.get("email")
            )
            failed_result = {
                "recipient_email": recipient.get("email", ""),
                "recipient_name": recipient.get("name", ""),
                "provider": cred.get("provider", ""),
                "status": "failed",
                "error": str(exc),
                "subject": "",
                "tracking_id": None,
                "sent_at": datetime.now(timezone.utc).isoformat(),
            }
            await db.email_campaigns.update_one(
                {"campaign_id": campaign_id},
                {
                    "$push": {"results": failed_result},
                    "$inc": {"failed": 1, "current_index": 1},
                },
            )

    # 5. All recipients processed
    await db.email_campaigns.update_one(
        {"campaign_id": campaign_id},
        {"$set": {"status": "completed", "completed_at": datetime.now(timezone.utc).isoformat()}},
    )


async def _maybe_refresh(cred: dict, db, credential_id: str, _refresh_oauth_token) -> dict:
    """Refresh the OAuth token if within 60s of expiry. Returns updated cred dict."""
    try:
        oauth = cred["oauth"]
        expiry_str = oauth.get("token_expiry", "")
        if not expiry_str:
            return cred
        expiry = datetime.fromisoformat(expiry_str.replace("Z", "+00:00"))
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) < expiry - timedelta(seconds=60):
            return cred
        oauth = await _refresh_oauth_token(oauth, cred["provider"])
        await db.project_db_client.update_one(
            {"credential_id": credential_id},
            {"$set": {
                "oauth.access_token": oauth["access_token"],
                "oauth.token_expiry": oauth["token_expiry"],
            }},
        )
        return {**cred, "oauth": oauth}
    except Exception as exc:
        logger.warning("Token refresh failed for credential %s: %s", credential_id, exc)
        return cred
