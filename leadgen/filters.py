"""Description → Apify actor filters (LLM, guarded).

Turns a free-text product/services description into:

* ``google_maps`` filters for the Google Maps scraper, and
* ``leads_finder`` filters for the B2B Leads Finder,

plus a recommendation of which actor(s) fit. Mirrors ``growth/competitors.py``:
a ``ChatPromptTemplate | get_chat(...).with_structured_output(...)`` chain with a
deterministic fallback so it runs offline (no OpenAI key → keyword-seeded filters).

Generation always post-processes the result to enforce the actors' hard rules
(cost caps, the ``contact_location`` XOR ``contact_city`` rule, and the
Leads Finder enum vocabularies) regardless of what the LLM returned.
"""
from __future__ import annotations

import logging

from langchain_core.prompts import ChatPromptTemplate

from social_trend_analyzer.config import get_settings
from social_trend_analyzer.growth.competitors import _fallback_keywords  # reuse deterministic keyword split
from social_trend_analyzer.llm import get_chat
from .schemas import (
    EMAIL_STATUSES,
    FUNCTIONAL_LEVELS,
    FUNDING_ROUNDS,
    SENIORITY_LEVELS,
    GoogleMapsFilters,
    LeadFilterSet,
    LeadsFinderFilters,
)

log = logging.getLogger(__name__)

VALID_ACTORS = ("google_maps", "leads_finder")

_FILTER_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "You are a lead-generation strategist. Given a PRODUCT/SERVICES description, "
            "produce filters for two Apify actors and recommend which one(s) to use.\n\n"
            "1) google_maps — scrapes LOCAL BUSINESSES from Google Maps. Fill "
            "searchStringsArray with the business types this product would sell to or "
            "partner with, and locationQuery if the description implies a place. Use this "
            "when the targets are local/physical businesses.\n"
            "2) leads_finder — finds B2B DECISION-MAKERS (people with job titles). Fill "
            "contact_job_title, seniority_level, functional_level, company_industry, "
            "company_keywords and size to match the ideal buyer. Use this when the targets "
            "are roles at companies.\n\n"
            "Only use these exact vocabularies:\n"
            "- seniority_level: {seniorities}\n"
            "- functional_level: {functionals}\n"
            "- email_status: {email_statuses}\n"
            "- funding: {fundings}\n\n"
            "Set recommended_actors to the subset of ['google_maps','leads_finder'] that "
            "genuinely fits, and still fill filters for both when plausible. Keep filters "
            "tight and specific. Do NOT set both contact_location and contact_city.",
        ),
        ("human", "PRODUCT/SERVICES:\n{product}\n\nLOCATION HINT (optional): {location}"),
    ]
)


def filter_chain():
    return _FILTER_PROMPT | get_chat(0.3).with_structured_output(LeadFilterSet)


def _keep_enum(values: list[str], allowed: tuple[str, ...]) -> list[str]:
    """Keep only values matching the allowed vocabulary (case-insensitive)."""
    lut = {a.lower(): a for a in allowed}
    out: list[str] = []
    seen: set[str] = set()
    for v in values:
        canon = lut.get(str(v).strip().lower())
        if canon and canon not in seen:
            seen.add(canon)
            out.append(canon)
    return out


def _apply_google_maps_rules(gm: GoogleMapsFilters, config: dict) -> GoogleMapsFilters:
    gm.maxCrawledPlacesPerSearch = min(
        gm.maxCrawledPlacesPerSearch or int(config.get("leadgen_gmaps_max_places", 50)),
        int(config.get("leadgen_gmaps_max_places", 50)),
    )
    if not gm.language:
        gm.language = str(config.get("leadgen_gmaps_language", "en"))
    # scrapeContacts default comes from config (on by default) when the LLM omitted it.
    gm.scrapeContacts = bool(gm.scrapeContacts) if gm.scrapeContacts is not None else bool(
        config.get("leadgen_gmaps_scrape_contacts", True)
    )
    return gm


def _apply_leads_finder_rules(lf: LeadsFinderFilters, config: dict) -> LeadsFinderFilters:
    cap = int(config.get("leadgen_leads_fetch_count", 100))
    lf.fetch_count = min(lf.fetch_count or cap, cap)
    lf.seniority_level = _keep_enum(lf.seniority_level, SENIORITY_LEVELS)
    lf.functional_level = _keep_enum(lf.functional_level, FUNCTIONAL_LEVELS)
    lf.email_status = _keep_enum(lf.email_status, EMAIL_STATUSES)
    lf.funding = _keep_enum(lf.funding, FUNDING_ROUNDS)
    # Actor README: contact_location and contact_city are mutually exclusive.
    if lf.contact_location and lf.contact_city:
        log.info("generate_filters: both contact_location and contact_city set — dropping contact_city")
        lf.contact_city = []
    return lf


def _postprocess(fs: LeadFilterSet, actors: list[str], config: dict) -> LeadFilterSet:
    """Enforce caps/rules and restrict output to the requested actors."""
    if "google_maps" in actors:
        fs.google_maps = _apply_google_maps_rules(fs.google_maps or GoogleMapsFilters(), config)
    else:
        fs.google_maps = None
    if "leads_finder" in actors:
        fs.leads_finder = _apply_leads_finder_rules(fs.leads_finder or LeadsFinderFilters(), config)
    else:
        fs.leads_finder = None

    rec = [a for a in fs.recommended_actors if a in actors]
    fs.recommended_actors = rec or list(actors)
    return fs


def _fallback(product_description: str, actors: list[str], config: dict, location: str) -> LeadFilterSet:
    """Deterministic filters when the LLM is unavailable: keyword-seed both actors."""
    limit = int(config.get("growth_keyword_limit", 8))
    keywords = _fallback_keywords(product_description, limit)
    gm = GoogleMapsFilters(searchStringsArray=keywords, locationQuery=location or "")
    lf = LeadsFinderFilters(company_keywords=keywords)
    fs = LeadFilterSet(
        product_description=product_description,
        google_maps=gm,
        leads_finder=lf,
        recommended_actors=list(actors),
        rationale="LLM disabled — deterministic keyword-seeded filters.",
    )
    return _postprocess(fs, actors, config)


def generate_filters(
    product_description: str,
    config: dict,
    actors: list[str] | None = None,
    location: str = "",
) -> LeadFilterSet:
    """Description → actor filters + recommendation (LLM, guarded fallback).

    ``actors`` restricts which actors to generate filters for (default: both).
    """
    actors = [a for a in (actors or VALID_ACTORS) if a in VALID_ACTORS] or list(VALID_ACTORS)
    description = (product_description or "").strip()
    if not description:
        return _postprocess(
            LeadFilterSet(product_description="", recommended_actors=list(actors)), actors, config
        )

    if not get_settings().has_openai():
        log.info("generate_filters: OpenAI not configured — using deterministic fallback")
        return _fallback(description, actors, config, location)

    try:
        result: LeadFilterSet = filter_chain().invoke({
            "product": description,
            "location": location or "(none)",
            "seniorities": ", ".join(SENIORITY_LEVELS),
            "functionals": ", ".join(FUNCTIONAL_LEVELS),
            "email_statuses": ", ".join(EMAIL_STATUSES),
            "fundings": ", ".join(FUNDING_ROUNDS),
        })
        result.product_description = description
        return _postprocess(result, actors, config)
    except Exception as e:  # noqa: BLE001
        log.warning("generate_filters LLM failed: %s — using fallback", e)
        return _fallback(description, actors, config, location)
