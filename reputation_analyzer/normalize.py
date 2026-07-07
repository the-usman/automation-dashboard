"""Normalize each platform's raw Apify items into ``NormalizedPost``.

Each actor returns a different shape, so we map per-platform field names to the
common ``{text, post_url, date, likes, comments}``. Missing keys default to
empty/zero — the scorer tolerates sparse data.
"""
from __future__ import annotations

from typing import Any

from .schemas import NormalizedPost

# Per-platform field name → our field. First present key wins.
_FIELD_MAP: dict[str, dict[str, tuple[str, ...]]] = {
    "instagram": {
        "text": ("caption", "text"),
        "post_url": ("url", "postUrl", "displayUrl"),
        "date": ("timestamp", "takenAt"),
        "likes": ("likesCount", "likes"),
        "comments": ("commentsCount", "comments"),
    },
    "x": {
        "text": ("text", "full_text", "fullText"),
        "post_url": ("url", "twitterUrl", "tweetUrl"),
        "date": ("createdAt", "created_at", "date"),
        "likes": ("likeCount", "favoriteCount", "likes"),
        "comments": ("replyCount", "replies", "comments"),
    },
    "tiktok": {
        "text": ("text", "desc", "description"),
        "post_url": ("webVideoUrl", "videoUrl", "url"),
        "date": ("createTimeISO", "createTime", "date"),
        "likes": ("diggCount", "likesCount", "likes"),
        "comments": ("commentCount", "comments"),
    },
}


def _first(item: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for k in keys:
        if item.get(k) not in (None, ""):
            return item[k]
    return None


def _as_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def normalize(platform: str, raw_items: list[dict]) -> list[NormalizedPost]:
    field_map = _FIELD_MAP.get(platform)
    if not field_map:
        return []
    posts: list[NormalizedPost] = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        posts.append(
            NormalizedPost(
                text=str(_first(item, field_map["text"]) or ""),
                post_url=str(_first(item, field_map["post_url"]) or ""),
                date=str(_first(item, field_map["date"]) or ""),
                likes=_as_int(_first(item, field_map["likes"])),
                comments=_as_int(_first(item, field_map["comments"])),
            )
        )
    return posts
