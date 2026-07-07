from reputation_analyzer.normalize import normalize


def test_instagram_mapping():
    raw = [
        {
            "caption": "hello world",
            "url": "https://instagram.com/p/abc",
            "timestamp": "2026-01-02T00:00:00Z",
            "likesCount": 120,
            "commentsCount": 8,
        }
    ]
    posts = normalize("instagram", raw)
    assert len(posts) == 1
    p = posts[0]
    assert p.text == "hello world"
    assert p.post_url == "https://instagram.com/p/abc"
    assert p.date == "2026-01-02T00:00:00Z"
    assert p.likes == 120
    assert p.comments == 8


def test_missing_fields_default():
    posts = normalize("instagram", [{}])
    assert posts[0].text == ""
    assert posts[0].likes == 0
    assert posts[0].comments == 0


def test_x_mapping():
    raw = [{"text": "a tweet", "twitterUrl": "https://x.com/i/1", "likeCount": 5, "replyCount": 2}]
    p = normalize("x", raw)[0]
    assert p.text == "a tweet"
    assert p.post_url == "https://x.com/i/1"
    assert p.likes == 5
    assert p.comments == 2


def test_tiktok_mapping():
    raw = [{"text": "vid", "webVideoUrl": "https://tiktok.com/@a/1", "diggCount": 9, "commentCount": 3}]
    p = normalize("tiktok", raw)[0]
    assert p.post_url == "https://tiktok.com/@a/1"
    assert p.likes == 9
    assert p.comments == 3


def test_unknown_platform_empty():
    assert normalize("facebook", [{"text": "x"}]) == []


def test_non_dict_items_skipped():
    assert normalize("instagram", ["nope", None]) == []
