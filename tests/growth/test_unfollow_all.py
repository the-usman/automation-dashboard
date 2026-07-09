"""Validation tests for the unfollow-all API request model.

POST /growth/unfollow-all unfollows everyone the account follows except the
`keep` usernames; these tests cover keep-list normalization and auth rules.
"""
import pytest
from pydantic import ValidationError

from app.src.models.growth_models import UnfollowAllRequest


def test_keep_is_normalized_and_deduped():
    req = UnfollowAllRequest(
        sessionid="s3ss10n", keep=["@BestFriend", "  bestfriend ", "Mom", "", "@"]
    )
    assert req.keep == ["bestfriend", "mom"]


def test_requires_sessionid_or_credential_id():
    with pytest.raises(ValidationError, match="sessionid"):
        UnfollowAllRequest(keep=["friend"])


def test_empty_keep_list_allowed():
    req = UnfollowAllRequest(credential_id="cred01")
    assert req.keep == []
    assert req.max_unfollows == 0


def test_max_unfollows_bounds():
    assert UnfollowAllRequest(sessionid="s", max_unfollows=500).max_unfollows == 500
    with pytest.raises(ValidationError):
        UnfollowAllRequest(sessionid="s", max_unfollows=501)
