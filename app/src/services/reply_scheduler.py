"""
Background scheduler — runs every 2 hours and does two things:
  1. Checks for replies to emails we sent and updates email_tracking + leads.
  2. Sends follow-up emails to leads that viewed an email 1+ day ago but
     haven't replied yet.
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from app.src.graphs.email_outreach.nodes import _refresh_oauth_token
from app.src.routes.tracking import _check_gmail_replies, _check_outlook_replies
from app.src.services.followup_service import send_followup_emails
from app.src.services.mongodb import get_db

logger = logging.getLogger(__name__)

INTERVAL_SECONDS = 2 * 60 * 60   # 2 hours
LOOKBACK_DAYS    = 30


async def _check_replies_once() -> None:
    """Check replies for every credential that has pending tracking records."""
    db = get_db()
    since = (datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)).isoformat()

    credential_ids: list[str] = await db.email_tracking.distinct(
        "credential_id",
        {"replied": False, "sent_at": {"$gte": since}},
    )

    if not credential_ids:
        logger.debug("Scheduled reply check: nothing to check")
        return

    logger.info("Scheduled reply check: %d credential(s) to check", len(credential_ids))

    for credential_id in credential_ids:
        try:
            cred = await db.project_db_client.find_one({"credential_id": credential_id})
            if not cred:
                continue

            oauth = cred["oauth"]
            expiry = datetime.fromisoformat(
                oauth.get("token_expiry", "").replace("Z", "+00:00")
            )
            if datetime.now(timezone.utc) >= expiry - timedelta(seconds=60):
                oauth = await _refresh_oauth_token(oauth, cred["provider"])
                await db.project_db_client.update_one(
                    {"credential_id": credential_id},
                    {"$set": {
                        "oauth.access_token": oauth["access_token"],
                        "oauth.token_expiry": oauth["token_expiry"],
                    }},
                )

            records = await db.email_tracking.find(
                {"credential_id": credential_id, "replied": False, "sent_at": {"$gte": since}},
            ).to_list(length=500)

            if cred["provider"] == "gmail":
                found = await _check_gmail_replies(oauth["access_token"], records, db)
            else:
                found = await _check_outlook_replies(oauth["access_token"], records, db)

            if found:
                logger.info(
                    "Credential %s: %d new reply/replies detected", credential_id, len(found)
                )

        except Exception:
            logger.exception("Reply check failed for credential %s", credential_id)


async def reply_check_loop() -> None:
    """Infinite loop: wait 2 hours, then run reply check + follow-ups, repeat."""
    logger.info("Scheduler started (interval: %d s)", INTERVAL_SECONDS)
    while True:
        await asyncio.sleep(INTERVAL_SECONDS)
        await _check_replies_once()
        await send_followup_emails()
