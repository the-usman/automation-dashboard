"""Run the two lead-generation Apify actors and normalize their output.

Thin wrappers over :func:`social_trend_analyzer.collectors.apify.run_actor_sync`
(the same start→poll→fetch loop the growth discovery uses). Guarded: with no
Apify token / actor id, ``run_actor_sync`` returns ``[]`` and these return ``[]``.

Normalization keeps a curated, flat subset of useful fields per source while the
caller stores the full ``raw`` dict alongside it.
"""
from __future__ import annotations

import logging
from typing import Any

from social_trend_analyzer.collectors.apify import run_actor_sync

log = logging.getLogger(__name__)


def _google_maps_actor(config: dict) -> str:
    return config.get("leadgen_google_maps_actor") or "compass~crawler-google-places"


def _leads_finder_actor(config: dict) -> str:
    return config.get("leadgen_leads_finder_actor") or "code_crafter~leads-finder"


def _poll_kwargs(config: dict) -> dict:
    return {
        "poll_interval": int(config.get("leadgen_poll_interval_seconds", 15)),
        "max_attempts": int(config.get("leadgen_poll_max_attempts", 80)),
    }


def _drop_empty(d: dict) -> dict:
    """Drop keys whose value is empty so the actor gets a lean input."""
    return {k: v for k, v in d.items() if v not in (None, "", [], {})}


def run_google_maps(filters: dict, config: dict) -> list[dict]:
    """Run the Google Maps scraper with ``filters`` (actor input keys) → raw items."""
    actor_input = _drop_empty(dict(filters or {}))
    if not actor_input.get("searchStringsArray"):
        log.warning("run_google_maps: no searchStringsArray — nothing to scrape")
        return []
    return run_actor_sync(_google_maps_actor(config), actor_input, **_poll_kwargs(config))


def run_leads_finder(filters: dict, config: dict) -> list[dict]:
    """Run the Leads Finder actor with ``filters`` (actor input keys) → raw items."""
    actor_input = _drop_empty(dict(filters or {}))
    return run_actor_sync(_leads_finder_actor(config), actor_input, **_poll_kwargs(config))


# --------------------------------------------------------------------------- #
# Normalizers — curated flat subset per source (raw dict kept separately).
# --------------------------------------------------------------------------- #
def _first(item: dict, *keys: str) -> Any:
    for k in keys:
        v = item.get(k)
        if v not in (None, "", [], {}):
            return v
    return ""


def normalize_place(raw: dict) -> dict:
    """Pick the useful fields from a Google Maps place item."""
    return {
        "name": _first(raw, "title", "name"),
        "category": _first(raw, "categoryName", "category"),
        "address": _first(raw, "address", "street"),
        "city": raw.get("city", ""),
        "state": raw.get("state", ""),
        "country": _first(raw, "countryCode", "country"),
        "postal_code": raw.get("postalCode", ""),
        "phone": _first(raw, "phone", "phoneUnformatted"),
        "website": raw.get("website", ""),
        "domain": raw.get("domain", ""),
        "emails": _first(raw, "emails", "email") or [],
        "rating": _first(raw, "totalScore", "rating"),
        "reviews_count": _first(raw, "reviewsCount", "reviews"),
        "place_id": raw.get("placeId", ""),
        "url": _first(raw, "url", "googleMapsUrl"),
    }


def normalize_lead(raw: dict) -> dict:
    """Pick the useful fields from a Leads Finder person item."""
    return {
        "first_name": raw.get("first_name", ""),
        "last_name": raw.get("last_name", ""),
        "full_name": _first(raw, "full_name", "name"),
        "job_title": raw.get("job_title", ""),
        "seniority_level": raw.get("seniority_level", ""),
        "email": raw.get("email", ""),
        "personal_email": raw.get("personal_email", ""),
        "mobile_number": raw.get("mobile_number", ""),
        "linkedin": _first(raw, "linkedin", "linkedin_url"),
        "city": raw.get("city", ""),
        "state": raw.get("state", ""),
        "country": raw.get("country", ""),
        "company_name": raw.get("company_name", ""),
        "company_domain": raw.get("company_domain", ""),
        "company_website": raw.get("company_website", ""),
        "company_linkedin": raw.get("company_linkedin", ""),
        "company_size": raw.get("company_size", ""),
        "industry": raw.get("industry", ""),
        "company_phone": raw.get("company_phone", ""),
        "company_technologies": raw.get("company_technologies", []),
    }


NORMALIZERS = {"google_maps": normalize_place, "leads_finder": normalize_lead}
RUNNERS = {"google_maps": run_google_maps, "leads_finder": run_leads_finder}
