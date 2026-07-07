from pathlib import Path

from reputation_analyzer import service
from reputation_analyzer.schemas import (
    NormalizedPost,
    PlatformScores,
    ReputationRequest,
    ReputationResponse,
)


def test_instagram_only_scores_only_instagram(monkeypatch):
    monkeypatch.setattr(
        service.apify_client, "scrape_profile",
        lambda platform, username: [{"caption": "hi", "likesCount": 3}],
    )

    def fake_score(platform_posts):
        # LLM might return scores for all platforms; service must null the absent ones.
        return ReputationResponse(
            overall_score=77,
            platform_scores=PlatformScores(instagram=77, x=50, tiktok=50),
            strong_points=["clear voice"],
            weak_points=["low posting cadence"],
        )

    monkeypatch.setattr(service.scorer, "score", fake_score)

    resp = service.check_reputation(ReputationRequest(instagram_username="brand"))
    assert resp.overall_score == 77
    assert resp.platform_scores.instagram == 77
    assert resp.platform_scores.x is None
    assert resp.platform_scores.tiktok is None
    assert resp.strong_points and resp.weak_points


def test_platform_failure_is_isolated(monkeypatch):
    def scrape(platform, username):
        if platform == "instagram":
            raise RuntimeError("username not found")
        return [{"text": "vid", "diggCount": 1}]

    monkeypatch.setattr(service.apify_client, "scrape_profile", scrape)
    monkeypatch.setattr(
        service.scorer, "score",
        lambda pp: ReputationResponse(
            overall_score=60,
            platform_scores=PlatformScores(tiktok=60),
            strong_points=["a"],
            weak_points=["b"],
        ),
    )

    resp = service.check_reputation(
        ReputationRequest(instagram_username="bad", tiktok_username="good")
    )
    # instagram failed -> null; tiktok scraped+scored -> present
    assert resp.platform_scores.instagram is None
    assert resp.platform_scores.tiktok == 60


def test_no_posts_returns_zero_without_calling_llm(monkeypatch):
    monkeypatch.setattr(service.apify_client, "scrape_profile", lambda p, u: [])

    def boom(pp):
        raise AssertionError("scorer must not be called when nothing was scraped")

    monkeypatch.setattr(service.scorer, "score", boom)

    resp = service.check_reputation(ReputationRequest(instagram_username="ghost"))
    assert resp.overall_score == 0
    assert resp.platform_scores.instagram is None
    assert resp.weak_points  # explains no data


def test_no_coupling_to_trend_analyzer():
    """The reputation feature must not import social_trend_analyzer."""
    import ast

    pkg = Path(service.__file__).parent
    for py in pkg.glob("*.py"):
        tree = ast.parse(py.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert all(not a.name.startswith("social_trend_analyzer") for a in node.names), py.name
            elif isinstance(node, ast.ImportFrom):
                assert not (node.module or "").startswith("social_trend_analyzer"), py.name
