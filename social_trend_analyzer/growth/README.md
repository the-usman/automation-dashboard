# Instagram Growth Agent — Integration Guide

A LangGraph agent that **recommends Instagram users to follow** and **executes
follow / unfollow** actions on a username list.

This document is the complete contract for integrating the agent into the
frontend + backend. It covers the data shapes, the recommended REST API, the
intended UI flow, and the safety rules the UI **must** enforce.

---

## 1. What it does

Two-step product flow (mirrors the email-outreach `preview → send` pattern):

```
            ┌─────────────── STEP 1: DISCOVER + RECOMMEND ───────────────┐
 Product /  │  mode=discover_product     → search hashtags → authors      │
 Competitor │  mode=discover_competitor  → competitor's last N posts →    │ → ranked
   input    │                              their commenters               │   recommendations
            └────────────────────────────────────────────────────────────┘
                                       │  user reviews & selects usernames
                                       ▼
            ┌─────────────── STEP 2: ACT ────────────────────────────────┐
 Selected   │  mode=execute_actions  → follow / unfollow each username    │ → per-user
 usernames  │  (instagrapi, rate-limited, daily-capped)                   │   results
            └────────────────────────────────────────────────────────────┘
```

**Library split (deliberate):**
- **Reads** (competitor posts, commenters, hashtag discovery) use the **Apify**
  scraper — they never touch the logged-in account, minimizing ban risk.
- **Writes** (follow / unfollow) use **instagrapi** — the only account-bound part.

---

## 2. Modes & data contract

The agent is driven by a single **trigger** dict. Three modes:

### Mode A — `discover_product`
Find candidates by the product's hashtags/keywords, then rank them.

```jsonc
{
  "mode": "discover_product",
  "product_description": "AI-powered home fitness coaching app",
  "keywords": ["fitness", "homeworkout", "gymtok"],   // or "fitness,homeworkout"
  "min_score": 60                                       // optional, default 60
}
```

### Mode B — `discover_competitor`
Find candidates from a competitor's recent posts' commenters, then rank them.

```jsonc
{
  "mode": "discover_competitor",
  "competitor_url": "https://www.instagram.com/competitor_handle/",
  "n_posts": 5,                                         // optional, default 5
  "product_description": "AI-powered home fitness coaching app",
  "min_score": 60
}
```

**Both discover modes return `recommendations`:**

```jsonc
{
  "run_id": "uuid",
  "mode": "discover_competitor",
  "recommendations": [
    { "username": "jane_doe", "score": 82, "action": "follow",
      "reason": "Engaged fitness commenter, fits target audience" },
    { "username": "bob99",    "score": 41, "action": "skip",
      "reason": "Low relevance, generic account" }
  ],
  "action_results": [],
  "errors": []
}
```

### Mode C — `execute_actions`
Follow or unfollow a list of usernames (the ones the user approved in step 1).

```jsonc
{
  "mode": "execute_actions",
  "action": "follow",                                   // "follow" | "unfollow"
  "usernames": ["jane_doe", "fit_mike"],                // or "jane_doe,fit_mike"
  "credential_id": "ig_client01"                        // MongoDB credential lookup key
}
```

**Returns `action_results`:**

```jsonc
{
  "run_id": "uuid",
  "mode": "execute_actions",
  "recommendations": [],
  "action_results": [
    { "username": "jane_doe", "action": "follow", "status": "success",  "user_id": "17841400000", "message": "" },
    { "username": "fit_mike", "action": "follow", "status": "skipped",  "user_id": "", "message": "daily cap (150) reached" },
    { "username": "ghost_x",  "action": "follow", "status": "error",    "user_id": "", "message": "User not found" }
  ],
  "errors": []
}
```

### Field reference

**`UserRecommendation`**
| Field | Type | Meaning |
|---|---|---|
| `username` | string | candidate IG handle |
| `score` | int 0–100 | relevance / value to follow |
| `action` | `"follow"` \| `"skip"` | suggested action (`follow` when `score >= min_score`) |
| `reason` | string | one-line justification |

**`ActionResult`**
| Field | Type | Meaning |
|---|---|---|
| `username` | string | target handle |
| `action` | `"follow"` \| `"unfollow"` | what was attempted |
| `status` | `"success"` \| `"skipped"` \| `"error"` | outcome |
| `user_id` | string | IG numeric id (success only) |
| `message` | string | reason for skip/error |

`errors` (top level) is a list of `{ "node_name": str, "error_message": str }` for
soft failures (e.g. credential not found, login failed) — show these to the user.

---

## 3. Setup

### Dependencies
Already in `requirements.txt` / `pyproject.toml`:
```
instagrapi  pymongo  cryptography  langgraph  langchain-openai
```

### Environment (all prefixed `TREND_`, see `social_trend_analyzer/.env.example`)
| Var | Required for | Notes |
|---|---|---|
| `TREND_MONGO_URI` | acting | credential store (same cluster as email-outreach) |
| `TREND_MONGO_DB` | acting | default `project_db` |
| `TREND_IG_CREDENTIAL_COLLECTION` | acting | default `instagram_accounts` |
| `TREND_FERNET_KEY` | acting (recommended) | decrypts password/totp at rest |
| `TREND_IG_SESSION_DIR` | acting | where instagrapi sessions cache (default `./.ig_sessions`) |
| `TREND_APIFY_TOKEN` | discovery | Apify scraper token |
| `TREND_OPENAI_API_KEY` | ranking | without it, recommendations fall back to neutral score 50 |

### Credential document (seed this in MongoDB before acting)
Collection `instagram_accounts`, one document per IG account:
```jsonc
{
  "credential_id": "ig_client01",   // the lookup key the frontend sends
  "username": "the_account",
  "password": "<fernet-encrypted-or-plaintext>",
  "totp_seed": "<optional 2FA seed>",
  "session": { }                    // optional instagrapi session blob (preferred over password)
}
```

---

## 4. Recommended REST API (backend — drop-in route)

The agent's `run()` is **synchronous and blocking** (instagrapi / Apify / pymongo).
Wrap it in a threadpool so it doesn't block the async event loop. Mirror the
existing `app/src/routes/email_outreach.py` conventions.

**`app/src/models/growth_models.py`** (new):
```python
from pydantic import BaseModel, Field

class RecommendRequest(BaseModel):
    mode: str = Field("discover_product", pattern="^(discover_product|discover_competitor)$")
    product_description: str = ""
    keywords: list[str] = Field(default_factory=list)
    competitor_url: str = ""
    n_posts: int = 5
    min_score: float = 60

class Recommendation(BaseModel):
    username: str
    score: int
    action: str
    reason: str

class RecommendResponse(BaseModel):
    run_id: str
    mode: str
    recommendations: list[Recommendation]
    errors: list[dict] = Field(default_factory=list)

class ActRequest(BaseModel):
    action: str = Field(..., pattern="^(follow|unfollow)$")
    usernames: list[str]
    credential_id: str

class ActionResultModel(BaseModel):
    username: str
    action: str
    status: str
    user_id: str = ""
    message: str = ""

class ActResponse(BaseModel):
    run_id: str
    action_results: list[ActionResultModel]
    errors: list[dict] = Field(default_factory=list)
```

**`app/src/routes/growth.py`** (new — register in `app/app.py` with `app.include_router(growth_router)`):
```python
import asyncio
from fastapi import APIRouter
from social_trend_analyzer.growth import run as run_growth
from app.src.models.growth_models import (
    RecommendRequest, RecommendResponse, Recommendation,
    ActRequest, ActResponse, ActionResultModel,
)

router = APIRouter(prefix="/growth", tags=["Instagram Growth"])

@router.post("/recommend", response_model=RecommendResponse)
async def recommend(req: RecommendRequest) -> RecommendResponse:
    result = await asyncio.to_thread(run_growth, req.model_dump())
    return RecommendResponse(
        run_id=result.get("run_id", ""),
        mode=result.get("mode", req.mode),
        recommendations=[Recommendation(**r.model_dump()) for r in result.get("recommendations", [])],
        errors=result.get("errors", []),
    )

@router.post("/act", response_model=ActResponse)
async def act(req: ActRequest) -> ActResponse:
    trigger = {"mode": "execute_actions", **req.model_dump()}
    result = await asyncio.to_thread(run_growth, trigger)
    return ActResponse(
        run_id=result.get("run_id", ""),
        action_results=[ActionResultModel(**a.model_dump()) for a in result.get("action_results", [])],
        errors=result.get("errors", []),
    )
```

> The objects in `result["recommendations"]` / `result["action_results"]` are
> Pydantic models — call `.model_dump()` as shown.

---

## 5. Frontend integration

### Step 1 — get recommendations
```js
// Product-based
const res = await fetch("/growth/recommend", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({
    mode: "discover_product",
    product_description: "AI-powered home fitness coaching app",
    keywords: ["fitness", "homeworkout"],
    min_score: 60,
  }),
});
const { recommendations, errors } = await res.json();

// Competitor-based: same endpoint, mode "discover_competitor" + competitor_url + n_posts
```

Render `recommendations` as a selectable list. Pre-check the rows where
`action === "follow"`. Show `score` and `reason` so the user can judge. **The user
selects which usernames to act on** — never auto-act on the full list.

### Step 2 — act on the selected usernames
```js
const res = await fetch("/growth/act", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({
    action: "follow",                       // or "unfollow"
    usernames: selectedUsernames,           // the user's checked rows
    credential_id: "ig_client01",
  }),
});
const { action_results, errors } = await res.json();
// Show per-row status: success / skipped (with message) / error (with message)
```

### Suggested UI
- **Discovery panel:** toggle Product vs Competitor; inputs for description +
  keywords *or* competitor URL + N posts; "Find users" button.
- **Results table:** checkbox · username · score (badge) · suggested action · reason.
  Bulk "select all suggested-follow".
- **Action bar:** Follow selected / Unfollow selected → **confirmation modal** →
  call `/growth/act` → live per-row status.

---

## 6. Safety rules the UI MUST enforce

Instagram bans accounts that automate aggressively. The backend already
rate-limits, but the UI must reinforce it:

1. **Confirm before acting.** Always show a confirmation modal with the count
   before calling `/growth/act`.
2. **Respect the daily cap.** Default `ig_daily_action_cap = 150` per run. Results
   with `status: "skipped"` and message "daily cap reached" mean the user hit the
   limit — surface this clearly and stop.
3. **Keep batches small.** Recommend acting on ≤ ~150–200 users/day total across
   all runs. Discourage giant selections in the UI.
4. **Each action is delayed** ~4s server-side (`ig_action_delay_seconds`), so an
   `/act` call with N usernames takes ≈ N×4s. Show a progress/loading state and
   set a generous client timeout, or move to a background-job pattern for large N.
5. **Surface a ToS warning** on first use — automation carries account risk; the
   user acts at their own discretion.

---

## 7. Error handling

| Where | Shape | UI action |
|---|---|---|
| Per-user action | `action_results[].status = "error"`, `message` | show inline on that row |
| Login / credential | top-level `errors[]` with `node_name = "execute_actions"` | toast: "Login failed — check credentials" |
| Discovery failure | top-level `errors[]` with `node_name = "discover_*"` | toast: "Discovery failed, try again" |
| No Apify token | empty `recommendations` (no error) | "No candidates found — check discovery config" |
| No OpenAI key | recommendations with `reason = "LLM disabled — neutral default"`, all score 50 | optionally hint ranking is off |

---

## 8. CLI (for testing without the backend)

```bash
# Discover by product
python -m social_trend_analyzer.growth.cli recommend \
  --product "AI fitness app" --keywords fitness,workout,gymtok

# Discover by competitor
python -m social_trend_analyzer.growth.cli recommend \
  --competitor https://www.instagram.com/competitor/ --product "AI fitness app"

# Act
python -m social_trend_analyzer.growth.cli act --follow user1,user2 --credential-id ig_client01
python -m social_trend_analyzer.growth.cli act --unfollow user1 --credential-id ig_client01

# Inspect the graph
python -m social_trend_analyzer.growth.cli --draw
```

All commands print a JSON summary identical in shape to the REST responses above.

---

## 9. Module map

| File | Responsibility |
|---|---|
| `graph.py` | LangGraph assembly + `run(trigger)` entry point |
| `state.py` | `GrowthState` (the trigger/output schema) |
| `schemas.py` | Pydantic models (`UserRecommendation`, `ActionResult`, …) |
| `discovery.py` | Apify reads (commenters, hashtag authors) |
| `recommend.py` | LLM ranking (+ offline fallback) |
| `instagram.py` | instagrapi wrapper (login, follow, unfollow, rate limit, cap) |
| `credentials.py` | MongoDB credential loading + Fernet decryption |
| `cli.py` | command-line entry point |

Tests: `tests/growth/` (32 tests, run offline with all externals mocked).
