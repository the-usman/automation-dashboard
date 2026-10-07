"""Lead Generation workflow — description → Apify actor filters → leads.

A free-text product/services description is turned into filters for two Apify
actors (Google Maps local-business scraper and the B2B Leads Finder) via an LLM,
reviewed/approved, then the selected actor(s) are run and their leads stored.

Reads only — never touches any logged-in account (contrast the growth agent). All
integrations are guarded so importing works without API keys.

Public API:
    from leadgen import generate_filters, run_google_maps, run_leads_finder
"""
from .actors import (
    NORMALIZERS,
    RUNNERS,
    normalize_lead,
    normalize_place,
    run_google_maps,
    run_leads_finder,
)
from .filters import VALID_ACTORS, generate_filters
from .schemas import GoogleMapsFilters, LeadFilterSet, LeadsFinderFilters

__all__ = [
    "generate_filters",
    "VALID_ACTORS",
    "run_google_maps",
    "run_leads_finder",
    "normalize_place",
    "normalize_lead",
    "NORMALIZERS",
    "RUNNERS",
    "GoogleMapsFilters",
    "LeadsFinderFilters",
    "LeadFilterSet",
]
