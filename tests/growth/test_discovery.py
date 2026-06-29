"""Apify-backed discovery: author extraction + commenter extraction + dedupe."""
from __future__ import annotations

from social_trend_analyzer.growth import discovery


def test_search_users_dedupes_and_tags_source(monkeypatch):
    items = [
        {"ownerUsername": "a", "caption": "hi"},
        {"ownerUsername": "a"},                 # duplicate
        {"owner": {"username": "b"}},           # nested shape
        {"caption": "no owner"},                # skipped
    ]
    monkeypatch.setattr(discovery, "run_actor_sync", lambda actor, inp, **k: items)

    out = discovery.search_users(["#fitness", "workout"], {})

    assert [c.username for c in out] == ["a", "b"]
    assert out[0].source == "search"
    assert out[0].context == "hi"
    assert "fitness" in out[0].source_ref


def test_search_users_no_keywords_returns_empty():
    assert discovery.search_users([], {}) == []


def test_competitor_commenters_extracts_unique(monkeypatch):
    posts = [
        {"latestComments": [{"ownerUsername": "x", "text": "nice"}, {"username": "y"}]},
        {"latestComments": [{"ownerUsername": "x"}]},  # duplicate across posts
    ]
    monkeypatch.setattr(discovery, "run_actor_sync", lambda actor, inp, **k: posts)

    out = discovery.competitor_commenters("competitor", 5, {})

    assert [c.username for c in out] == ["x", "y"]
    assert out[0].source == "competitor"
    assert out[0].context == "nice"
    assert out[0].source_ref == "https://www.instagram.com/competitor/"


def test_competitor_commenters_no_url_returns_empty():
    assert discovery.competitor_commenters("", 5, {}) == []


def test_competitor_commenters_respects_n_posts(monkeypatch):
    posts = [{"latestComments": [{"ownerUsername": f"u{i}"}]} for i in range(10)]
    monkeypatch.setattr(discovery, "run_actor_sync", lambda actor, inp, **k: posts)

    out = discovery.competitor_commenters("https://instagram.com/c/", 3, {})

    assert [c.username for c in out] == ["u0", "u1", "u2"]
