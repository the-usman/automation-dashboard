# Lead Generation — Next.js Frontend Integration Guide

> Backend reference: `app/LEADGEN_API_README.md` (endpoint contract + storage).
> **This doc is the frontend playbook for a Next.js (App Router) app.**

---

## The concept

The operator describes a product/service; the backend proposes Apify **filters**;
the operator reviews/edits them and approves; the selected actor(s) run in the
background and leads land in MongoDB.

```
product/service description
        │  POST /leadgen/filters           (LLM proposes filters — a few seconds)
        ▼
 [Review & edit the filters]  ← user can change any field, or run just one actor
        │  POST /leadgen/runs  (needs campaign_id)   (202 → run_id, runs in background)
        ▼
 [Progress]  poll GET /leadgen/runs/{id}   ("google_maps: 42 ✓, leads_finder: running…")
        ▼
 [Results]   GET /leadgen/runs/{id}/results   (fetch when status == "completed")
```

> **A run belongs to a campaign.** `POST /leadgen/runs` requires a `campaign_id`
> (foreign key). Every run is stored in the `lead_runs` collection under that
> `campaign_id`, so you can later list all runs for a campaign and let the user pick
> one from a dropdown. The generated leads are stored in the `leads` collection keyed
> by `run_id`.
>
> **Lead generation can take several minutes** (Apify scraping). The run endpoint
> returns a `run_id` immediately; the leads are ready once the run's `status` is
> `completed` — fetch them by `run_id`.

Two actors, selected per run via the `actors` array:

| Actor key      | Finds                    | Typical lead fields                              |
|----------------|--------------------------|--------------------------------------------------|
| `google_maps`  | Local businesses         | name, address, phone, website, emails, rating    |
| `leads_finder` | B2B decision-makers      | full_name, job_title, email, linkedin, company   |

> **Run just one:** pass `actors: ["google_maps"]` (or `["leads_finder"]`) to the
> run endpoint. Pass both to run both sequentially.

- Base URL: `${API_BASE}` (dev: `http://localhost:8001`)
- Content type: `application/json`
- Interactive docs: `${API_BASE}/docs`
- CORS is open by default (`CORS_ALLOW_ORIGINS=*`); set it to your frontend origin in prod.

---

## Endpoint summary

| # | Method & path | Purpose | Returns |
|---|---------------|---------|---------|
| 1 | `POST /leadgen/filters` | Description → proposed filters + `draft_id` | `200` filters |
| 2 | `GET /leadgen/filters/{draft_id}` | Re-fetch a saved draft | `200` filters |
| 3 | `POST /leadgen/runs` | Approve & run selected actor(s) (needs `campaign_id`) | `202` `{ run_id }` |
| 4 | `GET /leadgen/campaigns/{campaign_id}/runs` | All runs for a campaign (**dropdown**) | `200` run list |
| 5 | `GET /leadgen/runs/{run_id}` | Progress (per-actor) | `200` status |
| 6 | `GET /leadgen/runs/{run_id}/results` | Stored leads (when `completed`) | `200` leads |
| 7 | `POST /leadgen/runs/{run_id}/cancel` | Stop a running run | `200` ack |

---

## 0. Setup

`.env.local` (frontend):
```bash
# Browser-exposed if you call the API directly from the client:
NEXT_PUBLIC_API_BASE=http://localhost:8001
# OR keep it server-only and proxy through a route handler (recommended, see §Proxy):
API_BASE=http://localhost:8001
```

### TypeScript types (`lib/leadgen/types.ts`)

Filter keys match the Apify actor inputs exactly, so they round-trip generate → edit → run.

```ts
export type Actor = "google_maps" | "leads_finder";

export interface GoogleMapsFilters {
  searchStringsArray: string[];
  locationQuery: string;
  maxCrawledPlacesPerSearch: number;
  language: string;
  scrapeContacts: boolean;
  categoryFilterWords: string[];
  countryCode: string;
  city: string;
  state: string;
  postalCode: string;
}

export interface LeadsFinderFilters {
  fetch_count: number;
  contact_job_title: string[];
  contact_not_job_title: string[];
  seniority_level: string[];   // Founder, Owner, C-Level, Director, VP, Head, Manager, Senior, Entry, Trainee
  functional_level: string[];  // C-Level, Finance, Product, Engineering, Design, HR, IT, Legal, Marketing, Operations, Sales, Support
  contact_location: string[];  // mutually exclusive with contact_city
  contact_city: string[];
  contact_not_location: string[];
  contact_not_city: string[];
  email_status: string[];      // validated | not_validated | unknown
  company_domain: string[];
  size: string[];              // "2-10", "11-20", … "10000+"
  company_industry: string[];
  company_not_industry: string[];
  company_keywords: string[];
  company_not_keywords: string[];
  min_revenue: string;
  max_revenue: string;
  funding: string[];           // Seed, Angel, Series A–F, Venture, Debt, Convertible, PE, Other
}

export interface FiltersResponse {
  draft_id: string;
  product_description: string;
  recommended_actors: Actor[];
  rationale: string;
  google_maps: GoogleMapsFilters | null;
  leads_finder: LeadsFinderFilters | null;
  errors: Record<string, unknown>[];
}

export interface ActorProgress { status: "pending" | "running" | "completed" | "failed"; count: number; error: string | null; }

export interface RunStatus {
  run_id: string;
  campaign_id: string;
  status: "running" | "completed" | "stopped" | "failed";
  stage: string;                       // "queued" | "google_maps" | "leads_finder" | "done"
  actors: Actor[];
  filters: Record<string, unknown>;    // the filters this run used
  progress: Record<Actor, ActorProgress>;
  total_leads: number;
  started_at: string;
  completed_at: string | null;
  errors: { actor: string; error_message: string }[];
}

export interface RunListItem {
  run_id: string;
  status: RunStatus["status"];
  stage: string;
  actors: Actor[];
  total_leads: number;
  started_at: string;
  completed_at: string | null;
}

export interface RunList {
  campaign_id: string;
  count: number;
  runs: RunListItem[];
}

export interface RunResults {
  run_id: string;
  status: RunStatus["status"];
  total_leads: number;
  leads: Array<{ source: Actor; created_at: string; [field: string]: unknown }>;
  errors: { actor: string; error_message: string }[];
}
```

### API client (`lib/leadgen/client.ts`)

```ts
import type { Actor, FiltersResponse, GoogleMapsFilters, LeadsFinderFilters, RunList, RunResults, RunStatus } from "./types";

const BASE = process.env.NEXT_PUBLIC_API_BASE ?? "/api/leadgen-proxy"; // see §Proxy

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    // FastAPI: string for 404/400, array for 422 validation errors
    const detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail ?? res.statusText);
    throw new Error(detail);
  }
  return res.json() as Promise<T>;
}

export const leadgen = {
  generateFilters: (body: { product_description: string; actors?: Actor[]; location?: string }) =>
    fetch(`${BASE}/leadgen/filters`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    }).then(json<FiltersResponse>),

  getDraft: (draftId: string) =>
    fetch(`${BASE}/leadgen/filters/${draftId}`).then(json<FiltersResponse>),

  startRun: (body: {
    campaign_id: string;                 // required — the campaign this run belongs to
    actors: Actor[];
    draft_id?: string;
    product_description?: string;
    google_maps?: GoogleMapsFilters;
    leads_finder?: LeadsFinderFilters;
  }) =>
    fetch(`${BASE}/leadgen/runs`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    }).then(json<{ run_id: string; campaign_id: string; status: string; actors: Actor[] }>),

  listCampaignRuns: (campaignId: string) =>
    fetch(`${BASE}/leadgen/campaigns/${campaignId}/runs`).then(json<RunList>),

  getRun: (runId: string) => fetch(`${BASE}/leadgen/runs/${runId}`).then(json<RunStatus>),
  getResults: (runId: string) => fetch(`${BASE}/leadgen/runs/${runId}/results`).then(json<RunResults>),
  cancel: (runId: string) => fetch(`${BASE}/leadgen/runs/${runId}/cancel`, { method: "POST" }).then(json),
};
```

---

## 1. Generate filters — `POST /leadgen/filters`

Synchronous (calls the LLM — show a spinner; usually a few seconds).

**Request**
```jsonc
{
  "product_description": "specialty coffee roaster, wholesale to cafes", // required, min 1 char
  "actors": ["google_maps", "leads_finder"],   // which to generate for (default both)
  "location": "Austin, Texas, USA"              // optional hint
}
```

**Response `200`**
```jsonc
{
  "draft_id": "8f3c…",
  "product_description": "specialty coffee roaster, wholesale to cafes",
  "recommended_actors": ["google_maps"],
  "rationale": "Targets are local cafes → Google Maps fits best.",
  "google_maps": {
    "searchStringsArray": ["cafe", "coffee shop", "espresso bar"],
    "locationQuery": "Austin, Texas, USA",
    "maxCrawledPlacesPerSearch": 50,
    "language": "en", "scrapeContacts": true,
    "categoryFilterWords": [], "countryCode": "", "city": "", "state": "", "postalCode": ""
  },
  "leads_finder": {
    "fetch_count": 100, "contact_job_title": ["Owner", "Buyer"],
    "seniority_level": ["Owner"], "company_industry": ["Food & Beverage"],
    "company_keywords": ["cafe", "coffee"], "size": ["2-10", "11-20"]
    /* … all other keys present with defaults … */
  },
  "errors": []
}
```

> Render the returned filters into an editable form. `recommended_actors` + `rationale`
> drive a "we suggest running X" hint — but the operator chooses what to actually run.

---

## 2. Approve & run — `POST /leadgen/runs` → `202`

Requires a **`campaign_id`** (the campaign this run belongs to). Send the (edited)
filters **inline**, or reference the `draft_id` (inline wins when both are present).
`actors` selects what runs.

**Request (inline, after editing)**
```jsonc
{
  "campaign_id": "camp_123",                  // ← required foreign key
  "actors": ["google_maps"],                  // ← run ONE actor
  "google_maps": { "searchStringsArray": ["cafe"], "locationQuery": "Austin", "maxCrawledPlacesPerSearch": 25 }
}
```

**Request (use the unedited draft)**
```jsonc
{ "campaign_id": "camp_123", "actors": ["google_maps", "leads_finder"], "draft_id": "8f3c…" }
```

**Response `202`** — returns instantly; the run row is created in `lead_runs` and the
scrape proceeds in the background (**can take several minutes**). Store the `run_id`.
```jsonc
{ "run_id": "a1b2…", "campaign_id": "camp_123", "status": "running", "actors": ["google_maps"] }
```

> `422` if `campaign_id` or `actors` is missing; `422`/`400` if a selected actor has
> neither inline filters nor a draft that contains them.

---

## 2b. Pick a past run — `GET /leadgen/campaigns/{campaign_id}/runs`

Lists every run for a campaign, newest first — use it to populate a **run dropdown**.
Filter to `status === "completed"` to offer only runs whose leads are ready to view.

```jsonc
{
  "campaign_id": "camp_123", "count": 2,
  "runs": [
    { "run_id": "a1b2…", "status": "completed", "stage": "done",
      "actors": ["google_maps"], "total_leads": 42,
      "started_at": "2026-10-07T10:00:00+00:00", "completed_at": "2026-10-07T10:06:00+00:00" },
    { "run_id": "c3d4…", "status": "running", "stage": "leads_finder",
      "actors": ["google_maps","leads_finder"], "total_leads": 12, "started_at": "…", "completed_at": null }
  ]
}
```

```tsx
"use client";
import { useEffect, useState } from "react";
import { leadgen } from "@/lib/leadgen/client";
import type { RunListItem, RunResults } from "@/lib/leadgen/types";

export function RunPicker({ campaignId }: { campaignId: string }) {
  const [runs, setRuns] = useState<RunListItem[]>([]);
  const [results, setResults] = useState<RunResults | null>(null);

  useEffect(() => {
    leadgen.listCampaignRuns(campaignId).then((r) =>
      // offer only completed runs (their leads are ready)
      setRuns(r.runs.filter((x) => x.status === "completed"))
    );
  }, [campaignId]);

  return (
    <div>
      <select onChange={(e) => e.target.value && leadgen.getResults(e.target.value).then(setResults)} defaultValue="">
        <option value="" disabled>Select a run…</option>
        {runs.map((r) => (
          <option key={r.run_id} value={r.run_id}>
            {new Date(r.started_at).toLocaleString()} — {r.actors.join(", ")} — {r.total_leads} leads
          </option>
        ))}
      </select>
      {results && <LeadsTable leads={results.leads} />}
    </div>
  );
}
```

---

## 3. Progress — `GET /leadgen/runs/{run_id}`

Poll every ~3–5 s until `status` is terminal. Runs can take minutes (Apify scraping).

```jsonc
{
  "run_id": "a1b2…",
  "status": "running",                 // running | completed | stopped | failed
  "stage": "google_maps",              // queued | google_maps | leads_finder | done
  "actors": ["google_maps", "leads_finder"],
  "progress": {
    "google_maps":  { "status": "completed", "count": 42, "error": null },
    "leads_finder": { "status": "running",   "count": 0,  "error": null }
  },
  "total_leads": 42,
  "started_at": "2026-10-07T10:00:00+00:00",
  "completed_at": null,
  "errors": []
}
```

| Status | Terminal? | Meaning |
|--------|-----------|---------|
| `running` | no | in progress |
| `completed` | yes | finished (check `errors[]` for soft notes like "0 leads returned") |
| `stopped` | yes | cancelled |
| `failed` | yes | fatal error |

> One actor failing is recorded in that actor's `progress.error` + `errors[]`; the
> other actor still runs. So `completed` can coexist with a non-empty `errors[]`.

---

## 4. Results — `GET /leadgen/runs/{run_id}/results`

Normalized leads (raw Apify payload is kept in Mongo but omitted here). Each lead
carries `source` so you can render Google Maps vs. Leads Finder rows differently.

```jsonc
{
  "run_id": "a1b2…", "status": "completed", "total_leads": 42,
  "leads": [
    { "source": "google_maps", "created_at": "…", "name": "Blue Bottle", "phone": "+1…",
      "website": "https://…", "emails": ["hi@…"], "rating": 4.6, "reviews_count": 321 },
    { "source": "leads_finder", "created_at": "…", "full_name": "Ada Lovelace", "job_title": "CMO",
      "email": "ada@acme.com", "linkedin": "https://…", "company_name": "Acme", "company_domain": "acme.com" }
  ],
  "errors": []
}
```

---

## 5. Cancel — `POST /leadgen/runs/{run_id}/cancel`

Cooperative stop (halts before the next actor starts). Keep polling until `stopped`.

```jsonc
{ "run_id": "a1b2…", "status": "stopping", "message": "Stop signal sent. Poll GET /leadgen/runs/{id} to confirm." }
```

---

## Proxy (recommended for Next.js)

Keep `API_BASE` server-side and avoid CORS/secret exposure by proxying through an
App Router route handler. Then the client uses `BASE = "/api/leadgen-proxy"`.

`app/api/leadgen-proxy/[...path]/route.ts`:
```ts
const API_BASE = process.env.API_BASE!; // server-only env

async function proxy(req: Request, { params }: { params: { path: string[] } }) {
  const url = `${API_BASE}/${params.path.join("/")}${new URL(req.url).search}`;
  const res = await fetch(url, {
    method: req.method,
    headers: { "Content-Type": "application/json" },
    body: ["GET", "HEAD"].includes(req.method) ? undefined : await req.text(),
    cache: "no-store",
  });
  return new Response(res.body, { status: res.status, headers: { "Content-Type": "application/json" } });
}
export { proxy as GET, proxy as POST };
```
Client calls `/api/leadgen-proxy/leadgen/filters`, etc.

---

## React: the full flow (client component)

```tsx
"use client";
import { useRef, useState } from "react";
import { leadgen } from "@/lib/leadgen/client";
import type { Actor, FiltersResponse, RunResults, RunStatus } from "@/lib/leadgen/types";

const TERMINAL = new Set(["completed", "stopped", "failed"]);

export function LeadgenWizard({ campaignId }: { campaignId: string }) {
  const [draft, setDraft] = useState<FiltersResponse | null>(null);
  const [status, setStatus] = useState<RunStatus | null>(null);
  const [results, setResults] = useState<RunResults | null>(null);
  const [busy, setBusy] = useState(false);
  const runIdRef = useRef<string | null>(null);

  async function onGenerate(product_description: string, location: string) {
    setBusy(true);
    try { setDraft(await leadgen.generateFilters({ product_description, location })); }
    finally { setBusy(false); }
  }

  // `edited` is the (possibly modified) filters from your form; `actors` is the subset to run.
  async function onRun(actors: Actor[], edited: Pick<FiltersResponse, "google_maps" | "leads_finder">) {
    if (!draft) return;
    const { run_id } = await leadgen.startRun({
      campaign_id: campaignId,                 // ← run is tied to this campaign
      actors,
      google_maps: actors.includes("google_maps") ? edited.google_maps ?? undefined : undefined,
      leads_finder: actors.includes("leads_finder") ? edited.leads_finder ?? undefined : undefined,
      product_description: draft.product_description,
    });
    runIdRef.current = run_id;
    poll(run_id);
  }

  async function poll(runId: string) {
    const s = await leadgen.getRun(runId);
    setStatus(s);
    if (TERMINAL.has(s.status)) { setResults(await leadgen.getResults(runId)); return; }
    setTimeout(() => poll(runId), 4000);
  }

  async function onCancel() { if (runIdRef.current) await leadgen.cancel(runIdRef.current); }

  // render: form → onGenerate → editable filter form → onRun → progress (status.progress) → results
  return null;
}
```

**Suggested stages → UI**

| stage | UI |
|-------|----|
| `queued` | "Starting…" |
| `google_maps` | "Scraping Google Maps…" + `progress.google_maps.count` |
| `leads_finder` | "Finding decision-makers…" + `progress.leads_finder.count` |
| `done` | results table |

---

## Error handling cheat sheet

| Status | When | Frontend action |
|--------|------|-----------------|
| `400` | selected actor has no filters & no usable draft | show message, keep user on edit step |
| `404` | unknown `draft_id` / `run_id` | stop polling, show "not found" |
| `422` | body fails schema (empty description, missing `campaign_id`, bad `actors`) | show field errors from `detail[]` |
| `5xx` | server error | retry; run-level failures surface as `status:"failed"` + `errors[]` |

FastAPI returns `{ "detail": "…" }` (string) for 400/404 and `{ "detail": [ … ] }` (array) for 422.

---

## Notes & gotchas

- **Generate is sync, runs are async.** Only `/leadgen/filters` blocks (LLM); after
  `/leadgen/runs` you get a `run_id` instantly and must poll.
- **Edit before approve.** The whole point of the two-step flow: show the generated
  filters, let the user change them, send the edited set to `/runs`.
- **`completed` with `errors[]` is normal** — e.g. `"0 leads returned — check
  TREND_APIFY_TOKEN…"`. Show it as an info banner, not a crash.
- **Cost awareness.** Google Maps `scrapeContacts: true` and higher
  `maxCrawledPlacesPerSearch` cost more; `leads_finder.fetch_count` is capped at 100
  on the free Apify plan. Consider surfacing these as visible, editable fields.
- **Source of truth is the poll/results reads** (MongoDB), not the start response.
- **Runs are per-campaign.** Every run is stored under its `campaign_id` in `lead_runs`;
  list them via `GET /leadgen/campaigns/{campaign_id}/runs` to build the run dropdown.
- **A run can take minutes** — you get the `run_id` instantly, but only fetch
  `/results` once `status === "completed"` (or filter the dropdown to completed runs).
- **Leads can be large** — paginate/virtualize the results table; `total_leads` gives the count.
```
