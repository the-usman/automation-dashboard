"""Request/response models for the Lead Generation API.

Mirrors the style of ``growth_models.py``: separate request/response schemas, no
secret leakage. The filter models are API-facing mirrors of the package schemas in
``leadgen/schemas.py`` — their keys match the Apify actor
inputs so they round-trip verbatim through generate → (edit) → run.
"""
from __future__ import annotations

from pydantic import BaseModel, Field, field_validator, model_validator

VALID_ACTORS = ("google_maps", "leads_finder")


# ── Actor filter models (API-facing; keys == Apify actor input keys) ──────────
class GoogleMapsFiltersModel(BaseModel):
    searchStringsArray: list[str] = Field(default_factory=list)
    locationQuery: str = ""
    maxCrawledPlacesPerSearch: int = 50
    language: str = "en"
    scrapeContacts: bool = True
    categoryFilterWords: list[str] = Field(default_factory=list)
    searchMatching: str = "all"          # all | only_includes | only_exact
    placeMinimumStars: str = ""          # "" | two | twoAndHalf | three | threeAndHalf | four | fourAndHalf
    website: str = "allPlaces"           # allPlaces | withWebsite | withoutWebsite
    skipClosedPlaces: bool = False
    maximumLeadsEnrichmentRecords: int = 0   # >0 = per-place employee leads (extra cost)
    leadsEnrichmentDepartments: list[str] = Field(default_factory=list)
    verifyLeadsEnrichmentEmails: bool = False
    countryCode: str = ""
    city: str = ""
    state: str = ""
    postalCode: str = ""


class LeadsFinderFiltersModel(BaseModel):
    fetch_count: int = 100
    contact_job_title: list[str] = Field(default_factory=list)
    contact_not_job_title: list[str] = Field(default_factory=list)
    seniority_level: list[str] = Field(default_factory=list)
    functional_level: list[str] = Field(default_factory=list)
    contact_location: list[str] = Field(default_factory=list)
    contact_city: list[str] = Field(default_factory=list)
    contact_not_location: list[str] = Field(default_factory=list)
    contact_not_city: list[str] = Field(default_factory=list)
    email_status: list[str] = Field(default_factory=list)
    company_domain: list[str] = Field(default_factory=list)
    size: list[str] = Field(default_factory=list)
    company_industry: list[str] = Field(default_factory=list)
    company_not_industry: list[str] = Field(default_factory=list)
    company_keywords: list[str] = Field(default_factory=list)
    company_not_keywords: list[str] = Field(default_factory=list)
    min_revenue: str = ""
    max_revenue: str = ""
    funding: list[str] = Field(default_factory=list)


def _clean_actors(v: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for a in v or []:
        a = str(a).strip().lower()
        if a in VALID_ACTORS and a not in seen:
            seen.add(a)
            out.append(a)
    return out


# ── Generate filters (synchronous) ────────────────────────────────────────────
class GenerateFiltersRequest(BaseModel):
    """Describe the product/services; the LLM proposes filters for approval."""

    product_description: str = Field(..., min_length=1, description="what you sell / who you target")
    actors: list[str] = Field(
        default_factory=lambda: list(VALID_ACTORS),
        description="which actors to generate filters for: 'google_maps' and/or 'leads_finder'",
    )
    location: str = Field("", description="optional location hint, e.g. 'Austin, Texas, USA'")

    @field_validator("actors")
    @classmethod
    def _norm(cls, v: list[str]) -> list[str]:
        return _clean_actors(v) or list(VALID_ACTORS)


class GenerateFiltersResponse(BaseModel):
    draft_id: str
    product_description: str
    recommended_actors: list[str]
    rationale: str = ""
    google_maps: GoogleMapsFiltersModel | None = None
    leads_finder: LeadsFinderFiltersModel | None = None
    errors: list[dict] = Field(default_factory=list)


# ── Run actors (background) ───────────────────────────────────────────────────
class RunRequest(BaseModel):
    """Approve & run. Select which actors to run; supply their (possibly edited)
    filters inline, OR reference a ``draft_id`` to load the generated filters.

    ``actors`` is the "run just one" control — e.g. ``["google_maps"]``.
    """

    campaign_id: str = Field(..., min_length=1, description="foreign key — the campaign this run belongs to")
    actors: list[str] = Field(..., min_length=1, description="actors to run: 'google_maps'/'leads_finder'")
    draft_id: str = Field("", description="load filters from a saved draft when inline filters are omitted")
    product_description: str = Field("", description="optional label stored on the run")
    google_maps: GoogleMapsFiltersModel | None = None
    leads_finder: LeadsFinderFiltersModel | None = None

    @field_validator("actors")
    @classmethod
    def _norm(cls, v: list[str]) -> list[str]:
        cleaned = _clean_actors(v)
        if not cleaned:
            raise ValueError("actors must contain 'google_maps' and/or 'leads_finder'")
        return cleaned

    @model_validator(mode="after")
    def _require_filters(self) -> "RunRequest":
        # Each selected actor needs filters, either inline here or via a draft_id.
        if self.draft_id:
            return self
        missing = [a for a in self.actors if getattr(self, a) is None]
        if missing:
            raise ValueError(
                f"no filters for actor(s) {missing}; provide them inline or pass a draft_id"
            )
        return self


class RunCreatedResponse(BaseModel):
    run_id: str
    campaign_id: str
    status: str            # "running"
    actors: list[str]


class ActorProgress(BaseModel):
    status: str = "pending"   # pending | running | completed | failed
    count: int = 0            # leads stored
    error: str | None = None


class RunStatusResponse(BaseModel):
    run_id: str
    campaign_id: str = ""
    status: str            # "running" | "completed" | "stopped" | "failed"
    stage: str             # e.g. "google_maps" | "leads_finder" | "done"
    actors: list[str]
    filters: dict = Field(default_factory=dict)   # the actor filters this run used
    progress: dict[str, ActorProgress] = Field(default_factory=dict)
    total_leads: int = 0
    started_at: str
    completed_at: str | None = None
    errors: list[dict] = Field(default_factory=list)


# ── List a campaign's runs (for a "pick a run" dropdown) ──────────────────────
class RunListItem(BaseModel):
    run_id: str
    status: str
    stage: str = ""
    actors: list[str] = Field(default_factory=list)
    total_leads: int = 0
    started_at: str = ""
    completed_at: str | None = None


class RunListResponse(BaseModel):
    campaign_id: str
    count: int
    runs: list[RunListItem] = Field(default_factory=list)


class RunResultsResponse(BaseModel):
    run_id: str
    status: str
    total_leads: int
    leads: list[dict] = Field(default_factory=list)
    errors: list[dict] = Field(default_factory=list)
