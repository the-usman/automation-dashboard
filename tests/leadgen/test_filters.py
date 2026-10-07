"""Description → actor filters: fallback seeding + hard-rule enforcement.

Guarded/offline: OpenAI is disabled via a fake settings object, so these run
without network or credentials (pattern from tests/growth/test_competitors.py).
"""
from __future__ import annotations

from leadgen import filters


class _NoOpenAI:
    def has_openai(self) -> bool:
        return False


def _disable_openai(monkeypatch):
    monkeypatch.setattr(filters, "get_settings", lambda: _NoOpenAI())


# ── fallback ──────────────────────────────────────────────────────────────────
def test_fallback_seeds_both_actors_from_keywords(monkeypatch):
    _disable_openai(monkeypatch)

    fs = filters.generate_filters("The best organic vegan skincare serum", {})

    # Stopwords/short tokens dropped, deduped, lowercased → seed both actors.
    assert fs.google_maps.searchStringsArray == ["organic", "vegan", "skincare", "serum"]
    assert fs.leads_finder.company_keywords == ["organic", "vegan", "skincare", "serum"]
    assert set(fs.recommended_actors) == {"google_maps", "leads_finder"}


def test_location_hint_flows_into_google_maps(monkeypatch):
    _disable_openai(monkeypatch)

    fs = filters.generate_filters("coffee roaster", {}, location="Austin, Texas, USA")

    assert fs.google_maps.locationQuery == "Austin, Texas, USA"


def test_actors_arg_restricts_output(monkeypatch):
    _disable_openai(monkeypatch)

    fs = filters.generate_filters("vegan skincare", {}, actors=["google_maps"])

    assert fs.google_maps is not None
    assert fs.leads_finder is None
    assert fs.recommended_actors == ["google_maps"]


def test_empty_description_returns_empty_filters(monkeypatch):
    _disable_openai(monkeypatch)

    fs = filters.generate_filters("   ", {})

    assert fs.google_maps.searchStringsArray == []
    assert fs.leads_finder.company_keywords == []


# ── post-processing rules (independent of the LLM) ─────────────────────────────
def test_caps_applied(monkeypatch):
    _disable_openai(monkeypatch)
    config = {"leadgen_gmaps_max_places": 10, "leadgen_leads_fetch_count": 25}

    fs = filters.generate_filters("vegan skincare", config)

    assert fs.google_maps.maxCrawledPlacesPerSearch == 10
    assert fs.leads_finder.fetch_count == 25


def test_contact_city_dropped_when_location_also_set():
    from leadgen.schemas import LeadsFinderFilters

    lf = LeadsFinderFilters(contact_location=["California"], contact_city=["San Diego"])
    out = filters._apply_leads_finder_rules(lf, {})

    assert out.contact_location == ["California"]
    assert out.contact_city == []


def test_enum_values_filtered_to_vocabulary():
    from leadgen.schemas import LeadsFinderFilters

    lf = LeadsFinderFilters(
        seniority_level=["c-level", "Bogus", "VP"],
        email_status=["validated", "nope"],
    )
    out = filters._apply_leads_finder_rules(lf, {})

    assert out.seniority_level == ["C-Level", "VP"]   # canonicalized, bogus dropped
    assert out.email_status == ["validated"]


# ── Google Maps actor rules ────────────────────────────────────────────────────
def test_gmaps_select_enums_fall_back_on_bad_values():
    from leadgen.schemas import GoogleMapsFilters

    gm = GoogleMapsFilters(searchMatching="bogus", placeMinimumStars="99", website="nope")
    out = filters._apply_google_maps_rules(gm, {})

    assert out.searchMatching == "all"        # invalid → default
    assert out.placeMinimumStars == ""        # invalid → default (any)
    assert out.website == "allPlaces"         # invalid → default


def test_gmaps_select_enums_canonicalized():
    from leadgen.schemas import GoogleMapsFilters

    gm = GoogleMapsFilters(website="withwebsite", searchMatching="ONLY_EXACT")
    out = filters._apply_google_maps_rules(gm, {})

    assert out.website == "withWebsite"
    assert out.searchMatching == "only_exact"


def test_gmaps_leads_enrichment_capped_and_departments_filtered():
    from leadgen.schemas import GoogleMapsFilters

    gm = GoogleMapsFilters(
        maximumLeadsEnrichmentRecords=999,
        leadsEnrichmentDepartments=["sales", "Bogus", "marketing"],
        verifyLeadsEnrichmentEmails=True,
    )
    out = filters._apply_google_maps_rules(gm, {"leadgen_gmaps_max_leads_per_place": 5})

    assert out.maximumLeadsEnrichmentRecords == 5                 # capped
    assert out.leadsEnrichmentDepartments == ["sales", "marketing"]  # bogus dropped


def test_gmaps_departments_cleared_when_enrichment_off():
    from leadgen.schemas import GoogleMapsFilters

    gm = GoogleMapsFilters(
        maximumLeadsEnrichmentRecords=0,
        leadsEnrichmentDepartments=["sales"],
        verifyLeadsEnrichmentEmails=True,
    )
    out = filters._apply_google_maps_rules(gm, {})

    assert out.leadsEnrichmentDepartments == []       # enrichment off → no departments
    assert out.verifyLeadsEnrichmentEmails is False
