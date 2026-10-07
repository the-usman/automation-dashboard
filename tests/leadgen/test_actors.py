"""Actor runners + output normalizers (Apify mocked)."""
from __future__ import annotations

from leadgen import actors


def test_run_google_maps_uses_config_actor_and_input(monkeypatch):
    captured = {}

    def fake_run(actor_id, actor_input, **kw):
        captured["actor_id"] = actor_id
        captured["input"] = actor_input
        return [{"title": "Cafe"}]

    monkeypatch.setattr(actors, "run_actor_sync", fake_run)

    out = actors.run_google_maps(
        {"searchStringsArray": ["coffee"], "locationQuery": "", "maxCrawledPlacesPerSearch": 5},
        {"leadgen_google_maps_actor": "org~gmaps"},
    )

    assert out == [{"title": "Cafe"}]
    assert captured["actor_id"] == "org~gmaps"
    # Empty values are dropped from the actor input.
    assert "locationQuery" not in captured["input"]
    assert captured["input"]["searchStringsArray"] == ["coffee"]


def test_run_google_maps_requires_search_terms(monkeypatch):
    monkeypatch.setattr(
        actors, "run_actor_sync",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not scrape")),
    )
    assert actors.run_google_maps({"locationQuery": "Austin"}, {}) == []


def test_run_leads_finder_uses_config_actor(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        actors, "run_actor_sync",
        lambda actor_id, inp, **kw: captured.update(actor_id=actor_id, input=inp) or [{"email": "a@b.co"}],
    )

    out = actors.run_leads_finder(
        {"fetch_count": 100, "seniority_level": ["VP"]},
        {"leadgen_leads_finder_actor": "org~leads"},
    )

    assert out == [{"email": "a@b.co"}]
    assert captured["actor_id"] == "org~leads"


def test_normalize_place_picks_fields():
    raw = {
        "title": "Blue Bottle", "categoryName": "Coffee shop", "address": "1 Main St",
        "city": "Austin", "state": "TX", "countryCode": "US", "phone": "+1 555",
        "website": "https://bb.com", "emails": ["hi@bb.com"], "totalScore": 4.6,
        "reviewsCount": 321, "placeId": "abc", "url": "https://maps.google/abc",
    }
    lead = actors.normalize_place(raw)

    assert lead["name"] == "Blue Bottle"
    assert lead["category"] == "Coffee shop"
    assert lead["emails"] == ["hi@bb.com"]
    assert lead["rating"] == 4.6
    assert lead["reviews_count"] == 321
    assert lead["place_id"] == "abc"


def test_normalize_lead_picks_fields():
    raw = {
        "first_name": "Ada", "last_name": "Lovelace", "job_title": "CMO",
        "email": "ada@acme.com", "linkedin": "https://li/ada", "company_name": "Acme",
        "company_domain": "acme.com", "industry": "Software", "company_size": "11-20",
    }
    lead = actors.normalize_lead(raw)

    assert lead["full_name"] == ""   # not provided → empty, not fabricated
    assert lead["job_title"] == "CMO"
    assert lead["email"] == "ada@acme.com"
    assert lead["company_domain"] == "acme.com"
