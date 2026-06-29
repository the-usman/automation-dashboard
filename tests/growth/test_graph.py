"""End-to-end graph routing across the three modes (all externals stubbed)."""
from __future__ import annotations

from social_trend_analyzer.growth import graph as growth_graph
from social_trend_analyzer.growth.schemas import (
    ActionResult,
    CandidateUser,
    InstagramCredential,
    UserRecommendation,
)


def test_discover_competitor_flow(monkeypatch):
    monkeypatch.setattr(growth_graph.discovery, "competitor_commenters",
                        lambda url, n, cfg: [CandidateUser(username="x", source="competitor")])
    monkeypatch.setattr(growth_graph, "recommend",
                        lambda prod, cands, ms: [UserRecommendation(username="x", score=80, action="follow")])

    out = growth_graph.run({
        "mode": "discover_competitor",
        "competitor_url": "https://www.instagram.com/c/",
        "product_description": "p",
    })

    assert out["mode"] == "discover_competitor"
    assert [r.username for r in out["recommendations"]] == ["x"]


def test_discover_product_flow(monkeypatch):
    monkeypatch.setattr(growth_graph.discovery, "search_users",
                        lambda kw, cfg: [CandidateUser(username="a", source="search")])
    monkeypatch.setattr(growth_graph, "recommend",
                        lambda prod, cands, ms: [UserRecommendation(username="a", score=70, action="follow")])

    out = growth_graph.run({"mode": "discover_product", "keywords": "fitness,workout"})

    assert [r.username for r in out["recommendations"]] == ["a"]


def test_execute_actions_flow(monkeypatch):
    cred = InstagramCredential(credential_id="c", username="u", password="p")
    monkeypatch.setattr(growth_graph, "load_instagram_credential", lambda cid: cred)

    class FakeIG:
        def __init__(self, *a, **k):
            pass

        def login(self):
            return self

        def follow(self, username):
            return ActionResult(username=username, action="follow", status="success", user_id="1")

        def unfollow(self, username):
            return ActionResult(username=username, action="unfollow", status="success")

    monkeypatch.setattr(growth_graph, "InstagramClient", FakeIG)

    out = growth_graph.run({
        "mode": "execute_actions",
        "action": "follow",
        "usernames": "a,b",
        "credential_id": "c",
    })

    assert len(out["action_results"]) == 2
    assert all(r.status == "success" for r in out["action_results"])


def test_execute_actions_missing_credential(monkeypatch):
    monkeypatch.setattr(growth_graph, "load_instagram_credential", lambda cid: None)

    out = growth_graph.run({
        "mode": "execute_actions",
        "action": "follow",
        "usernames": "a",
        "credential_id": "missing",
    })

    assert out.get("errors")
    assert out["errors"][0]["node_name"] == "execute_actions"


def test_invalid_mode_defaults_to_discover_product(monkeypatch):
    monkeypatch.setattr(growth_graph.discovery, "search_users", lambda kw, cfg: [])
    monkeypatch.setattr(growth_graph, "recommend", lambda prod, cands, ms: [])

    out = growth_graph.run({"mode": "nonsense"})

    assert out["mode"] == "discover_product"
