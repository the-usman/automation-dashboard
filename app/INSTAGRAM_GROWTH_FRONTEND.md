# Instagram Growth API — Frontend Integration Guide

Everything a frontend needs to integrate the Instagram growth feature: recommend
users to follow, trigger a follow/unfollow batch, watch its progress, and cancel it.

All endpoints are mounted under `/growth` on the main FastAPI app.

- Base URL: `${API_BASE}` (e.g. `http://localhost:8000`)
- Content type: `application/json`
- Interactive docs: `${API_BASE}/docs` (OpenAPI)

---

## Endpoint summary

| # | Method & path | Purpose | Returns |
|---|---------------|---------|---------|
| 1 | `POST /growth/recommend` | Extract + rank users to follow from a profile/post link **or** keywords | `200` list of scored users (synchronous, can take 10–60s) |
| 2 | `POST /growth/tasks` | Trigger a follow/unfollow batch (runs in background) | `202` `{ task_id, ... }` immediately |
| 3 | `GET /growth/tasks/{task_id}` | Poll progress of a batch | `200` status + counters + per-user results |
| 4 | `POST /growth/tasks/{task_id}/cancel` | Stop a running batch | `200` acknowledgement |

---

## 1. Get recommendations — `POST /growth/recommend`

Synchronous. Calls Instagram scraping + LLM scoring, so expect **10–60 seconds**.
Show a loading state and use a generous client timeout (≥ 90s).

**Provide ONE of:**
- `source_url` — a competitor **profile** link (scans its recent `n_posts` posts'
  commenters) or a single **post** link (scans that post's commenters), **or**
- `keywords` — hashtags/terms (scans posts under them, takes the authors).

If you send neither, you get `400`.

### Request body

```jsonc
{
  "source_url": "https://www.instagram.com/competitor_brand/", // profile OR post link
  "keywords": [],                  // used only when source_url is empty
  "product_description": "organic vegan skincare for sensitive skin", // helps ranking
  "n_posts": 5,                    // 1–20, recent posts to scan (profile links only)
  "min_score": 60                  // 0–100; score >= this ⇒ action "follow", else "skip"
}
```

| Field | Type | Required | Default | Notes |
|-------|------|----------|---------|-------|
| `source_url` | string | one of url/keywords | `""` | IG profile or post URL |
| `keywords` | string[] | one of url/keywords | `[]` | used when `source_url` empty |
| `product_description` | string | no | `""` | context for LLM scoring |
| `n_posts` | int | no | `5` | 1–20 |
| `min_score` | number | no | `60` | 0–100 |

### Response `200`

```jsonc
{
  "run_id": "a1b2c3d4",
  "mode": "discover_competitor",   // or "discover_product" when using keywords
  "count": 12,
  "recommendations": [
    {
      "username": "jane_doe",
      "score": 82,                 // 0–100
      "action": "follow",          // "follow" | "skip"
      "reason": "Comments on vegan skincare posts; engaged audience match."
    }
  ],
  "errors": []                     // non-fatal issues, array of objects; usually empty
}
```

Feed the `username` values where `action === "follow"` into endpoint #2.

### Example (TypeScript / fetch)

```ts
async function getRecommendations(payload: {
  source_url?: string;
  keywords?: string[];
  product_description?: string;
  n_posts?: number;
  min_score?: number;
}) {
  const res = await fetch(`${API_BASE}/growth/recommend`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) throw new Error((await res.json()).detail ?? res.statusText);
  return res.json(); // RecommendResponse
}
```

---

## 2. Trigger follow / unfollow — `POST /growth/tasks`

Returns **immediately** with `202` and a `task_id`. The actual following happens
in the background (logs in once, acts on each handle with a rate-limit delay,
respects a daily cap). **Do not block the UI** — store the `task_id` and poll #3.

### Request body

```jsonc
{
  "action": "follow",              // "follow" | "unfollow"  (exact, lowercase)
  "usernames": ["jane_doe", "bob"],// at least 1
  "credential_id": "acct_123"      // which IG account to act from (see "Credentials" below)
}
```

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `action` | string | yes | must match `^(follow|unfollow)$` |
| `usernames` | string[] | yes | min length 1 |
| `credential_id` | string | yes | id of a stored IG account |

### Response `202`

```jsonc
{
  "task_id": "f7e6d5c4-...",
  "status": "running",
  "action": "follow",
  "total": 2
}
```

### Example

```ts
async function startActionTask(body: {
  action: "follow" | "unfollow";
  usernames: string[];
  credential_id: string;
}) {
  const res = await fetch(`${API_BASE}/growth/tasks`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error((await res.json()).detail ?? res.statusText);
  return res.json(); // { task_id, status, action, total }
}
```

---

## 3. Poll progress — `GET /growth/tasks/{task_id}`

Call on an interval (e.g. every 3–5s) until `status` is terminal. `404` if the id
is unknown.

### Response `200`

```jsonc
{
  "task_id": "f7e6d5c4-...",
  "status": "running",             // "running" | "completed" | "stopped" | "failed"
  "action": "follow",
  "total": 2,
  "done": 1,                       // processed so far
  "success": 1,
  "skipped": 0,
  "failed": 0,
  "current_index": 1,
  "results": [
    {
      "username": "jane_doe",
      "action": "follow",
      "status": "success",         // "success" | "skipped" | "error"
      "user_id": "17841400000",
      "message": ""
    }
  ],
  "started_at": "2026-06-29T10:00:00+00:00",
  "completed_at": null,            // ISO string once terminal, else null
  "error": null                    // populated when status == "failed"
}
```

### Status values

**Task `status`:**
| Value | Meaning | Terminal? |
|-------|---------|-----------|
| `running` | in progress | no |
| `completed` | all usernames processed | yes |
| `stopped` | cancelled by user (#4) | yes |
| `failed` | login or fatal error — see `error` | yes |

**Per-result `status`:** `success` (followed/unfollowed), `skipped` (e.g. daily cap
hit or already in desired state), `error` (per-user failure — see `message`).

Progress for a progress bar: `done / total`.

### Example polling loop

```ts
async function pollTask(taskId: string, onUpdate: (s: any) => void) {
  const TERMINAL = new Set(["completed", "stopped", "failed"]);
  while (true) {
    const res = await fetch(`${API_BASE}/growth/tasks/${taskId}`);
    if (res.status === 404) throw new Error("Task not found");
    const status = await res.json();
    onUpdate(status); // update progress bar / list
    if (TERMINAL.has(status.status)) return status;
    await new Promise((r) => setTimeout(r, 4000));
  }
}
```

---

## 4. Cancel a batch — `POST /growth/tasks/{task_id}/cancel`

Sends a stop signal. The task halts **after the current action finishes** (no
mid-action interruption), so allow a few seconds and keep polling #3 until
`status === "stopped"`.

### Responses `200`

```jsonc
// signal accepted (was running)
{ "task_id": "...", "status": "stopping", "message": "Stop signal sent. Poll GET /growth/tasks/{id} to confirm." }

// task wasn't running anymore
{ "task_id": "...", "status": "completed", "message": "Task is not running" }
// or
{ "task_id": "...", "status": "completed", "message": "Task already finished" }
```

`404` if the id is unknown.

> Note: the cancel response `status: "stopping"` is transient. The authoritative
> final state is `"stopped"`, which you observe via endpoint #3.

### Example

```ts
async function cancelTask(taskId: string) {
  const res = await fetch(`${API_BASE}/growth/tasks/${taskId}/cancel`, { method: "POST" });
  if (res.status === 404) throw new Error("Task not found");
  return res.json();
}
```

---

## Typical UI flow

```
[1] User pastes competitor profile/post link (or keywords)
        │  POST /growth/recommend     (spinner, ~10–60s)
        ▼
[2] Show ranked candidates; user selects which to follow
        │  POST /growth/tasks         → task_id (instant)
        ▼
[3] Show progress bar (done/total) + per-user results
        │  GET  /growth/tasks/{id}    (poll every ~4s)
        │  [optional] POST .../cancel → keep polling until "stopped"
        ▼
[4] Done when status ∈ {completed, stopped, failed}
```

---

## Credentials (`credential_id`)

`credential_id` refers to an Instagram account stored server-side in the
`instagram_accounts` MongoDB collection. Passwords/session blobs are stored
**encrypted** and are never returned by any endpoint. The frontend only ever
passes the opaque `credential_id`; it never sends a password.

> If you need a UI to add/list accounts, that's a separate credentials endpoint —
> not part of this growth API. Ask backend to expose one if required.

---

## Error handling cheat sheet

| Status | When | Frontend action |
|--------|------|-----------------|
| `400` | `/recommend` with neither `source_url` nor `keywords` | show validation message |
| `404` | unknown `task_id` on poll/cancel | stop polling, show "not found" |
| `422` | body fails schema (e.g. bad `action`, empty `usernames`) | show field errors from `detail` |
| `5xx` | scraping/login/server error | show retry; for tasks, `status:"failed"` carries `error` |

FastAPI returns validation errors as `{ "detail": [...] }`. For our explicit
errors (`400`/`404`) `detail` is a plain string.

---

## Notes & gotchas

- **`/recommend` is slow and synchronous.** Always show a loading state; set the
  client/proxy timeout to ≥ 90s.
- **Follow/unfollow is intentionally throttled** (delay between actions + daily
  cap), so a large batch can take a while — that's why it's a background task.
- **Cancel is cooperative**, not instant. Don't assume `stopped` the moment you
  call cancel; confirm via polling.
- **Counters are authoritative from `GET /tasks/{id}`**, not from the create
  response — always render progress from the poll payloa