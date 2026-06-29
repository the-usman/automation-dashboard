"""CLI argument → trigger mapping, and session-path sanitization."""
from __future__ import annotations

from social_trend_analyzer.growth import cli, instagram
from social_trend_analyzer.growth.schemas import InstagramCredential


def _trigger(argv):
    args = cli._build_parser().parse_args(argv)
    return cli._trigger_from_args(args)


def test_recommend_product_maps_to_discover_product():
    t = _trigger(["recommend", "--product", "AI app", "--keywords", "fitness,workout"])
    assert t["mode"] == "discover_product"
    assert t["product_description"] == "AI app"
    assert t["keywords"] == "fitness,workout"
    assert t["competitor_url"] == ""


def test_recommend_competitor_maps_to_discover_competitor():
    t = _trigger(["recommend", "--competitor", "https://instagram.com/c/", "--product", "p"])
    assert t["mode"] == "discover_competitor"
    assert t["competitor_url"] == "https://instagram.com/c/"


def test_act_follow_maps_to_execute_actions():
    t = _trigger(["act", "--follow", "u1,u2", "--credential-id", "cred01"])
    assert t["mode"] == "execute_actions"
    assert t["action"] == "follow"
    assert t["usernames"] == "u1,u2"
    assert t["credential_id"] == "cred01"


def test_act_unfollow_maps_to_execute_actions():
    t = _trigger(["act", "--unfollow", "u1", "--credential-id", "cred01"])
    assert t["mode"] == "execute_actions"
    assert t["action"] == "unfollow"
    assert t["usernames"] == "u1"


def test_session_path_sanitizes_malicious_username(tmp_path):
    cred = InstagramCredential(credential_id="c", username="../../evil", password="p")
    client = instagram.InstagramClient(cred, {}, str(tmp_path))
    path = client._session_path()
    # path separators are stripped, so the file is pinned inside session_dir
    # (remaining dots are harmless without a separator) and cannot traverse out.
    assert path.parent == tmp_path
    assert "/" not in path.name and "\\" not in path.name
    assert path.resolve().parent == tmp_path.resolve()
