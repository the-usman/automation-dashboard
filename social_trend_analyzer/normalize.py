"""Normalize raw posts into the unified :class:`Post` schema.

Faithful port of the n8n "Normalize Posts" buildPost() switch, including the
container-expansion for YouTube (items[]) / Reddit (data.children[]) /
Google Trends (rss item[]).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from .schemas import Metrics, Post


def _pick(*vals) -> Any:
    for v in vals:
        if v not in (None, "", []):
            return v
    return ""


def _num(v) -> int:
    try:
        n = float(v)
        return int(n) if n == n else 0  # NaN guard
    except (TypeError, ValueError):
        return 0


def _arr(v) -> list:
    if isinstance(v, list):
        return [x for x in v if x]
    return [v] if v else []


def _build_post(platform: str, raw: dict, source_actor: str) -> Post:
    likes = comments = shares = views = 0
    author_handle = author_id = post_id = post_url = text = posted_at = ""
    media_urls: list = []
    hashtags: list = []
    r = raw or {}

    if platform == "instagram":
        likes = _num(_pick(r.get("likesCount"), r.get("likes")))
        comments = _num(_pick(r.get("commentsCount"), r.get("comments")))
        views = _num(_pick(r.get("videoViewCount"), r.get("videoPlayCount"), r.get("views")))
        author_handle = _pick(r.get("ownerUsername"), r.get("username"))
        author_id = _pick(r.get("ownerId"))
        post_id = _pick(r.get("id"), r.get("shortCode"), r.get("shortcode"))
        post_url = _pick(r.get("url"), f"https://www.instagram.com/p/{r['shortCode']}/" if r.get("shortCode") else "")
        text = _pick(r.get("caption"), r.get("text"))
        posted_at = _pick(r.get("timestamp"), r.get("taken_at"))
        media_urls = _arr(r.get("displayUrl") or r.get("videoUrl"))
        hashtags = _arr(r.get("hashtags"))

    elif platform == "tiktok":
        st = r.get("stats") or {}
        am = r.get("authorMeta") or {}
        author = r.get("author") or {}
        likes = _num(_pick(r.get("diggCount"), st.get("diggCount")))
        comments = _num(_pick(r.get("commentCount"), st.get("commentCount")))
        shares = _num(_pick(r.get("shareCount"), st.get("shareCount")))
        views = _num(_pick(r.get("playCount"), st.get("playCount")))
        author_handle = _pick(am.get("name"), am.get("nickName"), author.get("uniqueId"))
        author_id = _pick(am.get("id"), author.get("id"))
        post_id = _pick(r.get("id"), r.get("awemeId"))
        post_url = _pick(r.get("webVideoUrl"), r.get("url"))
        text = _pick(r.get("text"), r.get("desc"))
        posted_at = _pick(r.get("createTimeISO"),
                          datetime.fromtimestamp(r["createTime"], timezone.utc).isoformat() if r.get("createTime") else "")
        vm = r.get("videoMeta") or {}
        media_urls = _arr(r.get("videoUrl") or vm.get("coverUrl"))
        hashtags = [h.get("name") if isinstance(h, dict) else h for h in (r.get("hashtags") or []) if h]

    elif platform in ("x", "twitter"):
        a = r.get("author") or r.get("user") or {}
        likes = _num(_pick(r.get("likeCount"), r.get("favoriteCount")))
        comments = _num(r.get("replyCount"))
        shares = _num(_pick(r.get("retweetCount"), r.get("quoteCount")))
        views = _num(_pick(r.get("viewCount"), r.get("views")))
        author_handle = _pick(a.get("userName"), a.get("username"), a.get("screen_name"))
        author_id = _pick(a.get("id"), a.get("id_str"))
        post_id = _pick(r.get("id"), r.get("id_str"), r.get("tweetId"))
        post_url = _pick(r.get("url"), r.get("twitterUrl"))
        text = _pick(r.get("text"), r.get("fullText"), r.get("full_text"))
        posted_at = _pick(r.get("createdAt"), r.get("created_at"))
        media_urls = [m.get("media_url_https") or m.get("url") if isinstance(m, dict) else m
                      for m in (r.get("media") or []) if m]
        ent_tags = (r.get("entities") or {}).get("hashtags") or r.get("hashtags") or []
        hashtags = [h.get("text") or h.get("tag") if isinstance(h, dict) else h for h in ent_tags if h]
        platform = "x"

    elif platform == "facebook":
        user = r.get("user") or {}
        likes = _num(_pick(r.get("likes"), r.get("reactionsCount"), r.get("likesCount")))
        comments = _num(_pick(r.get("comments"), r.get("commentsCount")))
        shares = _num(_pick(r.get("shares"), r.get("sharesCount")))
        views = _num(_pick(r.get("viewsCount"), r.get("videoViewCount")))
        author_handle = _pick(r.get("pageName"), user.get("name"), r.get("author"))
        author_id = _pick(r.get("pageId"), user.get("id"))
        post_id = _pick(r.get("postId"), r.get("id"))
        post_url = _pick(r.get("url"), r.get("postUrl"), r.get("permalink"))
        text = _pick(r.get("text"), r.get("message"), r.get("postText"))
        posted_at = _pick(r.get("time"), r.get("date"), r.get("timestamp"))
        media = r.get("media")
        media_urls = _arr(r.get("imageUrl") or ([m.get("url") if isinstance(m, dict) else m for m in media] if media else None))
        hashtags = _arr(r.get("hashtags"))

    elif platform == "linkedin":
        author_handle = _pick(r.get("username"), r.get("publicIdentifier"), r.get("profileId"))
        author_id = _pick(r.get("id"), r.get("urn"))
        post_id = _pick(r.get("id"), r.get("username"))
        post_url = _pick(r.get("url"), r.get("profileUrl"),
                         f"https://www.linkedin.com/in/{r['username']}" if r.get("username") else "")
        text = _pick(r.get("headline"), r.get("summary"), r.get("about"))
        media_urls = _arr(r.get("profilePicture"))

    elif platform == "youtube":
        sn = r.get("snippet") or {}
        stt = r.get("statistics") or {}
        likes = _num(stt.get("likeCount"))
        comments = _num(stt.get("commentCount"))
        views = _num(stt.get("viewCount"))
        author_handle = _pick(sn.get("channelTitle"))
        author_id = _pick(sn.get("channelId"))
        vid = _pick(r.get("id", {}).get("videoId") if isinstance(r.get("id"), dict) else r.get("id"))
        post_id = vid
        post_url = f"https://www.youtube.com/watch?v={vid}" if vid else ""
        text = _pick(sn.get("title"), sn.get("description"))
        posted_at = _pick(sn.get("publishedAt"))
        media_urls = _arr((sn.get("thumbnails", {}).get("high") or {}).get("url"))

    elif platform == "reddit":
        likes = _num(_pick(r.get("ups"), r.get("score"), r.get("upvotes")))
        comments = _num(_pick(r.get("num_comments"), r.get("numberOfComments")))
        author_handle = _pick(r.get("author"))
        author_id = _pick(r.get("author_fullname"))
        post_id = _pick(r.get("id"), r.get("name"))
        post_url = _pick(r.get("url"), f"https://www.reddit.com{r['permalink']}" if r.get("permalink") else "")
        text = _pick(r.get("title"), r.get("selftext"))
        posted_at = (datetime.fromtimestamp(r["created_utc"], timezone.utc).isoformat()
                     if r.get("created_utc") else _pick(r.get("createdAt")))
        media_urls = _arr(r.get("url"))

    elif platform == "google_trends":
        views = _num(_pick(r.get("ht:approx_traffic"), r.get("traffic")))
        post_id = _pick(r.get("title"), r.get("link"))
        post_url = _pick(r.get("link"), r.get("url"))
        text = _pick(r.get("title"), r.get("description"))
        posted_at = _pick(r.get("pubDate"))

    elif platform == "buzzsumo":
        shares = _num(_pick(r.get("total_shares"), r.get("totalShares")))
        author_handle = _pick(r.get("author_name"))
        post_id = _pick(r.get("id"), r.get("url"))
        post_url = _pick(r.get("url"))
        text = _pick(r.get("title"))
        posted_at = _pick(r.get("published_date"), r.get("publishedDate"))

    else:  # default
        likes = _num(_pick(r.get("likes"), r.get("likeCount")))
        comments = _num(_pick(r.get("comments"), r.get("commentCount")))
        shares = _num(_pick(r.get("shares"), r.get("shareCount")))
        views = _num(_pick(r.get("views"), r.get("viewCount"), r.get("playCount")))
        author_handle = _pick(r.get("author"), r.get("username"), r.get("ownerUsername"), r.get("channelTitle"))
        post_id = _pick(r.get("id"), r.get("postId"), r.get("videoId"), r.get("url"))
        post_url = _pick(r.get("url"), r.get("link"), r.get("permalink"))
        text = _pick(r.get("text"), r.get("caption"), r.get("title"), r.get("description"))
        posted_at = _pick(r.get("published_at"), r.get("publishedAt"), r.get("timestamp"))
        media_urls = _arr(r.get("media_urls"))
        hashtags = _arr(r.get("hashtags"))

    return Post(
        platform=platform,
        post_id=str(post_id or uuid.uuid4()),
        post_url=str(post_url or ""),
        author_handle=str(author_handle or ""),
        author_id=str(author_id or ""),
        posted_at=str(posted_at or ""),
        text=str(text or ""),
        media_urls=[str(m) for m in media_urls if m],
        metrics=Metrics(likes=likes, comments=comments, shares=shares, views=views),
        hashtags=[str(h) for h in hashtags if h],
        engagement_total=likes + comments + shares + views,
        scraped_at=datetime.now(timezone.utc).isoformat(),
        source_actor=str(source_actor or ""),
        raw=r,
    )


def normalize(raw_posts: list[dict]) -> list[Post]:
    out: list[Post] = []
    for env in raw_posts:
        platform = env.get("platform", "unknown")
        raw = env.get("raw") or {}
        source_actor = env.get("source_actor") or env.get("source") or ""

        # container expansion
        records: list[dict]
        if platform == "youtube" and isinstance(raw.get("items"), list):
            records = raw["items"]
        elif platform == "reddit" and isinstance(raw.get("data", {}).get("children"), list):
            records = [ch.get("data", ch) for ch in raw["data"]["children"]]
        elif platform == "google_trends":
            records = [raw]  # already one item per envelope from the collector
        else:
            records = [raw]

        for rec in records:
            out.append(_build_post(platform, rec or {}, source_actor))
    return out
