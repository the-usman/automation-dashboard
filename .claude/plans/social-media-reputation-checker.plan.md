# Plan: Social Media Reputation Checker

**Source PRD**: `.claude/prds/social-media-reputation-checker.prd.md`
**Selected Milestone**: 1 — Single-platform scoring (vertical slice, Instagram end-to-end)
**Complexity**: Small

## Summary
Build a **standalone, isolated** reputation-checker feature (its own `reputation_checker/`
package — no import of or coupling to `social_trend_analyzer`). Milestone 1 delivers the
full vertical slice for **one platform (Instagram)**: `POST /check-reputation` validates
input, scrapes the profile via an Apify actor, normalizes posts to
`{text, post_url, date, likes, comments}`, sends them in one batch to OpenAI for a
guaranteed-JSON reputation score, and returns the response. The request/response schema is
built for all three platforms from the start; wiring X + TikTok actors and full
per-platform failure isolation is Milestone 2.

## Isolation decision
- All feature logic lives in a new top-level package `reputation_checker/` that is
  **self-contained**. It must not `import social_trend_analyzer.*`.
- It reads its own env directly per the PRD spec: `OPENAI_API_KEY`, `APIFY_API_TOKEN`
  (plain names — **not** the `TREND_`-prefixed settings used by the analyzer).
- Only touch point with the existing app: a thin router mounted in `app/app.py`, so the
  one FastAPI server serves it. No shared services, models, or config.

## Patterns to Mirror
| Category | Source | Pattern |
|---|---|---|
| Apify call (style only, reimplemented) | `social_trend_analyzer/collectors/apify.py:114` | `run_actor_sync`: `httpx.Client(timeout=60)`, POST run → poll `TERMINAL` statuses → fetch `/datasets/{id}/items`; **guarded** — returns `[]` when token/actor missing |
| Thin FastAPI router | `app/src/routes/growth.py:46` | `APIRouter(prefix=...)`, `response_model=`, blocking work off the loop via `asyncio.to_thread`, `HTTPException` for input errors |
| "At least one required" validation | `app/src/models/campaign_models.py` (`_require_auth` `model_validator(mode="after")`) | Pydantic v2 `model_validator` raising `ValueError` when a cross-field rule fails |
| Guarded external integration | `social_trend_analyzer/collectors/apify.py:127-131` | Missing credential → log + return empty, never crash import or request |
| Structured logging | `app/app.py:11-20` | `logging.getLogger(__name__)`, INFO level, no secrets in logs |
| Tests | `tests/growth/test_*.py` | pytest, mock-based, exercise functions directly (no live network) |

## Files to Change
| File | Action | Why |
|---|---|---|
| `reputation_checker/__init__.py` | CREATE | Package marker + `check_reputation()` public entry |
| `reputation_checker/config.py` | CREATE | Read `OPENAI_API_KEY`, `APIFY_API_TOKEN`, tuning constants (posts-per-platform cap, model name, timeouts) |
| `reputation_checker/schemas.py` | CREATE | Pydantic `ReputationRequest` (3 optional usernames + `model_validator` rejecting all-null), `NormalizedPost`, `ReputationResponse` (overall/platform scores 0-100\|null, strong/weak points) |
| `reputation_checker/apify_client.py` | CREATE | `scrape_profile(platform, username) -> list[raw]`; one profile actor per platform, guarded start→poll→fetch |
| `reputation_checker/normalize.py` | CREATE | Map raw actor items → `NormalizedPost{text, post_url, date, likes, comments}`, per-platform field mapping |
| `reputation_checker/scorer.py` | CREATE | Batch all normalized posts → OpenAI with structured/JSON output → `ReputationResponse` fields; guaranteed-valid JSON |
| `reputation_checker/service.py` | CREATE | Orchestrator `check_reputation(req)`: for each provided username → scrape → normalize; batch → score; assemble response |
| `app/src/routes/reputation.py` | CREATE | `POST /check-reputation` thin router → `asyncio.to_thread(check_reputation, req)` |
| `app/app.py` | UPDATE | `include_router(reputation_router)` (only edit to existing code) |
| `tests/reputation/__init__.py` | CREATE | Test package |
| `tests/reputation/test_schemas.py` | CREATE | All-null rejected; single/partial usernames accepted |
| `tests/reputation/test_normalize.py` | CREATE | Raw IG actor item → `NormalizedPost` mapping incl. missing fields |
| `tests/reputation/test_service.py` | CREATE | Mocked scrape+score: Instagram-only request returns valid `ReputationResponse` |

## Tasks
### Task 1: Schemas + validation
- **Action**: Define `ReputationRequest` (`instagram_username`, `x_username`, `tiktok_username` all `str | None = None`) with a `model_validator(mode="after")` raising `ValueError` if all three are null/blank. Define `NormalizedPost` and `ReputationResponse` (`overall_score: int`, `platform_scores: {instagram,x,tiktok: int|None}`, `strong_points: list[str]`, `weak_points: list[str]`).
- **Mirror**: `campaign_models.py` `model_validator` "at least one required".
- **Validate**: `uv run pytest tests/reputation/test_schemas.py`

### Task 2: Config
- **Action**: `config.py` reads `OPENAI_API_KEY` / `APIFY_API_TOKEN` from env (via `os.getenv`, `.env` already loaded by `main.py`); constants for model, `POSTS_PER_PLATFORM` cap, Apify poll interval/max attempts, HTTP timeout.
- **Mirror**: guarded-credential style from `apify.py`.
- **Validate**: `uv run python -c "import reputation_checker.config"`

### Task 3: Apify profile scraper (Instagram)
- **Action**: `apify_client.scrape_profile(platform, username)` — resolve the platform's profile actor, start run, poll until terminal, fetch dataset items; return `[]` on missing token or any failure (log a warning). Cap results to `POSTS_PER_PLATFORM`.
- **Mirror**: `apify.py:114` `run_actor_sync` (reimplemented locally, not imported).
- **Validate**: `uv run pytest tests/reputation/test_service.py` (Apify mocked)

### Task 4: Normalizer
- **Action**: `normalize.normalize(platform, raw_items) -> list[NormalizedPost]` mapping each platform's raw fields to `{text, post_url, date, likes, comments}`; tolerate missing keys (default empty/0).
- **Mirror**: defensive dict access from `apify._post_array`.
- **Validate**: `uv run pytest tests/reputation/test_normalize.py`

### Task 5: OpenAI scorer
- **Action**: `scorer.score(posts) -> dict` — single batched call using structured/JSON output (json_object mode or a Pydantic/function schema) so the result is guaranteed-valid JSON matching `ReputationResponse`. Prompt scores on the **general sentiment/quality** lens (PRD) and returns per-platform scores + strong/weak points. Platforms with no posts → `null`.
- **Mirror**: house LLM usage (`langchain-openai` is already a dependency); keep the call self-contained.
- **Validate**: `uv run pytest tests/reputation/test_service.py` (OpenAI mocked)

### Task 6: Service orchestrator
- **Action**: `service.check_reputation(req)` — for each provided username, scrape→normalize (Instagram wired now); collect all posts; call scorer; build `ReputationResponse`. (Full try/except isolation per platform lands in Milestone 2, but structure the per-platform loop now.)
- **Mirror**: soft-error accumulation style from the analyzer nodes.
- **Validate**: `uv run pytest tests/reputation/test_service.py`

### Task 7: Endpoint + mount
- **Action**: `routes/reputation.py` `POST /check-reputation` with `response_model=ReputationResponse`, dispatch blocking work via `asyncio.to_thread`; add `include_router(reputation_router)` in `app/app.py`.
- **Mirror**: `routes/growth.py:46` recommend endpoint.
- **Validate**: `uv run python -c "from app.app import app; print([r.path for r in app.routes if 'reputation' in getattr(r,'path','')])"`

## Validation
```bash
uv run pytest tests/reputation/ -v
uv run python -c "from app.app import app; assert any('check-reputation' in getattr(r,'path','') for r in app.routes)"
uv run ruff check reputation_checker app/src/routes/reputation.py
# Manual smoke (needs real tokens):
# uv run python main.py  # then: curl -X POST localhost:8001/check-reputation -H 'content-type: application/json' -d '{"instagram_username":"someacct"}'
```

## Risks
| Risk | Likelihood | Mitigation |
|---|---|---|
| Accidental coupling to `social_trend_analyzer` | Medium | Explicit rule: no `social_trend_analyzer` import in `reputation_checker/`; a test asserts it |
| Chosen Instagram Apify actor's output shape differs from assumed fields | High | Normalizer tolerates missing keys; validate against one real scrape before hardening |
| Apify scrape latency exceeds HTTP timeout (blocking request) | Medium | Cap posts; generous `to_thread` + httpx timeout; async/polling deferred to an Open Question |
| OpenAI returns non-conforming JSON | Low | Structured/json_object output + Pydantic validation of the parsed result |
| Env name confusion (`OPENAI_API_KEY` vs `TREND_OPENAI_API_KEY`) | Medium | `reputation_checker/config.py` reads the plain names per PRD; documented in-file |

## Acceptance
- [ ] All tasks complete
- [ ] `tests/reputation/` passes; Instagram-only request returns a valid `ReputationResponse`
- [ ] All-null request rejected (422)
- [ ] `reputation_checker/` imports nothing from `social_trend_analyzer`
- [ ] Patterns mirrored (httpx-guarded Apify, thin router, Pydantic validator), not reinvented
- [ ] Only existing-file change is the router mount in `app/app.py`
