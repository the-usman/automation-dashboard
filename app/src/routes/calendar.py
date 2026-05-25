"""
Calendar routes:
  POST   /calendar/credentials              — store a calendar credential
  GET    /calendar/events/{credential_id}   — list upcoming events
  POST   /calendar/events                   — create an event (returns Meet/Teams link)
  DELETE /calendar/events/{cred_id}/{event_id}
  GET    /calendar/availability/{cred_id}   — free/busy slots
"""

from fastapi import APIRouter, HTTPException, Query

from app.src.models.calendar_models import (
    AvailabilityRequest,
    AvailabilityResponse,
    BookingResponse,
    CalendarCredentialIn,
    CalendarEvent,
    CreateEventRequest,
    RSVPResponse,
    UpdateEventRequest,
)
from app.src.services import calendar_service
from app.src.services.mongodb import get_db

router = APIRouter(prefix="/calendar", tags=["Calendar"])


@router.post("/credentials", status_code=201)
async def store_calendar_credential(body: CalendarCredentialIn):
    """Store or update a calendar credential in calendar_db_client."""
    db = get_db()
    doc = body.model_dump()
    await db.calendar_db_client.update_one(
        {"credential_id": body.credential_id},
        {"$set": doc},
        upsert=True,
    )
    return {"credential_id": body.credential_id, "provider": body.provider}


@router.get("/events/{credential_id}", response_model=list[CalendarEvent])
async def get_events(credential_id: str, days: int = Query(default=7, ge=1, le=90)):
    """List upcoming calendar events for the next N days."""
    try:
        return await calendar_service.list_events(credential_id, days)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Calendar API error: {exc}")


@router.post("/events", response_model=BookingResponse, status_code=201)
async def create_event(request: CreateEventRequest):
    """
    Book a calendar event with built-in availability check (default: on).

    - **check_availability=true** (default): checks the calendar first.
      - Slot free → creates event, returns `{ booked: true, event: {...} }`
      - Slot busy → returns `{ booked: false, conflict: true, suggested_slots: [...] }`
        with up to 3 nearest available alternatives.
    - **check_availability=false**: skips the check and creates immediately.
    """
    try:
        if request.check_availability:
            return await calendar_service.smart_book_event(
                credential_id=request.credential_id,
                title=request.title,
                description=request.description,
                start_datetime=request.start_datetime,
                end_datetime=request.end_datetime,
                timezone_str=request.timezone,
                attendees=request.attendees,
                add_meeting_link=request.add_meeting_link,
                location=request.location,
            )
        # Direct create — no availability check
        event = await calendar_service.create_event(
            credential_id=request.credential_id,
            title=request.title,
            description=request.description,
            start_datetime=request.start_datetime,
            end_datetime=request.end_datetime,
            timezone_str=request.timezone,
            attendees=request.attendees,
            add_meeting_link=request.add_meeting_link,
            location=request.location,
        )
        return BookingResponse(booked=True, event=event)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Calendar API error: {exc}")


@router.patch("/events/{credential_id}/{event_id}", response_model=BookingResponse)
async def update_event(credential_id: str, event_id: str, request: UpdateEventRequest):
    """
    Edit a calendar event. All fields optional — only sent fields are updated.

    When start or end time is changed and check_availability=true (default):
    - New slot free  → updates event, returns `{ booked: true, event: {...} }`
    - New slot busy  → returns `{ booked: false, conflict: true, suggested_slots: [...] }`
    """
    try:
        time_is_changing = request.start_datetime is not None or request.end_datetime is not None

        if time_is_changing and request.check_availability:
            # Need both start and end to check — fall back to 1-hour window if only one given
            check_start = request.start_datetime or request.end_datetime
            check_end   = request.end_datetime   or request.start_datetime
            avail = await calendar_service.smart_book_event(
                credential_id=credential_id,
                title=request.title or "",
                description=request.description or "",
                start_datetime=check_start,
                end_datetime=check_end,
                timezone_str=request.timezone or "UTC",
                attendees=request.attendees or [],
                add_meeting_link=False,   # don't create an event, just check
                location=request.location or "",
                _check_only=True,
            )
            if not avail.booked:
                return avail   # conflict — return suggestions, don't touch the event

        event = await calendar_service.update_event(
            credential_id=credential_id,
            event_id=event_id,
            title=request.title,
            description=request.description,
            location=request.location,
            start_datetime=request.start_datetime,
            end_datetime=request.end_datetime,
            timezone_str=request.timezone,
            attendees=request.attendees,
            add_meeting_link=request.add_meeting_link,
        )
        return BookingResponse(booked=True, event=event)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Calendar API error: {exc}")


@router.get("/events/{credential_id}/{event_id}/rsvp", response_model=RSVPResponse)
async def get_rsvp(credential_id: str, event_id: str):
    """
    Returns the RSVP status of every attendee on a calendar event.
    Status values: accepted | declined | tentative | awaiting
    """
    try:
        return await calendar_service.get_rsvp(credential_id, event_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Calendar API error: {exc}")


@router.delete("/events/{credential_id}/{event_id}", status_code=204)
async def delete_event(credential_id: str, event_id: str):
    """Delete a calendar event."""
    try:
        await calendar_service.delete_event(credential_id, event_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Calendar API error: {exc}")


@router.get("/availability/{credential_id}", response_model=AvailabilityResponse)
async def get_availability(
    credential_id: str,
    start: str = Query(..., description="ISO 8601 start datetime, e.g. 2026-05-23T09:00:00Z"),
    end:   str = Query(..., description="ISO 8601 end datetime,   e.g. 2026-05-23T17:00:00Z"),
    timezone: str = Query(default="UTC"),
):
    """
    Returns 30-minute slots between start and end, each marked is_free: true/false.
    """
    try:
        slots = await calendar_service.check_availability(credential_id, start, end, timezone)
        return AvailabilityResponse(credential_id=credential_id, slots=slots)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Calendar API error: {exc}")
