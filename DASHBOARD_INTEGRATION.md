# Dashboard Integration Guide — AI Email Outreach

This document describes how to integrate the **Automation API** (FastAPI + LangGraph)
into the existing Next.js dashboard. Hand this file to Cursor and ask it to implement
the changes described below.

---

## 1. Overview

The automation backend runs at `http://localhost:8000` (configurable via env var).
It exposes two endpoints for AI-powered email outreach:

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/outreach/preview` | Generate personalised email drafts using AI (no send) |
| `POST` | `/outreach/send` | Send emails — three modes (template / AI-generate / pre-generated) |

### Full UI flow
1. User fills in a **Campaign Form**: picks a connected email account, writes a campaign
   description, optionally selects a knowledge base, and adds recipients with descriptions.
2. User clicks **"Generate Preview"** → calls `/outreach/preview` → drafts appear per recipient.
3. User reviews / edits each draft.
4. User clicks **"Send All"** → calls `/outreach/send` with the approved drafts.

---

## 2. Environment Variable

Add to the dashboard `.env.local` (or `.env`):

```env
AUTOMATION_API_URL=http://localhost:8000
```

In production replace with the deployed URL of the automation server.

---

## 3. TypeScript Types

Create `lib/automationApi.ts` in the dashboard with the following types and helper functions.

```typescript
// lib/automationApi.ts

const BASE_URL = process.env.AUTOMATION_API_URL ?? "http://localhost:8000";

// ── Request types ──────────────────────────────────────────────────────────────

export type RecipientInput = {
  email: string;
  name?: string;
  /** Recipient background / role used by the AI to personalise the email. */
  description?: string;
};

export type PreviewRequest = {
  credential_id: string;          // accountId from the connected email credential
  campaign_description: string;   // what the campaign is about
  knowledge_base_id?: string;     // Qdrant collection name — usually the clientId
  recipients: RecipientInput[];
};

export type SendRequest = {
  credential_id: string;
  recipients: RecipientInput[];
  from_name?: string;
  reply_to?: string;
  cc?: string[];
  bcc?: string[];

  // ── Content mode — provide exactly ONE of the three below ──────────────────

  // Mode A: send pre-generated drafts from /outreach/preview
  generated_emails?: GeneratedEmail[];

  // Mode B: let the backend generate + send in one shot
  campaign_description?: string;
  knowledge_base_id?: string;

  // Mode C: classic template ({{name}} is replaced per recipient)
  subject?: string;
  body_html?: string;
  body_text?: string;
};

// ── Response types ─────────────────────────────────────────────────────────────

export type GeneratedEmail = {
  recipient_email: string;
  recipient_name: string;
  subject: string;
  body_html: string;
  body_text: string;
};

export type PreviewResponse = {
  emails: GeneratedEmail[];
};

export type SendResult = {
  recipient_email: string;
  recipient_name: string;
  provider: string;
  status: "sent" | "failed";
  message_id: string | null;
  subject: string;
  error: string | null;
  sent_at: string;
};

export type SendResponse = {
  success: boolean;
  summary: { total: number; sent: number; failed: number };
  results: SendResult[];
};

// ── API helpers ────────────────────────────────────────────────────────────────

export async function previewOutreach(req: PreviewRequest): Promise<PreviewResponse> {
  const res = await fetch(`${BASE_URL}/outreach/preview`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail ?? "Preview failed");
  }
  return res.json();
}

export async function sendOutreach(req: SendRequest): Promise<SendResponse> {
  const res = await fetch(`${BASE_URL}/outreach/send`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail ?? "Send failed");
  }
  return res.json();
}
```

---

## 4. Next.js API Routes (proxy layer)

Create two thin API routes so the browser never calls the automation server directly.

### `app/api/outreach/preview/route.ts`

```typescript
import { NextResponse } from "next/server";
import { requireSession } from "@/lib/auth/guards";
import { hasClientDataAccess } from "@/lib/auth/access";
import type { PreviewRequest } from "@/lib/automationApi";

const AUTOMATION_URL = process.env.AUTOMATION_API_URL ?? "http://localhost:8000";

export async function POST(request: Request) {
  const auth = await requireSession();
  if ("response" in auth) return auth.response;

  const body: PreviewRequest = await request.json();

  // Enforce client-scoped access
  if (auth.session.role === "client") {
    if (!auth.session.clientId)
      return NextResponse.json({ message: "No client linked." }, { status: 400 });
    if (!hasClientDataAccess(auth.session, auth.session.clientId))
      return NextResponse.json({ message: "Access denied." }, { status: 403 });
    // Force knowledge_base_id to the client's own collection
    body.knowledge_base_id = auth.session.clientId;
  }

  const upstream = await fetch(`${AUTOMATION_URL}/outreach/preview`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });

  const data = await upstream.json();
  return NextResponse.json(data, { status: upstream.status });
}
```

### `app/api/outreach/send/route.ts`

```typescript
import { NextResponse } from "next/server";
import { requireSession } from "@/lib/auth/guards";
import { hasClientDataAccess } from "@/lib/auth/access";
import type { SendRequest } from "@/lib/automationApi";

const AUTOMATION_URL = process.env.AUTOMATION_API_URL ?? "http://localhost:8000";

export async function POST(request: Request) {
  const auth = await requireSession();
  if ("response" in auth) return auth.response;

  const body: SendRequest = await request.json();

  if (auth.session.role === "client") {
    if (!auth.session.clientId)
      return NextResponse.json({ message: "No client linked." }, { status: 400 });
    if (!hasClientDataAccess(auth.session, auth.session.clientId))
      return NextResponse.json({ message: "Access denied." }, { status: 403 });
  }

  const upstream = await fetch(`${AUTOMATION_URL}/outreach/send`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });

  const data = await upstream.json();
  return NextResponse.json(data, { status: upstream.status });
}
```

---

## 5. React Component — `EmailOutreachComposer`

Create `components/EmailOutreachComposer.tsx`.

### Props

```typescript
type Props = {
  /** clientId of the current client — used to scope credentials and knowledge base */
  clientId: string;
  /** List of connected email accounts from GET /api/email-accounts?clientId=... */
  emailAccounts: { _id: string; credential_id: string; email_address: string; provider: string }[];
};
```

### Component behaviour

The component has **three stages** driven by a `stage` state variable:

#### Stage 1 — `"compose"` (default)
Show a form with:
- **"From account"** — `<select>` populated from `emailAccounts`. Value = `credential_id`.
- **"Campaign description"** — `<textarea>` (required). Describe what the campaign is about and
  what outcome you want. Example: *"We help B2B startups automate SDR outreach using AI. Reaching
  out to founders frustrated with manual email campaigns."*
- **"Knowledge base"** (optional) — pass `clientId` as the `knowledge_base_id`. This is already
  the Qdrant collection name for this client. You can either hardcode it or show a label like
  "Use my knowledge base" with a checkbox.
- **Recipients table** — an editable table where each row has:
  - `email` (required)
  - `name`
  - `description` — the most important field for personalisation. Examples:
    - *"CTO at a 15-person fintech startup, currently managing outreach manually"*
    - *"VP Sales at Series B company, team of 5 SDRs, low reply rates"*
  - A delete row button
- **"+ Add recipient"** button
- **"Generate Preview"** button (primary) — disabled while loading

On clicking **"Generate Preview"**:
```typescript
const res = await fetch("/api/outreach/preview", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({
    credential_id: selectedCredentialId,
    campaign_description: campaignDescription,
    knowledge_base_id: clientId,    // Qdrant collection = clientId
    recipients,
  }),
});
const data = await res.json();    // { emails: GeneratedEmail[] }
setGeneratedEmails(data.emails);
setStage("preview");
```

#### Stage 2 — `"preview"`
Show the generated drafts. For each recipient render:
- Recipient name + email as a header
- Editable `<input>` for subject
- Editable `<textarea>` (or rich text editor) for `body_html`
- A small "plain text" toggle to view `body_text`

Buttons:
- **"← Back"** — returns to stage `"compose"` (edits preserved)
- **"Send All"** (primary) — calls `/api/outreach/send`

On clicking **"Send All"**:
```typescript
// Build recipients list to match generated_emails order
const recipientsForSend = generatedEmails.map(e => ({
  email: e.recipient_email,
  name: e.recipient_name,
}));

const res = await fetch("/api/outreach/send", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({
    credential_id: selectedCredentialId,
    recipients: recipientsForSend,
    generated_emails: generatedEmails,   // pass back the (possibly edited) drafts
  }),
});
const result = await res.json();   // { success, summary, results }
setSendResult(result);
setStage("result");
```

#### Stage 3 — `"result"`
Show a summary card:
- Total / Sent / Failed counts from `result.summary`
- A per-recipient table showing `status`, `subject`, `sent_at`, and `error` (if any)
- **"Send another campaign"** button — resets everything to stage `"compose"`

---

## 6. Wiring the component into the dashboard

In whatever page manages outreach (e.g. `app/(dashboard)/outreach/page.tsx` or
`app/(dashboard)/clients/[id]/outreach/page.tsx`):

```typescript
import { EmailOutreachComposer } from "@/components/EmailOutreachComposer";

// Fetch connected email accounts server-side
const accounts = await fetch(
  `${process.env.NEXT_PUBLIC_APP_URL}/api/email-accounts?clientId=${clientId}`,
  { cache: "no-store" }
).then(r => r.json());

return (
  <EmailOutreachComposer
    clientId={clientId}
    emailAccounts={accounts}
  />
);
```

---

## 7. Data flow diagram

```
Dashboard (Next.js)                    Automation API (FastAPI + LangGraph)
────────────────────────────────       ──────────────────────────────────────────
User fills campaign form
        │
        ▼
POST /api/outreach/preview  ────────►  POST /outreach/preview
  (Next.js proxy route)                  │
                                         ├─ validate credential exists (MongoDB)
                                         ├─ fetch_knowledge_base
                                         │    └─ Qdrant semantic search
                                         │       (collection = knowledge_base_id)
                                         └─ generate_personalized_emails
                                              └─ per-recipient Qdrant search
                                                 + GPT-4o-mini → JSON email
                             ◄──────────
        │
        ▼
User reviews / edits drafts
        │
        ▼
POST /api/outreach/send     ────────►  POST /outreach/send
  (Next.js proxy route)                  │
                                         ├─ fetch_credentials (MongoDB)
                                         ├─ check_and_refresh_token (OAuth2)
                                         ├─ send_email per recipient
                                         │    ├─ Gmail API  (RFC 2822 + base64url)
                                         │    └─ Microsoft Graph API
                                         ├─ aggregate_results
                                         └─ log_activity (MongoDB email_activity_log)
                             ◄──────────
        │
        ▼
Show send results to user
```

---

## 8. Key field mapping

| Dashboard concept | API field | Notes |
|---|---|---|
| Selected email account | `credential_id` | Use `credential_id` (= `accountId`) from `/api/email-accounts` response, NOT the Mongo `_id` |
| Knowledge base | `knowledge_base_id` | Pass the **clientId** — this is the Qdrant collection name where the client's documents are indexed |
| Campaign description | `campaign_description` | Plain text; 2–5 sentences describing the goal and value proposition |
| Recipient background | `recipients[].description` | The more detail, the better the personalisation. Role, company size, pain point. |
| AI-generated drafts | `generated_emails` | Returned by `/outreach/preview`; passed back to `/outreach/send` |

---

## 9. Error handling

Both proxy routes return the same error shape as the upstream API:
```json
{ "detail": "human readable error message" }
```

Common errors and causes:

| HTTP status | `detail` | Fix |
|---|---|---|
| 404 | `Credential not found` | The `credential_id` is wrong or the credential was deleted |
| 400 | `campaign_description is required` | Empty campaign description sent to `/preview` |
| 500 | `LLM did not return valid JSON` | OpenAI API key invalid / quota exceeded |
| 500 | `Knowledge base unavailable` | Qdrant collection doesn't exist for this clientId — safe to ignore, generation still runs |

---

## 10. Checklist for Cursor

- [ ] Add `AUTOMATION_API_URL=http://localhost:8000` to `.env.local`
- [ ] Create `lib/automationApi.ts` (section 3)
- [ ] Create `app/api/outreach/preview/route.ts` (section 4)
- [ ] Create `app/api/outreach/send/route.ts` (section 4)
- [ ] Create `components/EmailOutreachComposer.tsx` (section 5)
  - Stage 1: compose form (credential picker, campaign description, recipients table with description column)
  - Stage 2: preview with editable subject + body per recipient
  - Stage 3: send results summary
- [ ] Mount `<EmailOutreachComposer>` on the relevant dashboard page (section 6)
- [ ] Fetch `emailAccounts` server-side via `GET /api/email-accounts?clientId=...` before rendering the composer
