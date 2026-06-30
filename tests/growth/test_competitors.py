"""Description → competitors: keyword extraction + competitor ranking.

Guarded/offline: OpenAI is disabled via a fake settings object and Apify is
monkeypatched, so these run without network or credentials.
"""
from __future__ import annotations

from social_trend_analyzer.growth import competitors


class _NoOpenAI:
    def has_openai(self) -> bool:
        return False


def _disable_openai(monkeypatch):
    monkeypatch.setattr(competitors, "get_settings", lambda: _NoOpenAI())


# ── extract_keywords ──────────────────────────────────────────────────────────
def test_extract_keywords_fallback_strips_stopwords(monkeypatch):
    # Arrange
    _disable_openai(monkeypatch)

    # Act
    out = competitors.extract_keywords("The best organic vegan skincare serum", {})

    # Assert — stopwords ("the", "best") and short tokens dropped, deduped, lowercased
    assert out == ["organic", "vegan", "skincare", "serum"]


def test_extract_keywords_respects_limit(monkeypatch):
    _disable_openai(monkeypatch)

    out = competitors.extract_keywords(
        "alpha bravo charlie delta echo foxtrot golf hotel india", {"growth_keyword_limit": 3}
    )

    assert out == ["alpha", "bravo", "charlie"]


def test_extract_keywords_empty_description_returns_empty(monkeypatch):
    _disable_openai(monkeypatch)
    assert competitors.extract_keywords("   ", {}) == []


# ── discover_competitors ──────────────────────────────────────────────────────
def test_discover_competitors_ranks_by_frequency(monkeypatch):
    # Arrange — "a" authored 2 posts, "b" 1, one item has no owner (skipped)
    posts = [
        {"ownerUsername": "a", "ownerFullName": "Alpha", "likesCount": 10, "commentsCount": 2},
        {"ownerUsername": "a", "likesCount": 5},
        {"owner": {"username": "b"}},
        {"caption": "no owner here"},
    ]
    _disable_openai(monkeypatch)
    monkeypatch.setattr(competitors, "run_actor_sync", lambda actor, inp, **k: posts)

    # Act
    result = competitors.discover_competitors("organic vegan skincare", {})

    # Assert — top competitor is the most frequent author, scored 100
    handles = [c.username for c in result.competitors]
    assert handles == ["a", "b"]
    assert result.competitors[0].full_name == "Alpha"
    assert result.competitors[0].score == 100
    assert "2 post" in result.competitors[0].reason


def test_discover_competitors_respects_limit(monkeypatch):
    posts = [{"ownerUsername": f"u{i}"} for i in range(6)]
    _disable_openai(monkeypatch)
    monkeypatch.setattr(competitors, "run_actor_sync", lambda actor, inp, **k: posts)

    result = competitors.discover_competitors("vegan skincare", {"growth_competitor_limit": 2})

    assert len(result.competitors) == 2


def test_discover_competitors_no_keywords_returns_empty(monkeypatch):
    _disable_openai(monkeypatch)
    # No scraping should happen when there are no keywords.
    monkeypatch.setattr(
        competitors, "run_actor_sync",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not scrape")),
    )

    result = competitors.discover_competitors("   ", {})

    assert result.competitors == []
