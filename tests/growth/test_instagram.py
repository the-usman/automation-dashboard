"""instagrapi wrapper: login, follow/unfollow, rate cap, error handling, 2FA."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from social_trend_analyzer.growth import instagram
from social_trend_analyzer.growth.schemas import InstagramCredential


class FakeIGClient:
    def __init__(self):
        self.followed = []
        self.unfollowed = []
        self.logged = None
        self.is_business = False

    def set_settings(self, blob):
        self.blob = blob

    def load_settings(self, path):
        self.loaded = path

    def dump_settings(self, path):
        Path(path).write_text("{}", encoding="utf-8")

    def login(self, username, password, verification_code=""):
        self.logged = (username, password, verification_code)
        return True

    def user_id_from_username(self, username):
        if username == "boom":
            raise RuntimeError("user not found")
        return 111

    def user_follow(self, uid):
        self.followed.append(uid)
        return True

    def user_unfollow(self, uid):
        self.unfollowed.append(uid)
        return True

    def totp_generate_code(self, seed):
        return "123456"

    def user_info(self, uid):
        return SimpleNamespace(is_business=self.is_business)

    def account_info(self):
        return SimpleNamespace(pk=1, username="u")

    def user_following(self, uid, amount=0):
        self.following_requested = (uid, amount)
        return {
            "1": SimpleNamespace(username="alice"),
            "2": SimpleNamespace(username="bob"),
            "3": SimpleNamespace(username=""),
        }


def _config(cap=150):
    return {
        "ig_action_delay_seconds": 0,
        "ig_daily_action_cap": cap,
        "ig_read_delay_seconds": 0,
        "ig_delay_jitter_seconds": 0,
    }


def _login(monkeypatch, tmp_path, cred=None, cap=150):
    fake = FakeIGClient()
    monkeypatch.setattr(instagram, "Client", lambda: fake)
    cred = cred or InstagramCredential(credential_id="c", username="u", password="p")
    client = instagram.InstagramClient(cred, _config(cap), str(tmp_path)).login()
    return client, fake


def test_follow_success(monkeypatch, tmp_path):
    client, fake = _login(monkeypatch, tmp_path)
    result = client.follow("target")
    assert result.status == "success"
    assert result.user_id == "111"
    assert 111 in fake.followed


def test_unfollow_success(monkeypatch, tmp_path):
    client, fake = _login(monkeypatch, tmp_path)
    result = client.unfollow("target")
    assert result.status == "success"
    assert 111 in fake.unfollowed


def test_daily_cap_skips(monkeypatch, tmp_path):
    client, fake = _login(monkeypatch, tmp_path, cap=0)
    result = client.follow("target")
    assert result.status == "skipped"
    assert fake.followed == []


def test_action_error_is_captured(monkeypatch, tmp_path):
    client, _ = _login(monkeypatch, tmp_path)
    result = client.follow("boom")
    assert result.status == "error"
    assert "user not found" in result.message


def test_session_persisted_on_login(monkeypatch, tmp_path):
    _login(monkeypatch, tmp_path)
    assert (tmp_path / "u.json").exists()


def test_totp_passed_to_login(monkeypatch, tmp_path):
    cred = InstagramCredential(credential_id="c", username="u", password="p", totp_seed="SEED")
    _, fake = _login(monkeypatch, tmp_path, cred=cred)
    assert fake.logged[2] == "123456"


def test_is_business_account_true_for_brand(monkeypatch, tmp_path):
    client, fake = _login(monkeypatch, tmp_path)
    fake.is_business = True
    assert client.is_business_account("brandpage") is True


def test_is_business_account_false_for_person(monkeypatch, tmp_path):
    client, fake = _login(monkeypatch, tmp_path)
    fake.is_business = False
    assert client.is_business_account("realperson") is False


def test_is_business_account_none_on_error(monkeypatch, tmp_path):
    client, _ = _login(monkeypatch, tmp_path)
    assert client.is_business_account("boom") is None


def test_following_usernames(monkeypatch, tmp_path):
    client, fake = _login(monkeypatch, tmp_path)
    names = client.following_usernames()
    assert sorted(names) == ["alice", "bob"]  # blank usernames dropped
    assert fake.following_requested == ("1", 1000)  # default fetch cap applied


def test_login_without_instagrapi_raises(monkeypatch, tmp_path):
    monkeypatch.setattr(instagram, "Client", None)
    cred = InstagramCredential(credential_id="c", username="u", password="p")
    client = instagram.InstagramClient(cred, _config(), str(tmp_path))
    try:
        client.login()
        assert False, "expected RuntimeError"
    except RuntimeError as e:
        assert "instagrapi" in str(e)
