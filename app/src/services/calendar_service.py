"""
Calendar service — Google Calendar API and Microsoft Graph Calendar API.

Credentials are read from the dashboard's `credentials` MongoDB collection
(looked up by accountId).  A fallback lookup against `calendar_db_client`
(keyed by credential_id) is kept for backward compatibility.

apiKey values encrypted as  enc:v1:<base64url>  are decrypted with the
32-byte hex key in CREDENTIAL_ENCRYPTION_KEY.  The expected Node.js format is:
    base64url( iv[12] + authTag[16] + ciphertext )
using AES-256-GCM.
"""

import base64
import json
import logging
import os
from datetime import datetime, timedelta, timezone

import httpx
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.src.models.calendar_models import (
    AttendeeRSVP,
    AvailabilitySlot,
    BookingResponse,
    CalendarEvent,
    EventAttendee,
    RSVPResponse,
    SuggestedSlot,
)
from app.src.services.mongodb import get_db

logger = logging.getLogger(__name__)

_GOOGLE_BASE = "https://www.googleapis.com/calendar/v3"
_GRAPH_BASE  = "https://graph.microsoft.com/v1.0"


# ── Decryption ────────────────────────────────────────────────────────────────

def _decrypt_api_key(encrypted: str) -> dict:
    """
    Decrypt an enc:v1:<base64url> value produced by the dashboard.
    Tries the two most common Node.js AES-256-GCM layouts:
      Layout A: iv(12) + tag(16) + ciphertext
      Layout B: iv(12) + ciphertext + tag(16)  (tag appended)
    """
    raw_key = os.environ.get("CREDENTIAL_ENCRYPTION_KEY", "").strip()
    if not raw_key:
        raise RuntimeError(
            "CREDENTIAL_ENCRYPTION_KEY is not set — add it to .env"
        )

    # Accept 64-char hex (32 bytes) or base64, or raw bytes
    try:
        key = bytes.fromhex(raw_key) if len(raw_key) == 64 else base64.b64decode(raw_key + "==")
    except Exception:
        key = raw_key.encode()

    logger.debug("Decrypting apiKey (key length %d bytes)", len(key))

    b64 = encrypted.removeprefix("enc:v1:")
    b64 += "=" * (-len(b64) % 4)
    payload = base64.urlsafe_b64decode(b64)
    logger.debug("Decryption payload length: %d bytes", len(payload))

    aesgcm = AESGCM(key)

    # Layout A — iv(12) + tag(16) + ciphertext
    try:
        iv  = payload[:12]
        tag = payload[12:28]
        ct  = payload[28:]
        plain = aesgcm.decrypt(iv, ct + tag, None)
        logger.debug("Decrypted with layout A (iv12+tag16+ct)")
        return json.loads(plain)
    except Exception as e:
        logger.debug("Layout A failed: %s", e)

    # Layout B — iv(12) + ciphertext||tag (tag is last 16 bytes)
    try:
        plain = aesgcm.decrypt(payload[:12], payload[12:], None)
        logger.debug("Decrypted with layout B (iv12+ct||tag)")
        return json.loads(plain)
    except Exception as e:
        logger.debug("Layout B failed: %s", e)

    # Layout C — iv(16) + tag(16) + ciphertext
    try:
        iv  = payload[:16]
        tag = payload[16:32]
        ct  = payload[32:]
        plain = aesgcm.decrypt(iv, ct + tag, None)
        logger.debug("Decrypted with layout C (iv16+tag16+ct)")
        return json.loads(plain)
    except Exception as e:
        logger.debug("Layout C failed: %s", e)

    raise RuntimeError(
        "Failed to decrypt apiKey — verify CREDENTIAL_ENCRYPTION_KEY matches "
        "the dashboard's encryption key"
    )


def _normalize_dashboard_cred(doc: dict) -> dict:
    """
    Decrypt the apiKey field and return a credential dict ready for API calls.
    The decrypted apiKey is a full credential document with an 'oauth' sub-key.
    """
    api_key_raw = doc.get("apiKey", "")
    if api_key_raw.startswith("enc:v1:"):
        decrypted = _decrypt_api_key(api_key_raw)
    else:
        try:
            decrypted = json.loads(api_key_raw)
        except Exception:
            decrypted = {}

    logger.info("Decrypted apiKey keys: %s", list(decrypted.keys()))

    # The decrypted value IS the full credential document
    if "oauth" in decrypted:
        logger.info(
            "Using decrypted credential: provider=%s email=%s",
            decrypted.get("provider"), decrypted.get("email_address"),
        )
        return decrypted

    # Fallback: decrypted value is a flat OAuth dict (no 'oauth' wrapper)
    logger.warning("No 'oauth' key in decrypted apiKey — attempting flat mapping")
    logger.info("Available keys: %s", list(decrypted.keys()))

    def _pick(*keys):
        for k in keys:
            if decrypted.get(k):
                return decrypted[k]
        return ""

    cred_type = doc.get("type", "")
    provider  = "google" if "google" in cred_type else "microsoft" if "microsoft" in cred_type or "outlook" in cred_type else cred_type
    label     = doc.get("label", "")
    email     = label.split("·")[-1].strip() if "·" in label else doc.get("email", label)

    return {
        "credential_id": doc.get("accountId") or str(doc.get("_id", "")),
        "provider":      provider,
        "email_address": email,
        "display_name":  doc.get("clientName", ""),
        "oauth": {
            "client_id":     _pick("client_id",     "clientId"),
            "client_secret": _pick("client_secret", "clientSecret"),
            "access_token":  _pick("access_token",  "accessToken",  "token"),
            "refresh_token": _pick("refresh_token", "refreshToken"),
            "token_expiry":  _pick("token_expiry",  "tokenExpiry",  "expiry_date"),
            "tenant_id":     _pick("tenant_id",     "tenantId"),
        },
    }


# ── Token management ──────────────────────────────────────────────────────────

async def _refresh_calendar_token(oauth: dict, provider: str) -> dict:
    is_google = "google" in provider.lower()
    url = (
        "https://oauth2.googleapis.com/token"
        if is_google
        else f"https://login.microsoftonline.com/{oauth.get('tenant_id', 'common')}/oauth2/v2.0/token"
    )
    logger.debug("Refreshing calendar token for provider=%s", provider)
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            url,
            data={
                "client_id":     oauth["client_id"],
                "client_secret": oauth["client_secret"],
                "refresh_token": oauth["refresh_token"],
                "grant_type":    "refresh_token",
            },
        )
        resp.raise_for_status()
        tokens = resp.json()

    if "access_token" not in tokens:
        raise RuntimeError(f"Calendar token refresh failed: {tokens}")

    new_expiry = (datetime.now(timezone.utc) + timedelta(seconds=tokens["expires_in"])).isoformat()
    logger.info("Calendar token refreshed, new expiry: %s", new_expiry)
    return {**oauth, "access_token": tokens["access_token"], "token_expiry": new_expiry}


async def _get_credential(credential_id: str) -> dict:
    """Fetch a calendar credential from the `credentials` collection by accountId."""
    db = get_db()
    logger.info("Looking up calendar credential: accountId=%s", credential_id)

    raw_doc = await db.credentials.find_one({"accountId": credential_id})
    if not raw_doc:
        logger.error("No document in `credentials` with accountId=%s", credential_id)
        raise ValueError(f"Calendar credential not found: {credential_id}")

    logger.info("Found credential in `credentials` collection for accountId=%s", credential_id)
    cred = _normalize_dashboard_cred(raw_doc)

    oauth      = cred["oauth"]
    expiry_str = oauth.get("token_expiry", "")
    if expiry_str:
        try:
            expiry = datetime.fromisoformat(expiry_str.replace("Z", "+00:00"))
            if expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) >= expiry - timedelta(seconds=60):
                logger.info("Token near/past expiry (%s) — refreshing", expiry_str)
                oauth = await _refresh_calendar_token(oauth, cred["provider"])
                cred  = {**cred, "oauth": oauth}
        except (ValueError, TypeError):
            logger.warning("Could not parse token_expiry=%r — skipping refresh check", expiry_str)

    return cred


def _ensure_seconds(dt: str) -> str:
    """Add :00 seconds if the datetime string is missing them (T10:00 → T10:00:00)."""
    if "T" in dt and dt.count(":") == 1:
        return dt + ":00"
    return dt


# ── Event creation ────────────────────────────────────────────────────────────

async def create_event(
    credential_id: str,
    title: str,
    description: str,
    start_datetime: str,
    end_datetime: str,
    timezone_str: str,
    attendees: list[EventAttendee],
    add_meeting_link: bool = True,
    location: str = "",
) -> CalendarEvent:
    logger.info(
        "create_event: credential=%s title=%r start=%s end=%s attendees=%s",
        credential_id, title, start_datetime, end_datetime,
        [a.email for a in attendees],
    )
    cred  = await _get_credential(credential_id)
    token = cred["oauth"]["access_token"]

    if "google" in cred["provider"].lower():
        return await _create_google_event(
            token, title, description, start_datetime, end_datetime,
            timezone_str, attendees, add_meeting_link, location,
        )
    return await _create_microsoft_event(
        token, title, description, start_datetime, end_datetime,
        timezone_str, attendees, add_meeting_link, location,
    )


async def _create_google_event(
    token, title, description, start, end, tz, attendees, add_meeting_link, location
) -> CalendarEvent:
    start = _ensure_seconds(start)
    end   = _ensure_seconds(end)
    body: dict = {
        "summary":     title,
        "description": description,
        "location":    location,
        "start":       {"dateTime": start, "timeZone": tz},
        "end":         {"dateTime": end,   "timeZone": tz},
        "attendees":   [{"email": a.email, "displayName": a.name} for a in attendees],
    }
    if add_meeting_link:
        body["conferenceData"] = {
            "createRequest": {
                "requestId": f"meet-{start}",
                "conferenceSolutionKey": {"type": "hangoutsMeet"},
            }
        }

    params: dict = {"sendUpdates": "all"}
    if add_meeting_link:
        params["conferenceDataVersion"] = "1"
    logger.debug("Google Calendar create event body: %s", body)

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{_GOOGLE_BASE}/calendars/primary/events",
            params=params,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json=body,
        )
        if resp.status_code != 200:
            logger.error(
                "Google Calendar API error %d: %s", resp.status_code, resp.text
            )
        resp.raise_for_status()
        data = resp.json()

    logger.info("Google event created: id=%s", data.get("id"))

    meeting_link = None
    if add_meeting_link:
        entry_points = (data.get("conferenceData") or {}).get("entryPoints") or []
        for ep in entry_points:
            if ep.get("entryPointType") == "video":
                meeting_link = ep.get("uri")
                break

    return CalendarEvent(
        event_id=data["id"],
        title=data.get("summary", title),
        start=data["start"].get("dateTime", start),
        end=data["end"].get("dateTime", end),
        meeting_link=meeting_link,
        calendar_link=data.get("htmlLink"),
        attendees=[
            EventAttendee(email=a["email"], name=a.get("displayName", ""))
            for a in data.get("attendees", [])
        ],
    )


async def _create_microsoft_event(
    token, title, description, start, end, tz, attendees, add_meeting_link, location
) -> CalendarEvent:
    start = _ensure_seconds(start)
    end   = _ensure_seconds(end)
    body: dict = {
        "subject":  title,
        "body":     {"contentType": "HTML", "content": description},
        "location": {"displayName": location},
        "start":    {"dateTime": start, "timeZone": tz},
        "end":      {"dateTime": end,   "timeZone": tz},
        "attendees": [
            {"emailAddress": {"address": a.email, "name": a.name}, "type": "required"}
            for a in attendees
        ],
    }
    if add_meeting_link:
        body["isOnlineMeeting"]       = True
        body["onlineMeetingProvider"] = "teamsForBusiness"

    logger.debug("Microsoft Graph create event body: %s", body)

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{_GRAPH_BASE}/me/events",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json=body,
        )
        if resp.status_code not in (200, 201):
            logger.error(
                "Microsoft Graph API error %d: %s", resp.status_code, resp.text
            )
        resp.raise_for_status()
        data = resp.json()

    logger.info("Microsoft event created: id=%s", data.get("id"))

    def _extract_teams_link(d: dict) -> str | None:
        return (
            (d.get("onlineMeeting") or {}).get("joinUrl")
            or d.get("onlineMeetingUrl")
            or None
        )

    meeting_link = _extract_teams_link(data)

    # Graph API often omits joinUrl from the POST response — fetch the event to get it
    if add_meeting_link and not meeting_link:
        logger.info("Teams joinUrl absent from creation response — fetching event")
        async with httpx.AsyncClient(timeout=30) as client:
            get_resp = await client.get(
                f"{_GRAPH_BASE}/me/events/{data['id']}",
                params={"$select": "id,subject,start,end,webLink,onlineMeeting,onlineMeetingUrl,isOnlineMeeting,attendees"},
                headers={"Authorization": f"Bearer {token}"},
            )
            if get_resp.status_code == 200:
                fetched = get_resp.json()
                meeting_link = _extract_teams_link(fetched)
                if meeting_link:
                    data = fetched
                logger.info("Teams joinUrl after event fetch: %s", meeting_link)
            else:
                logger.warning("Failed to fetch event for joinUrl: %d", get_resp.status_code)

    # Last resort: create a standalone Teams meeting and embed the link in the event body.
    # This works for personal Microsoft accounts where isOnlineMeeting is silently ignored.
    if add_meeting_link and not meeting_link:
        logger.info("Falling back to /me/onlineMeetings to generate Teams link")
        try:
            # Build UTC ISO strings for the onlineMeetings endpoint
            def _to_utc_iso(dt_str: str, tz_name: str) -> str:
                from zoneinfo import ZoneInfo
                dt = datetime.fromisoformat(dt_str)
                if dt.tzinfo is None:
                    try:
                        dt = dt.replace(tzinfo=ZoneInfo(tz_name))
                    except Exception:
                        dt = dt.replace(tzinfo=timezone.utc)
                return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

            async with httpx.AsyncClient(timeout=30) as client:
                om_resp = await client.post(
                    f"{_GRAPH_BASE}/me/onlineMeetings",
                    headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                    json={
                        "startDateTime": _to_utc_iso(start, tz),
                        "endDateTime":   _to_utc_iso(end,   tz),
                        "subject":       title,
                    },
                )
                logger.info("onlineMeetings response: %d %s", om_resp.status_code, om_resp.text[:300])
                if om_resp.status_code in (200, 201):
                    om_data = om_resp.json()
                    meeting_link = om_data.get("joinUrl") or om_data.get("joinWebUrl") or None
                    logger.info("Teams link from onlineMeetings: %s", meeting_link)

                    # Embed link in the event body so attendees see it in the invite
                    if meeting_link:
                        join_html = (
                            f'<br><br><b>Join Teams Meeting:</b> '
                            f'<a href="{meeting_link}">{meeting_link}</a>'
                        )
                        await client.patch(
                            f"{_GRAPH_BASE}/me/events/{data['id']}",
                            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                            json={"body": {"contentType": "HTML", "content": description + join_html}},
                        )
        except Exception as exc:
            logger.warning("Standalone Teams meeting creation failed: %s", exc)

    meeting_link_error: str | None = None
    if add_meeting_link and not meeting_link:
        web_link = data.get("webLink", "")
        if "outlook.live.com" in web_link:
            meeting_link_error = (
                "Teams meeting links are not supported for personal Microsoft accounts "
                "(outlook.live.com). Please reconnect using a Microsoft 365 work or school account."
            )
        else:
            meeting_link_error = (
                "Teams meeting link could not be generated. Ensure the Microsoft 365 account "
                "has Teams enabled and the OAuth app includes the OnlineMeetings.ReadWrite permission."
            )
        logger.warning("meeting_link_error: %s", meeting_link_error)

    return CalendarEvent(
        event_id=data["id"],
        title=data.get("subject", title),
        start=data["start"].get("dateTime", start),
        end=data["end"].get("dateTime", end),
        meeting_link=meeting_link,
        meeting_link_error=meeting_link_error,
        calendar_link=data.get("webLink"),
        attendees=[
            EventAttendee(
                email=a["emailAddress"]["address"],
                name=a["emailAddress"].get("name", ""),
            )
            for a in data.get("attendees", [])
        ],
    )


# ── List events ───────────────────────────────────────────────────────────────

async def list_events(credential_id: str, days: int = 7) -> list[CalendarEvent]:
    logger.info("list_events: credential=%s days=%d", credential_id, days)
    cred  = await _get_credential(credential_id)
    token = cred["oauth"]["access_token"]

    now      = datetime.now(timezone.utc)
    time_max = now + timedelta(days=days)

    if "google" in cred["provider"].lower():
        return await _list_google_events(token, now.isoformat(), time_max.isoformat())
    return await _list_microsoft_events(token, now.isoformat(), time_max.isoformat())


async def _list_google_events(token, time_min, time_max) -> list[CalendarEvent]:
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            f"{_GOOGLE_BASE}/calendars/primary/events",
            params={
                "timeMin":      time_min,
                "timeMax":      time_max,
                "singleEvents": "true",
                "orderBy":      "startTime",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        if resp.status_code != 200:
            logger.error("Google list events error %d: %s", resp.status_code, resp.text)
        resp.raise_for_status()

    events = []
    for item in resp.json().get("items", []):
        entry_points = (item.get("conferenceData") or {}).get("entryPoints") or []
        meet_link    = next(
            (ep["uri"] for ep in entry_points if ep.get("entryPointType") == "video"), None
        )
        events.append(CalendarEvent(
            event_id=item["id"],
            title=item.get("summary", ""),
            start=item["start"].get("dateTime") or item["start"].get("date", ""),
            end=item["end"].get("dateTime")   or item["end"].get("date", ""),
            meeting_link=meet_link,
            calendar_link=item.get("htmlLink"),
            attendees=[
                EventAttendee(email=a["email"], name=a.get("displayName", ""))
                for a in item.get("attendees", [])
            ],
        ))
    logger.info("Google list_events returned %d events", len(events))
    return events


async def _list_microsoft_events(token, time_min, time_max) -> list[CalendarEvent]:
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            f"{_GRAPH_BASE}/me/events",
            params={
                "$filter":  f"start/dateTime ge '{time_min}' and end/dateTime le '{time_max}'",
                "$orderby": "start/dateTime",
                "$select":  "id,subject,start,end,webLink,onlineMeeting,attendees",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        if resp.status_code != 200:
            logger.error("Microsoft list events error %d: %s", resp.status_code, resp.text)
        resp.raise_for_status()

    events = []
    for item in resp.json().get("value", []):
        events.append(CalendarEvent(
            event_id=item["id"],
            title=item.get("subject", ""),
            start=item["start"].get("dateTime", ""),
            end=item["end"].get("dateTime", ""),
            meeting_link=(item.get("onlineMeeting") or {}).get("joinUrl"),
            calendar_link=item.get("webLink"),
            attendees=[
                EventAttendee(
                    email=a["emailAddress"]["address"],
                    name=a["emailAddress"].get("name", ""),
                )
                for a in item.get("attendees", [])
            ],
        ))
    logger.info("Microsoft list_events returned %d events", len(events))
    return events


# ── Availability / free-busy ──────────────────────────────────────────────────

async def check_availability(
    credential_id: str,
    start_datetime: str,
    end_datetime: str,
    timezone_str: str = "UTC",
) -> list[AvailabilitySlot]:
    logger.info(
        "check_availability: credential=%s start=%s end=%s",
        credential_id, start_datetime, end_datetime,
    )
    cred  = await _get_credential(credential_id)
    token = cred["oauth"]["access_token"]

    if "google" in cred["provider"].lower():
        busy = await _google_freebusy(token, cred["email_address"], start_datetime, end_datetime)
    else:
        busy = await _microsoft_freebusy(
            token, cred["email_address"], start_datetime, end_datetime, timezone_str
        )

    slots = _busy_to_slots(start_datetime, end_datetime, busy)
    logger.info(
        "check_availability: %d slots (%d free)",
        len(slots), sum(1 for s in slots if s.is_free),
    )
    return slots


async def _google_freebusy(token, email, time_min, time_max) -> list[dict]:
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{_GOOGLE_BASE}/freeBusy",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={"timeMin": time_min, "timeMax": time_max, "items": [{"id": email}]},
        )
        if resp.status_code != 200:
            logger.error("Google freeBusy error %d: %s", resp.status_code, resp.text)
        resp.raise_for_status()

    calendars = resp.json().get("calendars", {})
    busy      = calendars.get(email, {}).get("busy", [])
    logger.debug("Google freeBusy: %d busy slots for %s", len(busy), email)
    return busy


async def _microsoft_freebusy(token, email, start, end, tz) -> list[dict]:
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{_GRAPH_BASE}/me/calendar/getSchedule",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={
                "schedules":                [email],
                "startTime":               {"dateTime": start, "timeZone": tz},
                "endTime":                 {"dateTime": end,   "timeZone": tz},
                "availabilityViewInterval": 30,
            },
        )
        if resp.status_code != 200:
            logger.error("Microsoft getSchedule error %d: %s", resp.status_code, resp.text)
        resp.raise_for_status()

    schedules = resp.json().get("value", [])
    if not schedules:
        return []
    busy = [
        {"start": item["start"]["dateTime"], "end": item["end"]["dateTime"]}
        for item in schedules[0].get("scheduleItems", [])
        if item.get("status") == "busy"
    ]
    logger.debug("Microsoft freeBusy: %d busy slots for %s", len(busy), email)
    return busy


def _busy_to_slots(range_start: str, range_end: str, busy: list[dict]) -> list[AvailabilitySlot]:
    try:
        start = datetime.fromisoformat(range_start.replace("Z", "+00:00"))
        end   = datetime.fromisoformat(range_end.replace("Z",   "+00:00"))
    except ValueError:
        logger.error("Invalid datetime range: %r – %r", range_start, range_end)
        return []

    busy_ranges = []
    for b in busy:
        try:
            bs = datetime.fromisoformat(b["start"].replace("Z", "+00:00"))
            be = datetime.fromisoformat(b["end"].replace("Z",   "+00:00"))
            busy_ranges.append((bs, be))
        except (KeyError, ValueError):
            pass

    slots: list[AvailabilitySlot] = []
    cursor = start
    while cursor < end:
        slot_end = cursor + timedelta(minutes=30)
        is_free  = not any(bs <= cursor < be for bs, be in busy_ranges)
        slots.append(AvailabilitySlot(start=cursor.isoformat(), end=slot_end.isoformat(), is_free=is_free))
        cursor = slot_end
    return slots


# ── Smart booking (availability check + suggest) ─────────────────────────────

def _slot_label(original_start: datetime, slot_start: datetime, slot_end: datetime) -> str:
    """Human-friendly label for a suggested slot relative to the original time."""
    diff = slot_start - original_start
    total_minutes = int(diff.total_seconds() / 60)

    if total_minutes < 60:
        base = f"{total_minutes} minutes later"
    elif total_minutes < 120:
        base = "1 hour later"
    elif total_minutes < 1440:
        hours = total_minutes // 60
        base = f"{hours} hours later"
    else:
        days = total_minutes // 1440
        base = f"{'Tomorrow' if days == 1 else f'In {days} days'}"

    try:
        time_str = slot_start.strftime("%I:%M %p").lstrip("0") or "12:00 AM"
    except Exception:
        time_str = slot_start.isoformat()
    return f"{base} ({time_str})"


def _find_nearest_slots(
    search_from: datetime,
    search_until: datetime,
    busy: list[dict],
    duration_minutes: int,
    count: int = 3,
) -> list[SuggestedSlot]:
    """Return the nearest `count` free consecutive slots of `duration_minutes` length."""
    busy_ranges: list[tuple[datetime, datetime]] = []
    for b in busy:
        try:
            bs = datetime.fromisoformat(b["start"].replace("Z", "+00:00"))
            be = datetime.fromisoformat(b["end"].replace("Z",   "+00:00"))
            if bs.tzinfo is None:
                bs = bs.replace(tzinfo=timezone.utc)
            if be.tzinfo is None:
                be = be.replace(tzinfo=timezone.utc)
            busy_ranges.append((bs, be))
        except (KeyError, ValueError):
            pass

    duration  = timedelta(minutes=duration_minutes)
    slots: list[SuggestedSlot] = []

    # Round up to the next 30-minute boundary
    remainder = search_from.minute % 30
    cursor    = search_from + (timedelta(minutes=30 - remainder) if remainder else timedelta())
    cursor    = cursor.replace(second=0, microsecond=0)

    while cursor < search_until and len(slots) < count:
        slot_end = cursor + duration
        if slot_end > search_until:
            break

        is_free = not any(bs < slot_end and be > cursor for bs, be in busy_ranges)
        if is_free:
            slots.append(SuggestedSlot(
                start=cursor.isoformat(),
                end=slot_end.isoformat(),
                label=_slot_label(search_from, cursor, slot_end),
            ))

        cursor += timedelta(minutes=30)

    return slots


async def smart_book_event(
    credential_id: str,
    title: str,
    description: str,
    start_datetime: str,
    end_datetime: str,
    timezone_str: str,
    attendees: list[EventAttendee],
    add_meeting_link: bool = True,
    location: str = "",
    _check_only: bool = False,   # True = only check availability, never create
) -> BookingResponse:
    """
    Check availability first. If the slot is free, create the event and return
    booked=True. If busy, return booked=False with up to 3 nearest free slots.
    """
    logger.info(
        "smart_book_event: credential=%s start=%s end=%s",
        credential_id, start_datetime, end_datetime,
    )

    start_norm = _ensure_seconds(start_datetime)
    end_norm   = _ensure_seconds(end_datetime)

    # Parse times and attach timezone so freebusy gets a valid RFC 3339 string
    try:
        dt_start = datetime.fromisoformat(start_norm)
        dt_end   = datetime.fromisoformat(end_norm)
    except ValueError as exc:
        raise ValueError(f"Invalid datetime format: {exc}")

    # Make timezone-aware using the provided timezone string
    if dt_start.tzinfo is None and timezone_str:
        try:
            from zoneinfo import ZoneInfo
            tz = ZoneInfo(timezone_str)
            dt_start = dt_start.replace(tzinfo=tz)
            dt_end   = dt_end.replace(tzinfo=tz)
        except Exception:
            dt_start = dt_start.replace(tzinfo=timezone.utc)
            dt_end   = dt_end.replace(tzinfo=timezone.utc)

    duration_minutes = max(30, int((dt_end - dt_start).total_seconds() / 60))

    # Fetch busy periods for [start, start + 3 days] to cover suggestions too
    search_until = dt_start + timedelta(days=3)
    cred  = await _get_credential(credential_id)
    token = cred["oauth"]["access_token"]

    if "google" in cred["provider"].lower():
        busy = await _google_freebusy(
            token, cred["email_address"],
            dt_start.isoformat(), search_until.isoformat(),
        )
    else:
        busy = await _microsoft_freebusy(
            token, cred["email_address"],
            dt_start.isoformat(), search_until.isoformat(), timezone_str,
        )

    logger.info("Busy periods in window: %d", len(busy))

    # Check if the requested slot overlaps any busy period
    busy_ranges = []
    for b in busy:
        try:
            bs = datetime.fromisoformat(b["start"].replace("Z", "+00:00"))
            be = datetime.fromisoformat(b["end"].replace("Z",   "+00:00"))
            if bs.tzinfo is None:
                bs = bs.replace(tzinfo=timezone.utc)
            if be.tzinfo is None:
                be = be.replace(tzinfo=timezone.utc)
            busy_ranges.append((bs, be))
        except (KeyError, ValueError):
            pass

    is_conflict = any(bs < dt_end and be > dt_start for bs, be in busy_ranges)

    if not is_conflict:
        if _check_only:
            return BookingResponse(booked=True)
        logger.info("Slot is free — creating event")
        event = await create_event(
            credential_id=credential_id,
            title=title,
            description=description,
            start_datetime=start_norm,
            end_datetime=end_norm,
            timezone_str=timezone_str,
            attendees=attendees,
            add_meeting_link=add_meeting_link,
            location=location,
        )
        return BookingResponse(booked=True, event=event)

    # Slot is busy — find nearest alternatives
    logger.info("Slot is busy — finding nearest free slots")
    suggestions = _find_nearest_slots(
        search_from=dt_start,
        search_until=search_until,
        busy=busy,
        duration_minutes=duration_minutes,
        count=3,
    )

    return BookingResponse(
        booked=False,
        conflict=True,
        message=f"The requested time ({start_datetime} – {end_datetime}) is not available.",
        suggested_slots=suggestions,
    )


# ── Update (edit) event ───────────────────────────────────────────────────────

async def update_event(
    credential_id: str,
    event_id: str,
    title: str | None = None,
    description: str | None = None,
    location: str | None = None,
    start_datetime: str | None = None,
    end_datetime: str | None = None,
    timezone_str: str | None = None,
    attendees: list[EventAttendee] | None = None,
    add_meeting_link: bool | None = None,
) -> CalendarEvent:
    logger.info("update_event: credential=%s event=%s", credential_id, event_id)
    cred  = await _get_credential(credential_id)
    token = cred["oauth"]["access_token"]

    if "google" in cred["provider"].lower():
        return await _update_google_event(
            token, event_id, title, description, location,
            start_datetime, end_datetime, timezone_str, attendees, add_meeting_link,
        )
    return await _update_microsoft_event(
        token, event_id, title, description, location,
        start_datetime, end_datetime, timezone_str, attendees, add_meeting_link,
    )


async def _update_google_event(
    token, event_id, title, description, location,
    start, end, tz, attendees, add_meeting_link,
) -> CalendarEvent:
    # Fetch current event first so we only patch changed fields
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            f"{_GOOGLE_BASE}/calendars/primary/events/{event_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        if resp.status_code != 200:
            logger.error("Google get event error %d: %s", resp.status_code, resp.text)
        resp.raise_for_status()
        current = resp.json()

    patch: dict = {}
    if title       is not None: patch["summary"]     = title
    if description is not None: patch["description"] = description
    if location    is not None: patch["location"]    = location
    if attendees   is not None:
        patch["attendees"] = [{"email": a.email, "displayName": a.name} for a in attendees]

    tz_val = tz or current.get("start", {}).get("timeZone", "UTC")
    if start is not None:
        patch["start"] = {"dateTime": _ensure_seconds(start), "timeZone": tz_val}
    if end is not None:
        patch["end"]   = {"dateTime": _ensure_seconds(end),   "timeZone": tz_val}

    if add_meeting_link and not current.get("conferenceData"):
        patch["conferenceData"] = {
            "createRequest": {
                "requestId": f"meet-update-{event_id}",
                "conferenceSolutionKey": {"type": "hangoutsMeet"},
            }
        }

    params: dict = {"sendUpdates": "all"}
    if add_meeting_link:
        params["conferenceDataVersion"] = "1"
    logger.debug("Google PATCH event body: %s", patch)

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.patch(
            f"{_GOOGLE_BASE}/calendars/primary/events/{event_id}",
            params=params,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json=patch,
        )
        if resp.status_code != 200:
            logger.error("Google update event error %d: %s", resp.status_code, resp.text)
        resp.raise_for_status()
        data = resp.json()

    logger.info("Google event updated: id=%s", data.get("id"))

    entry_points = (data.get("conferenceData") or {}).get("entryPoints") or []
    meet_link    = next((ep["uri"] for ep in entry_points if ep.get("entryPointType") == "video"), None)

    return CalendarEvent(
        event_id=data["id"],
        title=data.get("summary", ""),
        start=data["start"].get("dateTime", ""),
        end=data["end"].get("dateTime", ""),
        meeting_link=meet_link,
        calendar_link=data.get("htmlLink"),
        attendees=[
            EventAttendee(email=a["email"], name=a.get("displayName", ""))
            for a in data.get("attendees", [])
        ],
    )


async def _update_microsoft_event(
    token, event_id, title, description, location,
    start, end, tz, attendees, add_meeting_link,
) -> CalendarEvent:
    patch: dict = {}
    if title       is not None: patch["subject"]              = title
    if description is not None: patch["body"]                 = {"contentType": "HTML", "content": description}
    if location    is not None: patch["location"]             = {"displayName": location}
    if add_meeting_link:
        patch["isOnlineMeeting"]       = True
        patch["onlineMeetingProvider"] = "teamsForBusiness"
    if attendees is not None:
        patch["attendees"] = [
            {"emailAddress": {"address": a.email, "name": a.name}, "type": "required"}
            for a in attendees
        ]

    tz_val = tz or "UTC"
    if start is not None:
        patch["start"] = {"dateTime": _ensure_seconds(start), "timeZone": tz_val}
    if end is not None:
        patch["end"]   = {"dateTime": _ensure_seconds(end),   "timeZone": tz_val}

    logger.debug("Microsoft PATCH event body: %s", patch)

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.patch(
            f"{_GRAPH_BASE}/me/events/{event_id}",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json=patch,
        )
        if resp.status_code not in (200, 201):
            logger.error("Microsoft update event error %d: %s", resp.status_code, resp.text)
        resp.raise_for_status()
        data = resp.json()

    logger.info("Microsoft event updated: id=%s", data.get("id"))

    return CalendarEvent(
        event_id=data["id"],
        title=data.get("subject", ""),
        start=data["start"].get("dateTime", ""),
        end=data["end"].get("dateTime", ""),
        meeting_link=(data.get("onlineMeeting") or {}).get("joinUrl"),
        calendar_link=data.get("webLink"),
        attendees=[
            EventAttendee(
                email=a["emailAddress"]["address"],
                name=a["emailAddress"].get("name", ""),
            )
            for a in data.get("attendees", [])
        ],
    )


# ── RSVP check ────────────────────────────────────────────────────────────────

async def get_rsvp(credential_id: str, event_id: str) -> RSVPResponse:
    logger.info("get_rsvp: credential=%s event=%s", credential_id, event_id)
    cred  = await _get_credential(credential_id)
    token = cred["oauth"]["access_token"]

    if "google" in cred["provider"].lower():
        return await _google_rsvp(token, event_id)
    return await _microsoft_rsvp(token, event_id)


async def _google_rsvp(token: str, event_id: str) -> RSVPResponse:
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            f"{_GOOGLE_BASE}/calendars/primary/events/{event_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        if resp.status_code != 200:
            logger.error("Google get event (RSVP) error %d: %s", resp.status_code, resp.text)
        resp.raise_for_status()
        data = resp.json()

    # Google responseStatus values: accepted | declined | tentative | needsAction
    status_map = {
        "accepted":    "accepted",
        "declined":    "declined",
        "tentative":   "tentative",
        "needsAction": "awaiting",
    }

    attendees = [
        AttendeeRSVP(
            email=a["email"],
            name=a.get("displayName", ""),
            status=status_map.get(a.get("responseStatus", "needsAction"), "awaiting"),
        )
        for a in data.get("attendees", [])
    ]
    logger.info("Google RSVP: event=%s attendees=%d", event_id, len(attendees))
    return RSVPResponse(event_id=event_id, title=data.get("summary", ""), attendees=attendees)


async def _microsoft_rsvp(token: str, event_id: str) -> RSVPResponse:
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            f"{_GRAPH_BASE}/me/events/{event_id}",
            params={"$select": "id,subject,attendees"},
            headers={"Authorization": f"Bearer {token}"},
        )
        if resp.status_code != 200:
            logger.error("Microsoft get event (RSVP) error %d: %s", resp.status_code, resp.text)
        resp.raise_for_status()
        data = resp.json()

    # Microsoft response values: accepted | declined | tentative | none | notResponded
    status_map = {
        "accepted":     "accepted",
        "declined":     "declined",
        "tentative":    "tentative",
        "none":         "awaiting",
        "notResponded": "awaiting",
    }

    attendees = [
        AttendeeRSVP(
            email=a["emailAddress"]["address"],
            name=a["emailAddress"].get("name", ""),
            status=status_map.get(
                (a.get("status") or {}).get("response", "none"), "awaiting"
            ),
        )
        for a in data.get("attendees", [])
    ]
    logger.info("Microsoft RSVP: event=%s attendees=%d", event_id, len(attendees))
    return RSVPResponse(event_id=event_id, title=data.get("subject", ""), attendees=attendees)


# ── Delete event ─────────────────────────────────────────────────────────────

async def delete_event(credential_id: str, event_id: str) -> None:
    logger.info("delete_event: credential=%s event=%s", credential_id, event_id)
    cred  = await _get_credential(credential_id)
    token = cred["oauth"]["access_token"]

    if "google" in cred["provider"].lower():
        url = f"{_GOOGLE_BASE}/calendars/primary/events/{event_id}"
    else:
        url = f"{_GRAPH_BASE}/me/events/{event_id}"

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.delete(url, headers={"Authorization": f"Bearer {token}"})
        if resp.status_code not in (200, 204):
            logger.error("Delete event error %d: %s", resp.status_code, resp.text)
        resp.raise_for_status()

    logger.info("Event deleted: %s", event_id)
