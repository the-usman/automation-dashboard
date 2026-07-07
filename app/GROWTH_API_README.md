# Instagram Growth API — Frontend Handover

Everything the frontend needs to drive the Instagram growth features. This is the
**current** contract (updated for `sessionid` auth + Apify-free campaigns).

- **Base URL:** `${API_BASE}` (e.g. `http://localhost:8001`)
- **Content-Type:** `application/json`
- **Interactive docs:** `${API_BASE}/docs` (Swagger) — always the source of truth.

---

## 1. Concept

The main feature is a **Campaign**: the user gives only a **product description**,
and the backend, in the background:

```
description ──▶ 1. AUTHENTICATE (sessionid cookie)
             ──▶ 2. DISCOVER    keywords → competitor accounts (via hashtags)
             ──▶ 3. HARVEST     their posts' commenters + likers
             ──▶ 4. FOLLOW      those users (delay + daily cap)
```

**Key facts the frontend must know:**
- Auth is a browser **`sessionid` cookie**, sent per campaign. No password, no
  stored account required. The `sessionid` is **never stored** — it's used for that
  run and discarded when the campaign ends.
- Harvest signals are **commenters + likers**. Instagram does **not** expose who
  *shared* a post, so "shares" is not available — don't offer it in the UI.
- All work is background. Every endpoint that starts work returns immediately with
  an id; the frontend **polls** for progress.

---

## 2. Auth: getting the `sessionid`

The user must supply the `sessionid` cookie of the Instagram account that will follow:

1. Log in to `instagram.com` in a browser as that account.
2. DevTools (F12) → **Application** → **Cookies** → `https://www.instagram.com`.
3. Copy the **Value** of the `sessionid` cookie
   (looks like `71234567%3AAbCdEf...%3A12%3A...`).

Collect it in a **password-type** input. Treat it as a secret: HTTPS only, never
log it, never render it back. It can **expire** (logout / password change / aging),
in which case a campaign fails auth → prompt the user for a fresh cookie.

---

## 3. Endpoint summary

| # | Method & path | Purpose |
|---|---------------|---------|
| 1 | `POST /growth/campaigns` | Start a campaign from a description + sessionid |
| 2 | `GET  /growth/campaigns/{id}` | Poll campaign progress |
| 3 | `GET  /growth/campaigns/{id}/results` | Full results (competitors + followed users) |
| 4 | `POST /growth/campaigns/{id}/cancel` | Stop a running campaign |
| 5 | `POST /growth/sessions` | (Optional) pre-store a session under a `credential_id` |
| 6 | `POST /growth/tasks` | Manual follow/unfollow of a known username list |
| 7 | `GET  /growth/tasks/{id}` | Poll a follow/unfollow task |
| 8 | `POST /growth/tasks/{id}/cancel` | Stop a task |
| 9 | `POST /growth/recommend` | Rank candidates from a link/keywords (⚠ uses Apify) |

**For the primary flow, the frontend only needs #1–#4.**

---

## 4. Campaign endpoints (primary flow)

### 4.1 `POST /growth/campaigns` — start

There are **two ways to find people to follow**, chosen with `candidate_source`:

- **`apify`** (default) — discover competitors from `product_description` and follow
  their post commenters (via Apify). Needs a description.
- **`instagram_suggested`** — follow the logged-in account's own Instagram
  **"Suggested for you"** accounts, reloading the list until `total_follows` is
  reached (or until suggestions run dry → completes partial). **No description or
  Apify needed.** `sources` / `n_posts` / `competitor_limit` are ignored.

**Request body — apify mode (default)**
```jsonc
{
  "product_description": "organic vegan skincare for sensitive skin", // required in apify mode, min 3 chars
  "candidate_source": "apify",  // default — can be omitted
  "sessionid": "71234567%3AAbCd...%3A12%3A...", // required* — IG sessionid cookie
  "total_follows": 50,       // 1–500, default 50
  "sources": ["commenters", "likers"], // default; subset allowed
  "n_posts": 5,              // 1–20, default 5 — recent posts per competitor to scan
  "competitor_limit": 10     // 1–50, optional — max competitors (default server config)
}
```

**Request body — instagram_suggested mode**
```jsonc
{
  "candidate_source": "instagram_suggested",
  "sessionid": "71234567%3AAbCd...%3A12%3A...", // required* — IG sessionid cookie
  "total_follows": 25        // 1–500, default 50 — keep small; this reads the account's private API
}
```

| Field | Type | Required | Default | Notes |
|-------|------|----------|---------|-------|
| `candidate_source` | string | ❌ | `"apify"` | `apify` or `instagram_suggested` |
| `product_description` | string | ✅ in apify | `""` | min length 3; required only for `apify` |
| `sessionid` | string | ✅* | `""` | IG `sessionid` cookie |
| `credential_id` | string | ✅* | `""` | alternative to `sessionid` (pre-stored account) |
| `total_follows` | int | ❌ | `50` | 1–500 |
| `sources` | string[] | ❌ | `["commenters","likers"]` | apify only; any of `commenters`, `likers` |
| `n_posts` | int | ❌ | `5` | apify only; 1–20 |
| `competitor_limit` | int\|null | ❌ | `null` | apify only; 1–50 |
| `days` | int | ❌ | `1` | 1–30 (reserved; multi-day pacing later) |

> \* Provide **either** `sessionid` **or** `credential_id`. Omitting both → `422`.
> In `instagram_suggested` mode `competitors` stays empty and the campaign goes
> straight `authenticating → following → done`. It reads the account's private API
> (one call per reload), so keep `total_follows` small.

**Response `202`**
```json
{ "campaign_id": "c1a2b3c4-...", "status": "running", "total_target": 50 }
```
Store `campaign_id`, then poll #4.2.

---

### 4.2 `GET /growth/campaigns/{campaign_id}` — progress

**Response `200`**
```jsonc
{
  "campaign_id": "c1a2b3c4-...",
  "status": "running",        // running | completed | stopped | failed
  "stage": "harvesting",      // authenticating | discovering | harvesting | following | done
  "total_target": 50,
  "competitors_found": 8,
  "candidates_found": 120,
  "done": 17,                 // users acted on so far
  "success": 15,
  "skipped": 1,
  "failed": 1,
  "current_index": 17,
  "started_at": "2026-07-04T09:00:00+00:00",
  "completed_at": null,       // set when finished
  "error": null               // human-readable reason when status=failed / empty result
}
```

**Drive the UI from `stage` + `status`:**

| stage | show |
|-------|------|
| `authenticating` | "Logging in…" |
| `discovering` | "Finding competitors…" |
| `harvesting` | "Collecting people who engaged…" |
| `following` | progress bar `done / total_target` |
| `done` | final state (see `status`) |

Poll every **3–5s** while `status === "running"`. Stop polling once `status` is
`completed` / `stopped` / `failed`, then fetch #4.3.

---

### 4.3 `GET /growth/campaigns/{campaign_id}/results` — full results

**Response `200`**
```jsonc
{
  "campaign_id": "c1a2b3c4-...",
  "status": "completed",
  "product_description": "organic vegan skincare for sensitive skin",
  "total_target": 50,
  "competitors": [
    { "username": "brand_a", "score": 100, "reason": "authored 4 top post(s) under matched hashtags", "followers": 0 }
  ],
  "results": [
    { "username": "user_1", "action": "follow", "status": "success", "user_id": "123", "message": "" },
    { "username": "user_2", "action": "follow", "status": "skipped", "user_id": "",    "message": "daily cap (150) reached" },
    { "username": "user_3", "action": "follow", "status": "error",   "user_id": "",    "message": "..." }
  ],
  "success": 15, "skipped": 1, "failed": 1,
  "started_at": "2026-07-04T09:00:00+00:00",
  "completed_at": "2026-07-04T09:12:00+00:00"
}
```
Per-user `status`: `success` | `skipped` | `error`.

---

### 4.4 `POST /growth/campaigns/{campaign_id}/cancel` — stop

**Response `200`**
```json
{ "campaign_id": "c1a2b3c4-...", "status": "stopping", "message": "Stop signal sent. Poll GET /growth/campaigns/{id} to confirm." }
```
Stops **before the next follow** (no mid-action interruption). Keep polling until
`status` becomes `stopped`. If already finished, returns the terminal status with a
"not running / already finished" message.

---

## 5. Optional: pre-stored sessions

If you'd rather store the session once and reuse a short `credential_id` across
campaigns instead of sending the cookie every time:

### `POST /growth/sessions`
**Request** `{ "sessionid": "<cookie>", "credential_id": "acct_1" }`
(`credential_id` optional — defaults to the resolved IG username.)

**Response `201`** `{ "credential_id": "acct_1", "username": "acct_1", "status": "saved", "message": "..." }`

Then start campaigns with `{ "product_description": "...", "credential_id": "acct_1" }`
instead of `sessionid`. Invalid/expired cookie → `400`.

> Note: campaigns **delete the stored session when they finish**, so a stored
> `credential_id` is single-use per session — you'll re-run `POST /growth/sessions`
> for the next campaign. (Say if you want this made persistent.)

---

## 6. Manual follow / unfollow (known list)

For when the user already has a list of usernames (no discovery).

### `POST /growth/tasks`
```json
{ "action": "follow", "usernames": ["a", "b", "c"], "credential_id": "acct_1" }
```
`action`: `follow` | `unfollow`. Response `202`:
`{ "task_id": "...", "status": "running", "action": "follow", "total": 3 }`

### `GET /growth/tasks/{task_id}`
Same shape as campaign progress (status/done/success/skipped/failed/results).

### `POST /growth/tasks/{task_id}/cancel`
Same semantics as campaign cancel.

> This path uses `credential_id` (stored account). Use `POST /growth/sessions`
> first to create one, or pass an account already in the DB.

---

## 7. `POST /growth/recommend` (⚠ uses Apify)

Ranks candidates from a profile/post URL or keywords **without following**. Unlike
campaigns, this endpoint **still calls Apify** (paid). Only wire it up if the user
opts into it.
```json
{ "source_url": "https://instagram.com/some_profile/", "product_description": "...", "n_posts": 5, "min_score": 60 }
```
or `{ "keywords": ["skincare","vegan"], "product_description": "..." }`. Returns a
`recommendations[]` list with `{ username, score, action, reason }`.

---

## 8. TypeScript client (primary flow)

```ts
const API_BASE = "http://localhost:8001";

export async function startCampaign(body: {
  candidate_source?: "apify" | "instagram_suggested"; // default "apify"
  product_description?: string;   // required only when candidate_source === "apify"
  sessionid: string;              // or credential_id
  credential_id?: string;
  total_follows?: number;
  sources?: ("commenters" | "likers")[];  // apify only
  n_posts?: number;                        // apify only
  competitor_limit?: number;               // apify only
}): Promise<{ campaign_id: string; status: string; total_target: number }> {
  const res = await fetch(`${API_BASE}/growth/campaigns`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error((await res.json()).detail ?? res.statusText);
  return res.json();
}

export async function getCampaign(id: string) {
  const res = await fetch(`${API_BASE}/growth/campaigns/${id}`);
  if (!res.ok) throw new Error((await res.json()).detail ?? res.statusText);
  return res.json();
}

export async function getCampaignResults(id: string) {
  const res = await fetch(`${API_BASE}/growth/campaigns/${id}/results`);
  return res.json();
}

export async function cancelCampaign(id: string) {
  const res = await fetch(`${API_BASE}/growth/campaigns/${id}/cancel`, { method: "POST" });
  return res.json();
}

// Poll until terminal, calling onTick with each progress snapshot.
export async function pollCampaign(id: string, onTick: (s: any) => void) {
  while (true) {
    const s = await getCampaign(id);
    onTick(s);
    if (s.status !== "running") return s;
    await new Promise((r) => setTimeout(r, 4000));
  }
}
```

---

## 9. Errors & status codes

| Code | Meaning | Frontend action |
|------|---------|-----------------|
| `202` | work started | store id, begin polling |
| `201` | session saved | proceed |
| `200` | ok | render |
| `400` | invalid sessionid (session endpoint) | ask for a fresh cookie |
| `404` | unknown campaign/task id | show "not found" |
| `422` | body failed validation (missing description, no auth, out-of-range) | show field errors from `detail` |

Also handle **`status: "failed"`** on a campaign/task (HTTP was `200`): read `error`.
Common `error` messages:
- `"sessionid login failed: ..."` → cookie invalid/expired → re-prompt.
- `"no competitors found — try a more specific, hashtag-friendly description"`.
- `"no candidate users harvested"`.

---

## 10. Recommended UX

1. Form: **product description** (textarea) + **Instagram session cookie**
   (password field, with a "where do I find this?" hint) + optional
   `total_follows` / `sources` toggles (Commenters, Likers).
2. Submit → `startCampaign` → store `campaign_id`.
3. `pollCampaign` → drive a stage label + `done/total_target` progress bar.
4. On terminal → `getCampaignResults` → show competitors found + followed list
   with per-user status chips (success / skipped / error).
5. Provide a **Cancel** button while running.

**Don't** expose a "shares" option (not supported). **Warn** users that very large
`total_follows` / `n_posts` values increase the account's block risk.
