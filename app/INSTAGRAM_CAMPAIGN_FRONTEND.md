  # Instagram Growth **Campaigns** — Frontend Integration Guide

  > Sibling doc: `INSTAGRAM_GROWTH_FRONTEND.md` covers the lower-level
  > recommend / follow-task APIs. **This doc covers the high-level Campaign flow.**

  ---

  ## What is it? (the concept)

  A **Campaign** is the "hands-off" growth flow. Instead of supplying competitor links
  or a username list, the operator gives **only a product description**. The backend
  then does everything in the background:

  ```
  product description
          │
          ▼
  1. DISCOVER   derive keywords → find competitor Instagram accounts
          ▼
  2. HARVEST    scan each competitor's recent posts → collect the users who commented
          ▼
  3. FOLLOW     log in once → follow those users (up to `total_follows`),
                with a delay between each action and a daily cap
          ▼
    progress + results readable at any time
  ```

  The frontend's job is small:
  1. Collect a **product description** + a few options, `POST /growth/campaigns`.
  2. Get back a `campaign_id` immediately (the work runs in the background).
  3. Poll **progress** to show a live "followed X of Y" bar.
  4. Show **results** (competitors found + everyone followed) when done.
  5. Optionally **cancel**.

  - Base URL: `${API_BASE}` (e.g. `http://localhost:8000`)
  - Content type: `application/json`
  - Interactive docs: `${API_BASE}/docs`

  > **Current scope:** candidate source is **post commenters** (the `sources` field is
  > built to grow); pacing uses a per-action delay + daily cap. Full human-like
  > randomized multi-day scheduling and follower-list harvesting are planned next —
  > the API shape (`days`, `sources`) already anticipates them, so the frontend
  > contract won't change when they land.

  ---

  ## Endpoint summary

  | # | Method & path | Purpose | Returns |
  |---|---------------|---------|---------|
  | 1 | `POST /growth/campaigns` | Start a campaign from a description | `202` `{ campaign_id, ... }` immediately |
  | 2 | `GET /growth/campaigns/{id}` | Progress (stage + how many followed) | `200` status + counters |
  | 3 | `GET /growth/campaigns/{id}/results` | Full results (competitors + followed users) | `200` |
  | 4 | `POST /growth/campaigns/{id}/cancel` | Stop a running campaign | `200` acknowledgement |

  ---

  ## 1. Start a campaign — `POST /growth/campaigns` → `202`

  Returns **immediately**. Store the `campaign_id` and poll #2.

  ### Request body

  ```jsonc
  {
    "product_description": "organic vegan skincare for sensitive skin", // required, min 3 chars
    "credential_id": "acct_123",   // required — which IG account follows (see "Credentials")
    "total_follows": 50,           // 1–500, how many users to follow this campaign
    "days": 2,                     // 1–30, intended spread (multi-day pacing = later milestone)
    "sources": ["commenters"],     // candidate sources; "commenters" supported today
    "n_posts": 5,                  // 1–20, recent posts per competitor to scan
    "competitor_limit": 10         // optional, max competitors (default from server config)
  }
  ```

  | Field | Type | Required | Default | Notes |
  |-------|------|----------|---------|-------|
  | `product_description` | string | yes | — | min length 3 |
  | `credential_id` | string | yes | — | id of a stored IG account |
  | `total_follows` | int | no | `50` | 1–500 |
  | `days` | int | no | `1` | 1–30 |
  | `sources` | string[] | no | `["commenters"]` | currently only `commenters` |
  | `n_posts` | int | no | `5` | 1–20 |
  | `competitor_limit` | int \| null | no | `null` | 1–50 |

  ### Response `202`

  ```jsonc
  { "campaign_id": "c1a2b3c4-...", "status": "running", "total_target": 50 }
  ```

  ### Example (TypeScript / fetch)

  ```ts
  async function startCampaign(body: {
    product_description: string;
    credential_id: string;
    total_follows?: number;
    days?: number;
    sources?: string[];
    n_posts?: number;
    competitor_limit?: number;
  }) {
    const res = await fetch(`${API_BASE}/growth/campaigns`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!res.ok) throw new Error((await res.json()).detail ?? res.statusText);
    return res.json(); // { campaign_id, status, total_target }
  }
  ```

  ---

  ## 2. Progress — `GET /growth/campaigns/{id}`

  Poll every 3–5s until `status` is terminal. `404` if unknown.

  ### Response `200`

  ```jsonc
  {
    "campaign_id": "c1a2b3c4-...",
    "status": "running",          // "running" | "completed" | "stopped" | "failed"
    "stage": "following",         // "discovering" | "harvesting" | "following" | "done"
    "total_target": 50,
    "competitors_found": 8,
    "candidates_found": 47,
    "done": 12,                   // users acted on so far
    "success": 11,
    "skipped": 0,
    "failed": 1,
    "current_index": 12,
    "started_at": "2026-06-30T10:00:00+00:00",
    "completed_at": null,         // ISO string once terminal
    "error": null                 // populated on "failed", or a soft note (e.g. "no competitors found")
  }
  ```

  **Stage → what to show the user**

  | Stage | Meaning | Suggested UI |
  |-------|---------|--------------|
  | `discovering` | finding competitors from the description | "Finding competitors…" spinner |
  | `harvesting` | collecting commenters from competitors | "Building your audience…" spinner |
  | `following` | actively following | progress bar `done / total_target` |
  | `done` | finished | final summary |

  **Status (terminal?)**

  | Value | Terminal? | Meaning |
  |-------|-----------|---------|
  | `running` | no | in progress |
  | `completed` | yes | finished (check `error` for soft notes like "no competitors found") |
  | `stopped` | yes | cancelled by the operator |
  | `failed` | yes | login/discovery/fatal error — see `error` |

  ### Example polling loop

  ```ts
  async function pollCampaign(id: string, onUpdate: (s: any) => void) {
    const TERMINAL = new Set(["completed", "stopped", "failed"]);
    while (true) {
      const res = await fetch(`${API_BASE}/growth/campaigns/${id}`);
      if (res.status === 404) throw new Error("Campaign not found");
      const s = await res.json();
      onUpdate(s);
      if (TERMINAL.has(s.status)) return s;
      await new Promise((r) => setTimeout(r, 4000));
    }
  }
  ```

  ---

  ## 3. Results — `GET /growth/campaigns/{id}/results`

  The full outcome: competitors discovered and every user followed. `404` if unknown.

  ### Response `200`

  ```jsonc
  {
    "campaign_id": "c1a2b3c4-...",
    "status": "completed",
    "product_description": "organic vegan skincare for sensitive skin",
    "total_target": 50,
    "competitors": [
      { "username": "rival_brand", "score": 100, "reason": "appears in 4 post(s) under matched keywords", "followers": 0 }
    ],
    "results": [
      { "username": "jane_doe", "action": "follow", "status": "success", "user_id": "17841400000", "message": "" },
      { "username": "spam_acct", "action": "follow", "status": "error",   "user_id": "",            "message": "user not found" }
    ],
    "success": 48,
    "skipped": 0,
    "failed": 2,
    "started_at": "2026-06-30T10:00:00+00:00",
    "completed_at": "2026-06-30T10:42:00+00:00"
  }
  ```

  Per-user `status`: `success` (followed), `skipped` (daily cap hit / already following),
  `error` (per-user failure — see `message`).

  ---

  ## 4. Cancel — `POST /growth/campaigns/{id}/cancel`

  Cooperative stop: halts **before the next follow** (no mid-action interruption). Keep
  polling #2 until `status === "stopped"`.

  ```jsonc
  // was running
  { "campaign_id": "...", "status": "stopping", "message": "Stop signal sent. Poll GET /growth/campaigns/{id} to confirm." }
  // already finished
  { "campaign_id": "...", "status": "completed", "message": "Campaign is not running" }
  ```

  `404` if unknown.

  ---

  ## Typical UI flow

  ```
  [Form] product description + total_follows (+ advanced: days, n_posts, competitor_limit)
          │  POST /growth/campaigns                → campaign_id (instant)
          ▼
  [Progress] stage label + "followed {done} / {total_target}"
          │  GET /growth/campaigns/{id}            (poll ~4s)
          │  [optional] POST .../cancel
          ▼
  [Results] competitors discovered + list of followed users
            GET /growth/campaigns/{id}/results
  ```

  ---

  ## Credentials — collection & schema

  The campaign follows from a stored Instagram account. The frontend **never sends a
  password** — it only passes the opaque `credential_id`. Accounts live server-side in
  MongoDB.

  - **Database:** the app's Mongo database (env `MONGODB_DB_NAME`, default `project_db`)
  - **Collection:** `instagram_accounts`
  - **Lookup key:** `credential_id` (unique)

  ### Document schema

  ```jsonc
  // collection: instagram_accounts
  {
    "credential_id": "acct_123",        // string, REQUIRED — the id the API receives
    "username": "my_brand_ig",          // string, REQUIRED — the IG handle to log in as
    "password": "<fernet-encrypted>",   // string — encrypted with TREND_FERNET_KEY (see below)
    "totp_seed": "<fernet-encrypted>",  // string, OPTIONAL — 2FA TOTP secret (encrypted)
    "session": { /* instagrapi settings blob */ } // object, OPTIONAL — preferred over password
  }
  ```

  | Field | Type | Required | Notes |
  |-------|------|----------|-------|
  | `credential_id` | string | ✅ | unique; what every API call references |
  | `username` | string | ✅ | Instagram login handle |
  | `password` | string | ✅ unless `session` present | Fernet-encrypted; field name `enc_password` is also accepted |
  | `totp_seed` | string | optional | Fernet-encrypted 2FA seed; enables automatic 2FA |
  | `session` | object | optional | instagrapi session settings; **preferred** — avoids repeat logins and reduces lockouts |

  ### Encryption rules

  - If `TREND_FERNET_KEY` is set on the server, `password` and `totp_seed` are
    **decrypted with Fernet** at use time. Store them encrypted with the same key.
  - If decryption fails (or no key is configured), the stored value is treated as
    **plaintext** (eases local/dev migration). For production, **always set
    `TREND_FERNET_KEY` and store ciphertext.**
  - `session` is stored as-is (not encrypted); treat the whole collection as secret.

  ### Generating values (server-side helper, not frontend)

  ```python
  from cryptography.fernet import Fernet
  key = Fernet.generate_key()                 # → set as TREND_FERNET_KEY
  enc = Fernet(key).encrypt(b"the_password")  # → store as "password"
  ```

  > There is **no API to create credentials yet** — they're inserted server-side (script
  > or admin tool). If you need an "Add account" screen, ask backend to expose a
  > credentials endpoint; this campaign API only *consumes* `credential_id`.

  ---

  ## Error handling cheat sheet

  | Status | When | Frontend action |
  |--------|------|-----------------|
  | `404` | unknown `campaign_id` on progress/results/cancel | stop polling, show "not found" |
  | `422` | body fails schema (missing description/credential_id, out-of-range numbers) | show field errors from `detail` |
  | `5xx` | server error | retry; campaign-level failures surface as `status:"failed"` with `error` |

  For schema (`422`) errors FastAPI returns `{ "detail": [ ... ] }`; for explicit
  `404`s `detail` is a plain string.

  ---

  ## Notes & gotchas

  - **Everything after start is async.** Never block the UI on the campaign; rely on
    the `campaign_id` + polling.
  - **`completed` can still carry a soft `error`** (e.g. "no competitors found",
    "no candidate users harvested") — show it as an info message, not a crash.
  - **Counters/competitors come from the results/progress reads**, which are the source
    of truth (MongoDB `instagram_campaigns`), not from the start response.
  - **Following is throttled by design** (per-action delay + daily cap), so a large
    `total_follows` legitimately takes time — that's why it's a background campaign.
  - **Cancel is cooperative**, not instant — confirm via polling.
