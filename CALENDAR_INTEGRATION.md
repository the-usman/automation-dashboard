# Dashboard Integration Guide — Calendar

This document describes how to integrate the **Calendar API** into the existing Next.js dashboard.
Hand this file to Cursor and ask it to implement the changes described below.

The calendar feature is a companion to the email outreach system already described in
`DASHBOARD_INTEGRATION.md`. It shares the same backend server and the same `AUTOMATION_API_URL`
environment variable.

---

## 1. Overview

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/calendar/credentials` | Store a Google or Microsoft calendar credential |
| `GET` | `/calendar/events/{credential_id}` | List upcoming events (next N days) |
| `POST` | `/calendar/events` | Create an event — returns Google Meet or Teams link |
| `DELETE` | `/calendar/events/{credential_id}/{event_id}` | Delete an event |
| `GET` | `/calendar/availability/{credential_id}` | Free/busy slots in 30-min increments |

### How it connects to email outreach

When sending emails via `POST /outreach/send`, pass an optional `calendar_credential_id`.
When the lead replies, the backend **automatically creates a calendar meeting** and returns
the `meeting_link` (Google Meet / Teams URL) in the reply-check response.

### Full UI flow

1. User connects a calendar account via a **Connect Calendar** form → stored as a credential.
2. On the **Email Outreach Composer**, user selects which calendar credential to attach
   (optional — enables auto-meeting on reply).
3. User visits the **Calendar** page to check availability, create meetings manually,
   and list upcoming events.
4. On the **Tracking / Leads** page, replied leads show a `meeting_link` badge.

---

## 2. Environment Variable

The calendar API uses the **same** env var as email outreach. Nothing new to add:

```env
AUTOMATION_API_URL=http://localhost:8001
```

---

## 3. TypeScript Types

Add the following to `lib/automationApi.ts` (append below the existing email types).

```typescript
// ── Calendar types ─────────────────────────────────────────────────────────────

export type CalendarProvider = "google" | "microsoft";

export type CalendarOAuth = {
  client_id: string;
  client_secret: string;
  access_token: string;
  refresh_token: string;
  token_expiry: string;       // ISO 8601
  tenant_id?: string;         // Microsoft only
};

export type CalendarCredentialIn = {
  credential_id: string;      // any unique string you choose, e.g. "cal-acme-google"
  provider: CalendarProvider;
  email_address: string;
  display_name?: string;
  oauth: CalendarOAuth;
};

export type EventAttendee = {
  email: string;
  name?: string;
};

export type CreateEventRequest = {
  credential_id: string;
  title: string;
  description?: string;
  start_datetime: string;     // ISO 8601, e.g. "2026-05-24T10:00:00Z"
  end_datetime: string;       // ISO 8601
  timezone?: string;          // default "UTC"
  attendees?: EventAttendee[];
  add_meeting_link?: boolean; // default true — generates Google Meet or Teams link
};

export type CalendarEvent = {
  event_id: string;
  title: string;
  start: string;
  end: string;
  meeting_link: string | null;   // Google Meet URL or Teams join URL
  calendar_link: string | null;  // link to view event in Google Calendar / Outlook
  attendees: EventAttendee[];
};

export type AvailabilitySlot = {
  start: string;
  end: string;
  is_free: boolean;
};

export type AvailabilityResponse = {
  credential_id: string;
  slots: AvailabilitySlot[];
};
```

---

## 4. API Helper Functions

Append to `lib/automationApi.ts`:

```typescript
// ── Calendar API helpers ───────────────────────────────────────────────────────

export async function storeCalendarCredential(
  body: CalendarCredentialIn
): Promise<{ credential_id: string; provider: string }> {
  const res = await fetch(`${BASE_URL}/calendar/credentials`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail ?? "Failed to store calendar credential");
  }
  return res.json();
}

export async function listCalendarEvents(
  credentialId: string,
  days = 7
): Promise<CalendarEvent[]> {
  const res = await fetch(
    `${BASE_URL}/calendar/events/${credentialId}?days=${days}`
  );
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail ?? "Failed to list events");
  }
  return res.json();
}

export async function createCalendarEvent(
  body: CreateEventRequest
): Promise<CalendarEvent> {
  const res = await fetch(`${BASE_URL}/calendar/events`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail ?? "Failed to create event");
  }
  return res.json();
}

export async function deleteCalendarEvent(
  credentialId: string,
  eventId: string
): Promise<void> {
  const res = await fetch(
    `${BASE_URL}/calendar/events/${credentialId}/${eventId}`,
    { method: "DELETE" }
  );
  if (!res.ok && res.status !== 204) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail ?? "Failed to delete event");
  }
}

export async function getAvailability(
  credentialId: string,
  start: string,
  end: string,
  timezone = "UTC"
): Promise<AvailabilityResponse> {
  const params = new URLSearchParams({ start, end, timezone });
  const res = await fetch(
    `${BASE_URL}/calendar/availability/${credentialId}?${params}`
  );
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail ?? "Failed to fetch availability");
  }
  return res.json();
}
```

---

## 5. Next.js API Routes (proxy layer)

Create the following proxy routes so the browser never calls the automation server directly.

### `app/api/calendar/credentials/route.ts`

```typescript
import { NextResponse } from "next/server";
import { requireSession } from "@/lib/auth/guards";
import type { CalendarCredentialIn } from "@/lib/automationApi";

const AUTOMATION_URL = process.env.AUTOMATION_API_URL ?? "http://localhost:8001";

export async function POST(request: Request) {
  const auth = await requireSession();
  if ("response" in auth) return auth.response;

  const body: CalendarCredentialIn = await request.json();

  const upstream = await fetch(`${AUTOMATION_URL}/calendar/credentials`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await upstream.json();
  return NextResponse.json(data, { status: upstream.status });
}
```

### `app/api/calendar/events/route.ts`

```typescript
import { NextResponse } from "next/server";
import { requireSession } from "@/lib/auth/guards";
import type { CreateEventRequest } from "@/lib/automationApi";

const AUTOMATION_URL = process.env.AUTOMATION_API_URL ?? "http://localhost:8001";

// GET /api/calendar/events?credentialId=xxx&days=7
export async function GET(request: Request) {
  const auth = await requireSession();
  if ("response" in auth) return auth.response;

  const { searchParams } = new URL(request.url);
  const credentialId = searchParams.get("credentialId");
  const days = searchParams.get("days") ?? "7";

  if (!credentialId)
    return NextResponse.json({ message: "credentialId required" }, { status: 400 });

  const upstream = await fetch(
    `${AUTOMATION_URL}/calendar/events/${credentialId}?days=${days}`
  );
  const data = await upstream.json();
  return NextResponse.json(data, { status: upstream.status });
}

// POST /api/calendar/events
export async function POST(request: Request) {
  const auth = await requireSession();
  if ("response" in auth) return auth.response;

  const body: CreateEventRequest = await request.json();

  const upstream = await fetch(`${AUTOMATION_URL}/calendar/events`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await upstream.json();
  return NextResponse.json(data, { status: upstream.status });
}
```

### `app/api/calendar/events/[credentialId]/[eventId]/route.ts`

```typescript
import { NextResponse } from "next/server";
import { requireSession } from "@/lib/auth/guards";

const AUTOMATION_URL = process.env.AUTOMATION_API_URL ?? "http://localhost:8001";

export async function DELETE(
  _request: Request,
  { params }: { params: { credentialId: string; eventId: string } }
) {
  const auth = await requireSession();
  if ("response" in auth) return auth.response;

  const upstream = await fetch(
    `${AUTOMATION_URL}/calendar/events/${params.credentialId}/${params.eventId}`,
    { method: "DELETE" }
  );
  return new NextResponse(null, { status: upstream.status });
}
```

### `app/api/calendar/availability/route.ts`

```typescript
import { NextResponse } from "next/server";
import { requireSession } from "@/lib/auth/guards";

const AUTOMATION_URL = process.env.AUTOMATION_API_URL ?? "http://localhost:8001";

// GET /api/calendar/availability?credentialId=xxx&start=...&end=...&timezone=UTC
export async function GET(request: Request) {
  const auth = await requireSession();
  if ("response" in auth) return auth.response;

  const { searchParams } = new URL(request.url);
  const credentialId = searchParams.get("credentialId");
  const start = searchParams.get("start");
  const end = searchParams.get("end");
  const timezone = searchParams.get("timezone") ?? "UTC";

  if (!credentialId || !start || !end)
    return NextResponse.json(
      { message: "credentialId, start, and end are required" },
      { status: 400 }
    );

  const params = new URLSearchParams({ start, end, timezone });
  const upstream = await fetch(
    `${AUTOMATION_URL}/calendar/availability/${credentialId}?${params}`
  );
  const data = await upstream.json();
  return NextResponse.json(data, { status: upstream.status });
}
```

---

## 6. React Components

### `components/CalendarCredentialForm.tsx`

A modal or settings-panel form for connecting a calendar account.

**Props:**
```typescript
type Props = {
  onSuccess: (credentialId: string) => void;
};
```

**Form fields:**
- `credential_id` — text input (user-chosen unique ID, e.g. `"cal-john-google"`)
- `provider` — `<select>` with options `google` / `microsoft`
- `email_address` — text input
- `display_name` — text input (optional)
- `oauth.client_id`, `oauth.client_secret` — text inputs
- `oauth.access_token`, `oauth.refresh_token` — text inputs (password type)
- `oauth.token_expiry` — datetime-local input (defaults to 1 hour from now)
- `oauth.tenant_id` — text input, shown only when `provider === "microsoft"`

On submit:
```typescript
const res = await fetch("/api/calendar/credentials", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(formValues),
});
if (res.ok) onSuccess(formValues.credential_id);
```

---

### `components/AvailabilityPicker.tsx`

Shows a day view with 30-min slots colour-coded free (green) / busy (red).

**Props:**
```typescript
type Props = {
  credentialId: string;
  date: Date;                              // which day to show
  onSlotSelect: (start: string, end: string) => void;  // ISO 8601 strings
};
```

**Behaviour:**
1. On mount (and when `date` changes), fetch:
   ```typescript
   const dayStart = new Date(date);
   dayStart.setHours(0, 0, 0, 0);
   const dayEnd = new Date(date);
   dayEnd.setHours(23, 59, 59, 0);

   const data = await fetch(
     `/api/calendar/availability?credentialId=${credentialId}` +
     `&start=${dayStart.toISOString()}&end=${dayEnd.toISOString()}`
   ).then(r => r.json());
   // data.slots — array of { start, end, is_free }
   ```
2. Render each slot as a row. Free slots are clickable; busy slots are dimmed.
3. On click of a free slot call `onSlotSelect(slot.start, slot.end)`.

---

### `components/CreateEventModal.tsx`

A modal triggered from `AvailabilityPicker` or from a standalone "Schedule Meeting" button.

**Props:**
```typescript
type Props = {
  credentialId: string;
  defaultStart?: string;   // pre-filled from slot selection
  defaultEnd?: string;
  defaultAttendeeEmail?: string;
  defaultAttendeeeName?: string;
  onCreated: (event: CalendarEvent) => void;
  onClose: () => void;
};
```

**Form fields:**
- `title` — text input (required)
- `description` — textarea
- `start_datetime` — datetime-local (pre-filled from `defaultStart`)
- `end_datetime` — datetime-local (pre-filled from `defaultEnd`, default +30 min)
- `timezone` — `<select>` (common timezones, default browser timezone)
- `add_meeting_link` — checkbox, default checked — label: "Add Google Meet / Teams link"
- **Attendees table** — rows of `email` + `name`, "+ Add attendee" button
  - Pre-populate one row from `defaultAttendeeEmail` / `defaultAttendeeName`

On submit:
```typescript
const res = await fetch("/api/calendar/events", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({
    credential_id: credentialId,
    title,
    description,
    start_datetime: new Date(startDatetime).toISOString(),
    end_datetime: new Date(endDatetime).toISOString(),
    timezone,
    attendees,
    add_meeting_link: addMeetingLink,
  }),
});
const event: CalendarEvent = await res.json();
onCreated(event);
```

After creation, show a success toast with:
- A **"Copy Meet Link"** button (if `event.meeting_link` exists)
- A **"View in Calendar"** link (if `event.calendar_link` exists)

---

### `components/CalendarEventCard.tsx`

A small card shown in the events list.

```typescript
type Props = { event: CalendarEvent; onDelete: (eventId: string) => void };
```

Render:
- Event title (bold)
- Start → End time (formatted, e.g. "Fri 24 May · 10:00 – 10:30")
- Attendee avatars / emails
- If `meeting_link`: a **"Join"** button (opens link in new tab)
- If `calendar_link`: a **"View"** link
- A trash icon — on click calls:
  ```typescript
  await fetch(`/api/calendar/events/${credentialId}/${event.event_id}`, {
    method: "DELETE",
  });
  onDelete(event.event_id);
  ```

---

### `components/CalendarPage.tsx` (or `app/(dashboard)/calendar/page.tsx`)

The main calendar page.

**Sections:**

1. **Connected Calendar** — show connected credential (from props / server fetch).
   A "Connect Calendar" button opens `<CalendarCredentialForm>`.

2. **Upcoming Events** — fetched on mount:
   ```typescript
   const events = await fetch(
     `/api/calendar/events?credentialId=${credentialId}&days=14`
   ).then(r => r.json());
   ```
   Renders a list of `<CalendarEventCard>` components.

3. **Availability View** — a date picker + `<AvailabilityPicker>`.
   Clicking a free slot opens `<CreateEventModal>` pre-filled with the slot times.

4. **"Schedule Meeting" button** — always visible, opens `<CreateEventModal>` with empty defaults.

---

## 7. Connecting Calendar to Email Outreach

Update `EmailOutreachComposer` (from `DASHBOARD_INTEGRATION.md`) to accept a
`calendarCredentialId` prop and pass it to the send request.

**Updated Props:**
```typescript
type Props = {
  clientId: string;
  emailAccounts: { credential_id: string; email_address: string; provider: string }[];
  // NEW — optional calendar credential; when set, a meeting is auto-created on reply
  calendarAccounts: { credential_id: string; email_address: string; provider: string }[];
};
```

**Stage 1 (compose) — add one new field:**
- **"Auto-create meeting on reply"** — `<select>` populated from `calendarAccounts`.
  Default: `""` (disabled). Label: *"When the lead replies, automatically create a meeting using:"*.

**Updated "Send All" call:**
```typescript
const res = await fetch("/api/outreach/send", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({
    credential_id: selectedCredentialId,
    recipients: recipientsForSend,
    generated_emails: generatedEmails,
    calendar_credential_id: selectedCalendarCredentialId || undefined,
  }),
});
```

---

## 8. Showing Meeting Links on the Leads / Tracking Page

The `GET /track/status/{credential_id}` response now includes `meeting_link` on each record
where a reply was detected and a calendar credential was attached.

On your leads table, add a **"Meeting"** column:
```typescript
// TrackingRecord shape (from GET /track/status/:id)
type TrackingRecord = {
  tracking_id: string;
  recipient_email: string;
  recipient_name: string;
  subject: string;
  sent_at: string;
  opened: boolean;
  opened_at: string | null;
  replied: boolean;
  replied_at: string | null;
  reply_subject: string | null;
  reply_preview: string | null;
  meeting_link: string | null;   // ← Google Meet or Teams URL (auto-created on reply)
  calendar_credential_id: string | null;
};
```

Render in the table:
```tsx
{record.meeting_link ? (
  <a href={record.meeting_link} target="_blank" rel="noopener noreferrer">
    <Button size="sm" variant="outline">Join Meeting</Button>
  </a>
) : record.replied ? (
  <span className="text-muted-foreground text-xs">No meeting</span>
) : null}
```

---

## 9. Data Flow Diagram

```
Dashboard (Next.js)                    Automation API (FastAPI)
────────────────────────────────       ────────────────────────────────────────
User connects calendar account
        │
        ▼
POST /api/calendar/credentials ──────► POST /calendar/credentials
                                          └─ upsert into calendar_db_client (MongoDB)

User sends email with calendar_credential_id attached
        │
        ▼
POST /api/outreach/send        ──────► POST /outreach/send
                                          └─ stores calendar_credential_id
                                             in email_tracking record

Every 2 hours (background scheduler):
                               ──────► reply check runs
                                          ├─ detects reply in Gmail thread / Outlook conversation
                                          ├─ calls calendar_service.create_event(...)
                                          │    ├─ Google: POST /calendar/v3/calendars/primary/events
                                          │    │          + conferenceData → Meet link
                                          │    └─ Microsoft: POST /graph/v1.0/me/events
                                          │                  + isOnlineMeeting → Teams link
                                          └─ stores meeting_link in email_tracking

User views Leads page
        │
        ▼
GET /api/track/status/:credId  ──────► GET /track/status/{credential_id}
                                          └─ returns tracking records with meeting_link

User checks availability
        │
        ▼
GET /api/calendar/availability ──────► GET /calendar/availability/{credential_id}
  ?credentialId=...                       ├─ Google: POST /calendar/v3/freeBusy
  &start=...&end=...                      └─ Microsoft: POST /graph/v1.0/me/calendar/getSchedule
                             ◄──────── list of 30-min slots (is_free: true/false)

User creates event manually
        │
        ▼
POST /api/calendar/events      ──────► POST /calendar/events
                                          ├─ Google: creates event + Meet link
                                          └─ Microsoft: creates event + Teams link
                             ◄──────── { event_id, meeting_link, calendar_link, ... }
```

---

## 10. Key Field Mapping

| Dashboard concept | API field | Notes |
|---|---|---|
| Calendar account | `credential_id` | Stored in `calendar_db_client` — separate from email `project_db_client` |
| Provider | `provider` | `"google"` or `"microsoft"` |
| Meeting link | `meeting_link` | Google Meet URL or Teams join URL — returned on event create and on reply detection |
| Calendar link | `calendar_link` | Direct link to view/accept event in Google Calendar / Outlook web |
| Auto-meeting on reply | `calendar_credential_id` on send request | Pass the calendar `credential_id` when sending emails; leave empty to disable |
| 30-min free/busy slot | `AvailabilitySlot` | `is_free: true` = slot available; clicking it pre-fills `CreateEventModal` |

---

## 11. Required OAuth Scopes

The user must authorise the calendar credential with these scopes.
These are **additional** to the email scopes already in `project_db_client`.

| Provider | Required scopes |
|---|---|
| Google | `https://www.googleapis.com/auth/calendar.events` |
| Microsoft | `Calendars.ReadWrite` and `OnlineMeetings.ReadWrite` |

Getting tokens:
- **Google** — use [OAuth Playground](https://developers.google.com/oauthplayground), select the calendar scope, exchange for tokens.
- **Microsoft** — use [Graph Explorer](https://developer.microsoft.com/en-us/graph/graph-explorer), sign in, copy the access token from the "Access token" tab. For a refresh token you need an Azure app registration with `offline_access` scope.

---

## 12. Error Handling

All calendar proxy routes return the same error shape:
```json
{ "detail": "human readable error message" }
```

| HTTP status | `detail` | Fix |
|---|---|---|
| 404 | `Calendar credential not found: xxx` | Wrong `credential_id` or credential not stored yet |
| 502 | `Calendar API error: 401 Unauthorized` | Access token expired — re-store credential with a fresh token |
| 502 | `Calendar API error: 403 Forbidden` | Calendar scope not granted — re-authorise with the correct scopes |
| 502 | `Calendar API error: 409 Conflict` | Event already exists (Google) — safe to ignore |

---

## 13. Checklist for Cursor

- [ ] Append calendar types and helpers to `lib/automationApi.ts` (sections 3–4)
- [ ] Create `app/api/calendar/credentials/route.ts` (section 5)
- [ ] Create `app/api/calendar/events/route.ts` (section 5)
- [ ] Create `app/api/calendar/events/[credentialId]/[eventId]/route.ts` (section 5)
- [ ] Create `app/api/calendar/availability/route.ts` (section 5)
- [ ] Create `components/CalendarCredentialForm.tsx` (section 6)
- [ ] Create `components/AvailabilityPicker.tsx` (section 6)
- [ ] Create `components/CreateEventModal.tsx` (section 6)
- [ ] Create `components/CalendarEventCard.tsx` (section 6)
- [ ] Create `app/(dashboard)/calendar/page.tsx` using the above components (section 6)
- [ ] Update `EmailOutreachComposer` to accept `calendarAccounts` prop and pass `calendar_credential_id` to the send call (section 7)
- [ ] Add `meeting_link` column + "Join Meeting" button to the leads/tracking table (section 8)
- [ ] Fetch calendar credentials server-side and pass to `EmailOutreachComposer` as `calendarAccounts`
