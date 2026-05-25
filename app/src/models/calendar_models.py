from pydantic import BaseModel, ConfigDict, model_validator


class OAuthCredential(BaseModel):
    client_id: str
    client_secret: str
    access_token: str
    refresh_token: str
    token_expiry: str          # ISO 8601
    tenant_id: str = ""        # Microsoft only


class CalendarCredentialIn(BaseModel):
    credential_id: str
    provider: str              # "google" | "microsoft"
    email_address: str
    display_name: str = ""
    oauth: OAuthCredential


class EventAttendee(BaseModel):
    email: str
    name: str = ""


class CreateEventRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")  # ignore unknown fields like compaginId

    credential_id: str
    title: str
    description: str = ""
    location: str = ""
    start_datetime: str        # ISO 8601, e.g. "2026-05-23T10:00:00+05:00"
    end_datetime: str          # ISO 8601
    timezone: str = "UTC"
    attendees: list[EventAttendee] = []
    add_meeting_link: bool = True   # Google Meet / Teams link
    check_availability: bool = True # if True, block booking when slot is busy

    @model_validator(mode="before")
    @classmethod
    def _normalize(cls, values: dict) -> dict:
        # Accept camelCase field names sent by the dashboard
        camel_map = {
            "startDateTime":     "start_datetime",
            "endDateTime":       "end_datetime",
            "timeZone":          "timezone",
            "credentialId":      "credential_id",
            "addMeetingLink":    "add_meeting_link",
            "checkAvailability": "check_availability",
        }
        for camel, snake in camel_map.items():
            if camel in values and snake not in values:
                values[snake] = values.pop(camel)

        # Accept attendees as a plain list of email strings
        raw_attendees = values.get("attendees", [])
        if raw_attendees and isinstance(raw_attendees[0], str):
            values["attendees"] = [{"email": e, "name": ""} for e in raw_attendees]

        return values


class CalendarEvent(BaseModel):
    event_id: str
    title: str
    start: str
    end: str
    meeting_link: str | None = None        # Google Meet or Teams URL
    meeting_link_error: str | None = None  # why meeting_link is null (if add_meeting_link=true)
    calendar_link: str | None = None       # link to view event in calendar UI
    attendees: list[EventAttendee] = []


class SuggestedSlot(BaseModel):
    start: str
    end: str
    label: str          # e.g. "30 minutes later", "Tomorrow at 10:00 AM"


class BookingResponse(BaseModel):
    booked: bool
    event: CalendarEvent | None = None          # present when booked=True
    conflict: bool = False                      # present when booked=False
    message: str = ""
    suggested_slots: list[SuggestedSlot] = []  # present when conflict=True


class UpdateEventRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    title: str | None = None
    description: str | None = None
    location: str | None = None
    start_datetime: str | None = None
    end_datetime: str | None = None
    timezone: str | None = None
    attendees: list[EventAttendee] | None = None
    add_meeting_link: bool | None = None
    check_availability: bool = True   # check for conflicts when time is changing

    @model_validator(mode="before")
    @classmethod
    def _normalize(cls, values: dict) -> dict:
        camel_map = {
            "startDateTime":     "start_datetime",
            "endDateTime":       "end_datetime",
            "timeZone":          "timezone",
            "addMeetingLink":    "add_meeting_link",
            "checkAvailability": "check_availability",
        }
        for camel, snake in camel_map.items():
            if camel in values and snake not in values:
                values[snake] = values.pop(camel)

        raw_attendees = values.get("attendees")
        if raw_attendees and isinstance(raw_attendees[0], str):
            values["attendees"] = [{"email": e, "name": ""} for e in raw_attendees]

        return values


class AttendeeRSVP(BaseModel):
    email: str
    name: str = ""
    status: str          # accepted | declined | tentative | awaiting


class RSVPResponse(BaseModel):
    event_id: str
    title: str
    attendees: list[AttendeeRSVP]


class AvailabilityRequest(BaseModel):
    credential_id: str
    start_datetime: str        # ISO 8601
    end_datetime: str          # ISO 8601
    timezone: str = "UTC"


class AvailabilitySlot(BaseModel):
    start: str
    end: str
    is_free: bool


class AvailabilityResponse(BaseModel):
    credential_id: str
    slots: list[AvailabilitySlot]
