from datetime import datetime, timedelta, timezone

from reputation_analyzer import config
from reputation_analyzer.apify_client import _actor_input, _window_start


def test_window_start_is_lookback_days_ago():
    expected = (
        datetime.now(timezone.utc) - timedelta(days=config.LOOKBACK_DAYS)
    ).strftime("%Y-%m-%d")
    assert _window_start() == expected


def test_instagram_input_has_window():
    inp = _actor_input("instagram", "nike", 100)
    assert inp["onlyPostsNewerThan"] == _window_start()
    assert inp["resultsLimit"] == 100


def test_x_input_has_window():
    inp = _actor_input("x", "nike", 100)
    assert inp["start"] == _window_start()
    assert inp["maxItems"] == 100


def test_tiktok_input_has_window():
    inp = _actor_input("tiktok", "nike", 100)
    assert inp["oldestPostDateUnified"] == _window_start()
    assert inp["resultsPerPage"] == 100
