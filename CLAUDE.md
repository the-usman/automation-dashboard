# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

Uses **uv** (Python ≥3.13). Dependencies are locked in `uv.lock`.

```bash
uv sync                                   # install deps into .venv
uv run python main.py                     # run FastAPI server (uvicorn on 0.0.0.0:8001)
uv run pytest                             # run the whole suite
uv run pytest tests/growth/test_instagram.py            # one file
uv run pytest tests/growth/test_instagram.py::test_name # one test
uv run pytest -k "competitor" -v          # by keyword
uv run ruff check .                        # lint (ruff is the configured linter; .ruff_cache present)
```

Batch/offline pipelines run as module CLIs (not via the server):

```bash
# Trend analyzer (multi-client social scan)
uv run python -m social_trend_analyzer.cli --mode client00
uv run python -m social_trend_analyzer.cli --mode client --client-id test01 --platforms instagram,tiktok
uv run python -m social_trend_analyzer.cli --draw          # print graph as mermaid

# Instagram growth agent
uv run python -m social_trend_analyzer.growth.cli recommend --product "AI fitness app" --keywords fitness,workout
uv run python -m social_trend_analyzer.growth.cli act --follow user1,user2 --credential-id cred01
```

Instagram session bootstrap (run once, interactively, to avoid `ChallengeRequired`):

```bash
uv run python scripts/ig_session_from_cookie.py "<sessionid-cookie>"   # preferred: reuse browser cookie
uv run python scripts/ig_login.py <username>                           # password + emailed checkpoint code
```

## The two halves of this repo

There are **two separate systems** that share the `social_trend_analyzer` package but have independent config and entry points. Do not assume a change to one affects the other.

1. **`app/`** — the FastAPI service (`app.app:app`). Thin routers → services → MongoDB. Long jobs run in the background and report progress through Mongo, which is the **single source of truth** for status. This is the deployed product.
2. **`social_trend_analyzer/`** — LangGraph batch pipelines, also usable standalone via their module CLIs. The FastAPI layer calls into this package (e.g. growth discovery, the `InstagramClient`).

### Config is split by prefix — this trips people up

- **FastAPI app** reads **raw env vars**: `MONGODB_URI`, `MONGODB_DB_NAME` (default `project_db`), `OPENAI_API_KEY`, `CREDENTIAL_ENCRYPTION_KEY` (Fernet), `BASE_URL`, `QDRANT_URL`/`QDRANT_API_KEY`. See `app/src/services/mongodb.py`.
- **`social_trend_analyzer`** reads **pydantic-settings with a `TREND_` prefix** (`social_trend_analyzer/config.py` → `Settings`): `TREND_MONGO_URI`, `TREND_OPENAI_API_KEY`, `TREND_APIFY_TOKEN`, `TREND_FERNET_KEY`, etc.

So the same Mongo cluster is reached two ways (`MONGODB_URI` for the app, `TREND_MONGO_URI` for the package) and there are two OpenAI/Fernet keys. When adding config, put runtime tuning knobs in `DEFAULT_CONFIG` (a plain dict overlaid at graph init) and secrets/endpoints on `Settings`.

## LangGraph convention (both pipelines follow it)

`social_trend_analyzer/graph.py` and `social_trend_analyzer/growth/graph.py` share one shape: a module-level **compiled** `StateGraph` (`MAIN_GRAPH`/`CLIENT_GRAPH`, `GROWTH_GRAPH`) built once at import, an `init_run` node that normalizes the trigger dict, a `route_mode` conditional edge, and a `run(trigger)` entry point. Nodes are pure `state -> dict` functions. **Soft-error pattern:** nodes catch exceptions, log a warning, and append `{"node_name", "error_message"}` to an `errors` list in state rather than raising — the graph always completes. These graphs are a faithful port of the n8n workflows in `workflows/*.json`; comments cross-reference the original n8n node names.

- **Main analyzer graph** (`graph.py`): `MAIN_GRAPH` builds per-client contexts then loops `CLIENT_GRAPH` once per client. `CLIENT_GRAPH` fans out to five collectors in parallel (`raw_posts` merge via an `operator.add` reducer), then linear normalize → dedupe → rag → trends → sentiment → ideas → store → report. Storage dual-writes to Google Sheets (`storage.py`) and Qdrant (`rag.py`).
- **Growth graph** (`growth/graph.py`): routes to `discover_product` / `discover_competitor` (Apify scrape → LLM `recommend`) or `execute_actions` (login → follow/unfollow).

**Graceful degradation is a hard rule** across this package: every external integration (`storage.py` Google Sheets, `rag.py` Qdrant, `growth/credentials.py` Mongo, `growth/instagram.py` instagrapi) is guarded so importing works without the dependency/credentials and loaders return empty/`None` instead of crashing. Preserve this when editing collectors/integrations.

## Instagram growth: account safety is the core constraint

The `InstagramClient` (`growth/instagram.py`) is the **only** account-bound surface. The dominant design pressure is **not getting the user's account logged out or rate-limited (429)**:

- **Reads via Apify, writes via instagrapi.** Apify scrapes public data with its own accounts/proxies (never touches the user's account); the logged-in `sessionid` is used only for the follow/unfollow writes. Aggressive private-API reads (fetching *all* comments/likers of every post) are what historically got accounts logged out — read-volume caps live in `DEFAULT_CONFIG` (`growth_comments_per_post`, `growth_likers_per_post`, `growth_hashtags_max`, etc.).
- **Auth:** prefer `cl.login_by_sessionid()` (runs the full login flow so private *writes* are authorized) over `set_settings()` (restores read state only → `login_required` on follow). Web `sessionid` cookies are inherently distrusted by the mobile private API, so sessions still expire.
- **Pacing:** campaigns wait a long randomized gap between follows (`ig_follow_min_seconds`/`ig_follow_max_seconds`, default 5–10 min) so a batch spreads across the day. There is also a small per-action floor delay and a hard per-run cap (`ig_daily_action_cap`).
- **Credentials** live in MongoDB collection `instagram_accounts` keyed by `credential_id` (never in env); password/totp are Fernet-encrypted at rest. Never send passwords from a frontend — only an opaque `credential_id` or a `sessionid`. Never log or echo a `sessionid`.

### Background campaign runner

`app/src/services/instagram_campaign_runner.py` runs a follow campaign as an `asyncio` background task, with in-memory `dict[str, Task]` + `asyncio.Event` for cooperative stop (plus a hard `task.cancel()`). Blocking instagrapi/LLM calls go through `asyncio.to_thread`. Two candidate sources selected per-campaign via `req.candidate_source`: `apify` (discover competitors → harvest commenters) or `instagram_suggested` (reload the account's own "Suggested for you" list until the target is met). Progress is streamed to `instagram_campaigns` in Mongo. Each campaign also writes a timestamped human-readable audit trail to `logs/growth/<campaign_id>.log` (via `_activity_log`) recording Apify/Instagram candidates and every follow — this is separate from the Python `logging` output.

## API surface

Routers mounted in `app/app.py`, each with a prefix: `/workflow`, `/outreach` (email outreach), `/track` (email open/click tracking), `/calendar`, `/growth` (Instagram). The growth API is documented in `app/GROWTH_API_README.md`; campaign frontend contracts in `app/INSTAGRAM_CAMPAIGN_FRONTEND.md`; email campaign API in `CAMPAIGN_API.md`. `email_outreach` has its own LangGraph under `app/src/graphs/email_outreach/`. The `reply_check_loop` scheduler starts in the app lifespan.

## Notes

- Tests live in `tests/growth/` (pytest, mock-based). There is no server/integration test harness — tests exercise the package functions directly.
- `logs/` and `.ig_sessions/` are gitignored; session files are named `<username>.json` under `ig_session_dir`.
- `main.py` sets `reload=False` and DEBUG logging; `app/app.py` configures INFO logging and silences noisy driver logs (pymongo/httpx/etc.).
