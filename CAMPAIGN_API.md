# Background Campaign Email API

Send thousands of AI-personalized emails without blocking the client.  
The dashboard fires one request, gets a `campaign_id` back immediately, then polls for progress.

---

## Base URL

```
http://localhost:8001
```

---

## Endpoints

### 1. Launch a Campaign

**`POST /outreach/campaigns`**

Start a background campaign. Returns immediately (< 200ms) — emails are generated and sent one by one in the background.

**Request Body**

```json
{
  "credential_id": "client_abc_gmail",
  "campaign_description": "Outreach to SaaS founders about our AI analytics tool",
  "recipients": [
    {
      "email": "john@example.com",
      "name": "John Smith",
      "description": "CEO of a 50-person SaaS startup, recently raised Series A"
    },
    {
      "email": "sara@acme.com",
      "name": "Sara Lee",
      "description": "Head of Growth at Acme Corp, focused on reducing churn"
    }
  ],
  "knowledge_base_id": "client_abc_kb",
  "from_name": "Alex from Acme",
  "reply_to": "alex@acme.com",
  "cc": [],
  "bcc": [],
  "calendar_credential_id": ""
}
```

| Field | Required | Description |
|---|---|---|
| `credential_id` | Yes | Email account credential stored in MongoDB |
| `campaign_description` | Yes | What the campaign is about — used by AI to write emails |
| `recipients` | Yes | List of recipients. Each must have `email`; `name` and `description` improve personalization |
| `knowledge_base_id` | No | Qdrant collection name for product/company context |
| `from_name` | No | Sender display name |
| `reply_to` | No | Reply-to email address |
| `cc` / `bcc` | No | CC/BCC addresses applied to every email |
| `calendar_credential_id` | No | Auto-create calendar meeting when lead replies |

**Response — 202 Accepted**

```json
{
  "campaign_id": "f3a2b1c0-1234-5678-abcd-ef0123456789",
  "status": "running",
  "total": 2
}
```

Store `campaign_id` in your dashboard — you need it to poll progress and stop the campaign.

---

### 2. Poll Campaign Progress

**`GET /outreach/campaigns/{campaign_id}`**

Call this every few seconds to update the progress bar on your dashboard.

**Response — 200 OK**

```json
{
  "campaign_id": "f3a2b1c0-1234-5678-abcd-ef0123456789",
  "status": "running",
  "total": 1000,
  "sent": 42,
  "failed": 1,
  "current_index": 43,
  "started_at": "2026-06-02T10:00:00.000000+00:00",
  "completed_at": null,
  "results": [
    {
      "recipient_email": "john@example.com",
      "recipient_name": "John Smith",
      "provider": "gmail",
      "status": "sent",
      "subject": "Quick question about your analytics stack",
      "message_id": "18f3a2b1c0d4e5f6",
      "tracking_id": "uuid-here",
      "sent_at": "2026-06-02T10:00:05.123456+00:00",
      "error": null
    }
  ]
}
```

**`status` values**

| Value | Meaning |
|---|---|
| `running` | Campaign is active — keep polling |
| `completed` | All recipients processed |
| `stopped` | Stopped by API request |
| `failed` | Fatal error (e.g. credential not found) |

**Recommended polling interval:** every 3–5 seconds while `status === "running"`.

---

### 3. Stop a Campaign

**`POST /outreach/campaigns/{campaign_id}/stop`**

Sends a stop signal. The current recipient finishes sending, then the campaign halts. No email is cut off mid-send.

**Response — 200 OK**

```json
{
  "campaign_id": "f3a2b1c0-1234-5678-abcd-ef0123456789",
  "status": "stopping",
  "message": "Stop signal sent. Poll GET /outreach/campaigns/{id} to confirm."
}
```

After receiving `stopping`, keep polling `GET /outreach/campaigns/{id}` until `status === "stopped"`.

**If campaign already finished:**

```json
{
  "campaign_id": "f3a2b1c0-...",
  "status": "completed",
  "message": "Campaign already finished"
}
```

**If campaign_id not found — 404**

```json
{ "detail": "'f3a2b1c0-...' not found" }
```

---

## Dashboard Integration Flow

```
User clicks "Send Campaign"
        │
        ▼
POST /outreach/campaigns
        │
        ▼
Store campaign_id, show progress bar (0 / total)
        │
        ▼
Every 3s: GET /outreach/campaigns/{id}
        │
        ├─ status = "running"  → update progress bar (sent / total), keep polling
        ├─ status = "completed" → show success summary, stop polling
        ├─ status = "stopped"   → show stopped message, stop polling
        └─ status = "failed"    → show error, stop polling

User clicks "Stop"
        │
        ▼
POST /outreach/campaigns/{id}/stop
        │
        ▼
Keep polling until status = "stopped"
```

---

## Progress Bar Formula

```js
const percent = Math.round((campaign.current_index / campaign.total) * 100);
// current_index = number of recipients fully processed (sent OR failed)
// sent + failed = current_index
```

---

## n8n Integration Example

**Node 1 — HTTP Request (Launch)**
- Method: `POST`
- URL: `http://localhost:8001/outreach/campaigns`
- Body: JSON with credential_id, campaign_description, recipients
- Save `campaign_id` from response

**Node 2 — Wait**
- Wait 5 seconds

**Node 3 — HTTP Request (Poll)**
- Method: `GET`
- URL: `http://localhost:8001/outreach/campaigns/{{ $json.campaign_id }}`

**Node 4 — IF**
- Condition: `status` equals `running`
- True → back to Node 2 (loop)
- False → continue to success/error handler

---

## Error Handling

| Scenario | Behavior |
|---|---|
| Credential not found | Campaign status set to `failed` immediately |
| One recipient fails | Logged in `results` with `status: "failed"`, campaign continues |
| Token expires mid-campaign | Auto-refreshed before each send |
| Server restarts mid-campaign | Campaign stays `running` in DB but task is gone — use stop endpoint or re-launch |

> **Note:** If the server restarts while a campaign is running, the MongoDB document will remain `status: "running"` but no task is processing it. The dashboard should detect this (e.g. `current_index` not incrementing for > 60s) and offer a "Re-launch" option or call the stop endpoint to mark it cleanly.

---

## Campaign Result Fields

Each object in `results[]`:

| Field | Type | Description |
|---|---|---|
| `recipient_email` | string | Recipient email address |
| `recipient_name` | string | Recipient name |
| `provider` | string | `"gmail"` or `"outlook"` |
| `status` | string | `"sent"` or `"failed"` |
| `subject` | string | AI-generated subject line |
| `message_id` | string \| null | Provider message ID (Gmail only) |
| `tracking_id` | string \| null | UUID for open-tracking pixel |
| `sent_at` | string | ISO 8601 UTC timestamp |
| `error` | string \| null | Error message if `status = "failed"` |
