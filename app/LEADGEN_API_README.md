# Lead Generation API (`/leadgen`)

Turn a free-text **product/services description** into leads from two Apify actors:

| Actor key      | Apify actor                      | Use for                                   |
|----------------|----------------------------------|-------------------------------------------|
| `google_maps`  | `compass/crawler-google-places`  | Local businesses (name, address, phone, website, emails) |
| `leads_finder` | `code_crafter/leads-finder`      | B2B decision-makers (people with job titles) |

Flow: **generate filters → review/edit → approve & run → leads stored in Mongo.**
Reads only — no account is logged in. All steps degrade gracefully: with no
`TREND_OPENAI_API_KEY` the filters fall back to deterministic keyword seeding, and
with no `TREND_APIFY_TOKEN` a run stores 0 leads with a soft error instead of crashing.

## Config / secrets

- Secrets (reused, package layer): `TREND_OPENAI_API_KEY`, `TREND_APIFY_TOKEN`.
- App layer: `MONGODB_URI`, `MONGODB_DB_NAME`.
- Tuning knobs in `social_trend_analyzer/config.py` → `DEFAULT_CONFIG`:
  `leadgen_google_maps_actor`, `leadgen_leads_finder_actor`, `leadgen_gmaps_max_places`
  (50), `leadgen_gmaps_scrape_contacts` (true), `leadgen_leads_fetch_count` (100 —
  the free-plan ceiling), `leadgen_poll_interval_seconds`/`leadgen_poll_max_attempts`.

## Endpoints

### 1) Generate filters — `POST /leadgen/filters`
```json
{ "product_description": "specialty coffee roaster wholesale to cafes",
  "actors": ["google_maps", "leads_finder"],
  "location": "Austin, Texas, USA" }
```
Returns a `draft_id`, a `recommended_actors` list + `rationale`, and the proposed
filters (keys match each actor's Apify input exactly, so you can edit any value):
```json
{ "draft_id": "…", "recommended_actors": ["google_maps"],
  "rationale": "Targets are local cafes → Google Maps.",
  "google_maps": { "searchStringsArray": ["cafe", "coffee shop"],
                   "locationQuery": "Austin, Texas, USA",
                   "maxCrawledPlacesPerSearch": 50, "scrapeContacts": true, … },
  "leads_finder": { "fetch_count": 100, "contact_job_title": ["Owner"],
                    "seniority_level": ["Owner"], "company_industry": ["Food & Beverage"], … } }
```

`GET /leadgen/filters/{draft_id}` re-fetches a saved draft.

### 2) Approve & run — `POST /leadgen/runs`  → `202`
Requires a **`campaign_id`** (foreign key — the campaign this run belongs to). Pass the
(edited) filters inline, **or** reference the `draft_id`. `actors` selects what to run —
**pass one key to run a single actor**:
```json
{ "campaign_id": "camp_123", "actors": ["google_maps"], "draft_id": "…" }
```
or fully inline:
```json
{ "campaign_id": "camp_123", "actors": ["google_maps"],
  "google_maps": { "searchStringsArray": ["cafe"], "locationQuery": "Austin", "maxCrawledPlacesPerSearch": 25 } }
```
Each selected actor must have filters (inline or from the draft) or the request is 400;
`campaign_id` and a non-empty `actors` are required (else 422).

On start, a new document is created in the **`lead_runs`** collection holding
`{ run_id, campaign_id, status, stage, actors, filters, progress, total_leads, … }`.
Returns `{ "run_id", "campaign_id", "status": "running", "actors": [...] }` **immediately** —
the actual scraping runs in the background and **can take several minutes**. Keep the
`run_id`; fetch leads with it once the run completes.

### 3) List a campaign's runs — `GET /leadgen/campaigns/{campaign_id}/runs`
All runs for a campaign, newest first — use this to build a **run dropdown**. Filter to
`status == "completed"` to show only runs whose leads are ready.
```json
{ "campaign_id": "camp_123", "count": 2,
  "runs": [ { "run_id": "…", "status": "completed", "stage": "done",
              "actors": ["google_maps"], "total_leads": 42,
              "started_at": "…", "completed_at": "…" } ] }
```

### 4) Progress — `GET /leadgen/runs/{run_id}`
```json
{ "run_id": "…", "campaign_id": "camp_123", "status": "running", "stage": "google_maps",
  "actors": ["google_maps","leads_finder"], "filters": { "google_maps": { … } },
  "progress": { "google_maps": {"status":"completed","count":42,"error":null},
                "leads_finder": {"status":"running","count":0,"error":null} },
  "total_leads": 42, "started_at": "…", "completed_at": null, "errors": [] }
```
`status`: `running` → `completed` | `stopped` | `failed`. One actor failing is recorded
in `errors`/`progress` and does not stop the other actor.

### 5) Results — `GET /leadgen/runs/{run_id}/results`
Call once `status == "completed"`. Reads the **`leads`** collection filtered by `run_id`
(raw Apify payload kept in Mongo but omitted here):
```json
{ "run_id": "…", "status": "completed", "total_leads": 42,
  "leads": [ { "source": "google_maps", "name": "Blue Bottle", "phone": "…",
               "website": "…", "emails": ["…"], "rating": 4.6, … } ],
  "errors": [] }
```

### 6) Cancel — `POST /leadgen/runs/{run_id}/cancel`
Stops before the next actor starts.

## Storage (MongoDB)
- `leadgen_filter_drafts` — generated filter sets (by `draft_id`).
- **`lead_runs`** — one doc per run, foreign key **`campaign_id`**; holds status, stage,
  `actors`, the `filters` used, per-actor `progress`, `total_leads`, timestamps, `errors`
  (single source of truth for progress).
- **`leads`** — one doc per lead, foreign key **`run_id`** (plus denormalized
  `campaign_id`): `{ run_id, campaign_id, source, lead (normalized), raw, created_at }`.

## Tests
`uv run pytest tests/leadgen -v` — mock-based, offline (OpenAI disabled, Apify monkeypatched).
