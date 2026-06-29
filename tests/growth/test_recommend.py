"""LLM recommendation: offline fallback behaviour (no OpenAI key)."""
from __future__ import annotations

from social_trend_analyzer.growth import recommend as rec_mod
from social_trend_analyzer.growth.schemas import CandidateUser


class FakeSettings:
    def has_openai(self):
        return False


def test_empty_candidates_returns_empty():
    assert rec_mod.recommend("product", [], 60) == []


def test_fallback_scores_neutral_and_skips_below_threshold(monkeypatch):
    monkeypatch.setattr(rec_mod, "get_settings", lambda: FakeSettings())
    cands = [CandidateUser(username="a"), CandidateUser(username="b")]

    recs = rec_mod.recommend("product", cands, min_score=60)

    assert len(recs) == 2
    assert all(r.score == 50 for r in recs)
    assert all(r.action == "skip" for r in recs)


def test_fallback_follows_when_threshold_low(monkeypatch):
    monkeypatch.setattr(rec_mod, "get_settings", lambda: FakeSettings())
    recs = rec_mod.recommend("product", [CandidateUser(username="a")], min_score=40)
    assert recs[0].action == "follow"
