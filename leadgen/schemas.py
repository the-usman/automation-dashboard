"""Pydantic models for the Lead Generation workflow.

Two Apify actors are driven from a free-text product/services description:

* **Google Maps** (``compass~crawler-google-places``) — local businesses.
* **Leads Finder** (``code_crafter~leads-finder``) — B2B decision-makers.

The filter models are bound to ``.with_structured_output`` (same style as
``social_trend_analyzer/schemas.py`` / ``growth/schemas.py``) so an LLM fills the
actor inputs directly from the description. Field names match each actor's input
schema **exactly** — they are forwarded verbatim as the Apify run input.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator

# --------------------------------------------------------------------------- #
# Leads Finder enum vocabularies (from the actor's input schema). Generation
# normalizes LLM output against these so an off-vocabulary value is dropped
# rather than sent to the actor.
# --------------------------------------------------------------------------- #
SENIORITY_LEVELS = (
    "Founder", "Owner", "C-Level", "Director", "VP", "Head",
    "Manager", "Senior", "Entry", "Trainee",
)
FUNCTIONAL_LEVELS = (
    "C-Level", "Finance", "Product", "Engineering", "Design", "HR", "IT",
    "Legal", "Marketing", "Operations", "Sales", "Support",
)
EMAIL_STATUSES = ("validated", "not_validated", "unknown")
FUNDING_ROUNDS = (
    "Seed", "Angel", "Series A", "Series B", "Series C", "Series D",
    "Series E", "Series F", "Venture", "Debt", "Convertible", "PE", "Other",
)

# --------------------------------------------------------------------------- #
# Google Maps scraper (compass/crawler-google-places) enum vocabularies.
# --------------------------------------------------------------------------- #
GMAPS_SEARCH_MATCHING = ("all", "only_includes", "only_exact")
GMAPS_MIN_STARS = ("", "two", "twoAndHalf", "three", "threeAndHalf", "four", "fourAndHalf")
GMAPS_WEBSITE = ("allPlaces", "withWebsite", "withoutWebsite")
GMAPS_LEADS_DEPARTMENTS = (
    "c_suite", "product", "engineering_technical", "design", "education",
    "finance", "human_resources", "information_technology", "legal",
    "marketing", "medical_health", "operations", "sales", "consulting",
)


def _as_str_list(v: Any) -> list[str]:
    """Coerce scraped/LLM JSON (None, scalar, or list) into a clean str list."""
    if v is None:
        return []
    if isinstance(v, str):
        v = [v]
    if not isinstance(v, (list, tuple)):
        return []
    out: list[str] = []
    for item in v:
        s = str(item).strip()
        if s:
            out.append(s)
    return out


class GoogleMapsFilters(BaseModel):
    """Input for ``compass~crawler-google-places`` (Google Maps scraper).

    Keys match the actor schema and are forwarded as-is to the Apify run.
    """

    searchStringsArray: list[str] = Field(
        default_factory=list,
        description="search terms, e.g. ['coffee roaster', 'specialty cafe'] — the "
        "business types this product/service targets as local-business leads",
    )
    locationQuery: str = Field(
        default="",
        description="free-text location, e.g. 'Austin, Texas, USA' (leave empty for no geo filter)",
    )
    maxCrawledPlacesPerSearch: int = Field(
        default=50, ge=1, description="max places returned per search term (cap for cost control)"
    )
    language: str = Field(default="en", description="results language, ISO code")
    scrapeContacts: bool = Field(
        default=True,
        description="enrich each place with website emails/socials (extra per-place cost)",
    )
    categoryFilterWords: list[str] = Field(
        default_factory=list, description="optional Google category words to restrict matches"
    )
    searchMatching: str = Field(
        default="all",
        description="how strictly a place name must match the search term: "
        "'all' | 'only_includes' | 'only_exact'",
    )
    placeMinimumStars: str = Field(
        default="",
        description="only scrape places at/above this rating: '' (any) | 'two' | 'twoAndHalf' | "
        "'three' | 'threeAndHalf' | 'four' | 'fourAndHalf'",
    )
    website: str = Field(
        default="allPlaces",
        description="filter by website presence: 'allPlaces' | 'withWebsite' | 'withoutWebsite'. "
        "Use 'withWebsite' when you need emails (contact enrichment reads the site).",
    )
    skipClosedPlaces: bool = Field(
        default=False, description="skip temporarily/permanently closed places"
    )
    maximumLeadsEnrichmentRecords: int = Field(
        default=0, ge=0,
        description="per-place employee leads to extract (name/title/email/LinkedIn); 0 = off "
        "(extra cost add-on). Set >0 only for B2B employee-level prospecting.",
    )
    leadsEnrichmentDepartments: list[str] = Field(
        default_factory=list,
        description="limit extracted leads to departments (only applies when "
        "maximumLeadsEnrichmentRecords > 0): c_suite, product, engineering_technical, design, "
        "education, finance, human_resources, information_technology, legal, marketing, "
        "medical_health, operations, sales, consulting",
    )
    verifyLeadsEnrichmentEmails: bool = Field(
        default=False, description="verify each extracted lead's email (extra cost)"
    )
    countryCode: str = Field(default="", description="optional ISO country code geo filter")
    city: str = Field(default="", description="optional city geo filter")
    state: str = Field(default="", description="optional state/region geo filter")
    postalCode: str = Field(default="", description="optional postal code geo filter")

    @field_validator(
        "searchStringsArray", "categoryFilterWords", "leadsEnrichmentDepartments", mode="before"
    )
    @classmethod
    def _coerce_lists(cls, v: Any) -> list[str]:
        return _as_str_list(v)

    @field_validator(
        "locationQuery", "language", "searchMatching", "placeMinimumStars", "website",
        "countryCode", "city", "state", "postalCode",
        mode="before",
    )
    @classmethod
    def _none_to_empty(cls, v: Any) -> Any:
        return "" if v is None else v


class LeadsFinderFilters(BaseModel):
    """Input for ``code_crafter~leads-finder`` (B2B decision-maker finder).

    Keys match the actor schema. Per the actor README, ``contact_location`` and
    ``contact_city`` are mutually exclusive — generation drops ``contact_city``
    when both are present.
    """

    fetch_count: int = Field(
        default=100, ge=1, le=100,
        description="max leads to fetch (free Apify plan caps this actor at 100 per run)",
    )
    contact_job_title: list[str] = Field(
        default_factory=list, description="job titles to include, e.g. ['Head of Marketing', 'CMO']"
    )
    contact_not_job_title: list[str] = Field(
        default_factory=list, description="job titles to exclude"
    )
    seniority_level: list[str] = Field(
        default_factory=list,
        description=f"seniority filter; allowed values: {', '.join(SENIORITY_LEVELS)}",
    )
    functional_level: list[str] = Field(
        default_factory=list,
        description=f"department/function filter; allowed values: {', '.join(FUNCTIONAL_LEVELS)}",
    )
    contact_location: list[str] = Field(
        default_factory=list,
        description="region/country/state filters (mutually exclusive with contact_city)",
    )
    contact_city: list[str] = Field(
        default_factory=list,
        description="city-level filters (mutually exclusive with contact_location)",
    )
    contact_not_location: list[str] = Field(default_factory=list, description="locations to exclude")
    contact_not_city: list[str] = Field(default_factory=list, description="cities to exclude")
    email_status: list[str] = Field(
        default_factory=list,
        description=f"email verification filter; allowed values: {', '.join(EMAIL_STATUSES)}",
    )
    company_domain: list[str] = Field(default_factory=list, description="specific company domains")
    size: list[str] = Field(
        default_factory=list,
        description="company size ranges, e.g. ['2-10', '11-20', '21-50'] up to '10000+'",
    )
    company_industry: list[str] = Field(default_factory=list, description="industries to include")
    company_not_industry: list[str] = Field(default_factory=list, description="industries to exclude")
    company_keywords: list[str] = Field(default_factory=list, description="company keywords to include")
    company_not_keywords: list[str] = Field(default_factory=list, description="company keywords to exclude")
    min_revenue: str = Field(default="", description="minimum revenue threshold (string)")
    max_revenue: str = Field(default="", description="maximum revenue threshold (string)")
    funding: list[str] = Field(
        default_factory=list,
        description=f"funding rounds; allowed values: {', '.join(FUNDING_ROUNDS)}",
    )

    @field_validator(
        "contact_job_title", "contact_not_job_title", "seniority_level",
        "functional_level", "contact_location", "contact_city",
        "contact_not_location", "contact_not_city", "email_status",
        "company_domain", "size", "company_industry", "company_not_industry",
        "company_keywords", "company_not_keywords", "funding",
        mode="before",
    )
    @classmethod
    def _coerce_lists(cls, v: Any) -> list[str]:
        return _as_str_list(v)

    @field_validator("min_revenue", "max_revenue", mode="before")
    @classmethod
    def _none_to_empty(cls, v: Any) -> Any:
        return "" if v is None else str(v)


class LeadFilterSet(BaseModel):
    """LLM structured-output target: filters for both actors + a recommendation.

    ``recommended_actors`` lets the model say which actor(s) actually fit the
    described product (local businesses → ``google_maps``; B2B decision-makers →
    ``leads_finder``) while still returning usable filters for both.
    """

    product_description: str = Field(default="", description="echo of the input description")
    google_maps: GoogleMapsFilters | None = Field(
        default=None, description="Google Maps scraper filters (null if not applicable)"
    )
    leads_finder: LeadsFinderFilters | None = Field(
        default=None, description="Leads Finder filters (null if not applicable)"
    )
    recommended_actors: list[str] = Field(
        default_factory=list,
        description="which actor(s) best fit this product: 'google_maps' and/or 'leads_finder'",
    )
    rationale: str = Field(
        default="", description="one or two sentences explaining the recommendation"
    )

    @field_validator("recommended_actors", mode="before")
    @classmethod
    def _coerce_actors(cls, v: Any) -> list[str]:
        return _as_str_list(v)

    @field_validator("product_description", "rationale", mode="before")
    @classmethod
    def _none_to_empty(cls, v: Any) -> Any:
        return "" if v is None else v
