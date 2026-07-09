# Reputation API — Frontend Integration Guide

Score the online reputation of one or more social accounts (Instagram / X / TikTok).
The backend scrapes each provided profile via Apify, sends the posts to OpenAI, and
returns an overall score, a per-platform score, and strong/weak points.

**Scope:** reputation is judged on the posts of the **last 30 days** (configurable via
`REPUTATION_LOOKBACK_DAYS`). Older posts are excluded even if the scraper returns them.

This is a **single, stateless endpoint** — no auth, no database, no polling. You send
usernames, you get JSON back.

---

## Endpoint

```
POST {BASE_URL}/check-reputation
Content-Type: application/json
```

`BASE_URL` is wherever the API runs (local dev: `http://localhost:8001`).

### Request body

At least one username is required. Omit or send `null` for platforms you don't want checked.

```json
{
  "instagram_username": "nike",
  "x_username": "nike",
  "tiktok_username": "nike"
}
```

| Field | Type | Required | Notes |
|---|---|---|---|
| `instagram_username` | `string \| null` | — | Instagram handle (with or without `@`) |
| `x_username` | `string \| null` | — | X/Twitter handle |
| `tiktok_username` | `string \| null` | — | TikTok handle |

**Rule:** if all three are missing/null/blank → `422` (see Errors).

### Response `200`

```json
{
  "overall_score": 78,
  "platform_scores": {
    "instagram": 82,
    "x": 74,
    "tiktok": null
  },
  "strong_points": [
    "Consistent, high-quality visual branding",
    "Strong positive audience engagement"
  ],
  "weak_points": [
    "Infrequent posting on X",
    "Little community interaction in comments"
  ]
}
```

| Field | Type | Notes |
|---|---|---|
| `overall_score` | `number` (0–100) | Overall reputation across all scored platforms |
| `platform_scores.instagram` | `number \| null` | `null` if not requested **or** if that platform failed / had no posts |
| `platform_scores.x` | `number \| null` | same |
| `platform_scores.tiktok` | `number \| null` | same |
| `strong_points` | `string[]` | Concrete strengths |
| `weak_points` | `string[]` | Concrete shortcomings to improve |

> **Per-platform resilience:** if one platform fails (e.g. username not found), its score
> comes back `null` and the others are still scored — the request does **not** fail as a whole.
> If *every* platform yields no data, you get `overall_score: 0` and a `weak_points` note
> explaining nothing could be retrieved.

---

## ⚠️ Latency — this is a slow request

Scraping runs live via Apify and can take **tens of seconds to a few minutes**, especially
with multiple platforms. Design the UI for it:

- Use a **long client timeout** (e.g. 180s) — do not let `fetch` abort at the default.
- Show a **loading / progress state**; disable the submit button while in flight.
- Consider warning the user that a full 3-platform check takes longer.

---

## Errors

| Status | When | Body |
|---|---|---|
| `422` | All three usernames null/blank, or malformed body | FastAPI validation error (`detail` array) |
| `502` | Scrape/score pipeline failed (e.g. OpenAI not configured) | `{ "detail": "reputation check failed: ..." }` |

`422` example:

```json
{ "detail": [ { "type": "value_error", "msg": "Value error, provide at least one of: instagram_username, x_username, tiktok_username" } ] }
```

---

## TypeScript types

```ts
export interface ReputationRequest {
  instagram_username?: string | null;
  x_username?: string | null;
  tiktok_username?: string | null;
}

export interface PlatformScores {
  instagram: number | null;
  x: number | null;
  tiktok: number | null;
}

export interface ReputationResponse {
  overall_score: number;              // 0–100
  platform_scores: PlatformScores;
  strong_points: string[];
  weak_points: string[];
}
```

## Fetch helper

```ts
const BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8001";

export async function checkReputation(
  body: ReputationRequest
): Promise<ReputationResponse> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 180_000); // 3 min

  try {
    const res = await fetch(`${BASE_URL}/check-reputation`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal: controller.signal,
    });

    if (res.status === 422) {
      throw new Error("Provide at least one username.");
    }
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail ?? `Request failed (${res.status})`);
    }
    return (await res.json()) as ReputationResponse;
  } finally {
    clearTimeout(timeout);
  }
}
```

## React usage example

```tsx
import { useState } from "react";

function ReputationChecker() {
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<ReputationResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function onSubmit(usernames: ReputationRequest) {
    setLoading(true);
    setError(null);
    setResult(null);
    try {
      setResult(await checkReputation(usernames));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Something went wrong");
    } finally {
      setLoading(false);
    }
  }

  // Render: inputs for the 3 usernames, a submit button (disabled while loading),
  // a spinner while `loading`, `error` if set, and the score card when `result` is set.
  // Remember: platform_scores.* can be null — render "N/A" for those.
}
```

---

## curl (quick manual test)

```bash
curl -X POST http://localhost:8001/check-reputation \
  -H "Content-Type: application/json" \
  -d '{"instagram_username":"nike"}'
```

---

## Backend setup notes (for whoever runs the server)

- **CORS** is enabled. In production set `CORS_ALLOW_ORIGINS` to your frontend origin(s),
  comma-separated (default is `*` for dev).
- Required env for scoring to work: `OPENAI_API_KEY`, `APIFY_API_TOKEN`. Optional:
  `REPUTATION_OPENAI_MODEL` (default `gpt-4o`), `REPUTATION_LOOKBACK_DAYS` (default `30`),
  `REPUTATION_POSTS_PER_PLATFORM` (max posts per platform within the window, default `100`).
- **Logs:** every check is written (DEBUG level, full trail: scrape runs, poll status, post
  counts, scoring, final result, timings) to `logs/reputation/reputation.log` (rotating,
  5 MB × 3 backups). Override the directory with `REPUTATION_LOG_DIR`. Each request's lines
  share a `[check_id]` prefix for correlation.
- The server currently also needs `MONGODB_URI` set to start (shared app lifespan), even
  though the reputation endpoint itself doesn't use the database.
- Interactive API docs: `GET {BASE_URL}/docs`.
