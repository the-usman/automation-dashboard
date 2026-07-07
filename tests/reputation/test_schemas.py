import pytest
from pydantic import ValidationError

from reputation_analyzer.schemas import ReputationRequest


def test_rejects_all_null():
    with pytest.raises(ValidationError):
        ReputationRequest()


def test_rejects_all_blank():
    with pytest.raises(ValidationError):
        ReputationRequest(instagram_username="  ", x_username="", tiktok_username=None)


def test_single_username_ok():
    req = ReputationRequest(instagram_username="brand")
    assert req.provided() == {"instagram": "brand"}


def test_strips_at_and_whitespace():
    req = ReputationRequest(x_username="  @handle ")
    assert req.provided() == {"x": "handle"}


def test_multiple_platforms():
    req = ReputationRequest(instagram_username="a", tiktok_username="b")
    assert req.provided() == {"instagram": "a", "tiktok": "b"}
