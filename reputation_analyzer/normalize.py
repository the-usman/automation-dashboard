"""Normalize each platform's raw Apify items into ``NormalizedPost``.

Each actor returns a different shape, so we map per-platform field names to the
common ``{text, post_url, date, likes, comments}``. Missing keys default to
empty/zero — the scorer tolerates sparse data.

Two kinds of raw items are dropped here:
- placeholder rows with neither text nor a post URL (e.g. the tweet-scraper's
  ``{"noResults": true}`` markers);
- posts older than the ``LOOKBACK_DAYS`` window, so reputation is always judged
  on the last N days even when an actor ignores its date-filter input param.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from . import config
from .schemas import NormalizedPost

log = logging.getLogger(__name__)

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


def _parse_post_date(value: str) -> datetime | None:
    """Best-effort parse of the date shapes the actors emit; None if unknown."""
    v = (value or "").strip()
    if not v:
        return None
    if v.isdigit():  # unix seconds (tiktok createTime fallback)
        try:
            return datetime.fromtimestamp(int(v), tz=timezone.utc)
        except (ValueError, OverflowError, OSError):
            return None
    try:
        dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError:
        try:
            dt = datetime.strptime(v, "%a %b %d %H:%M:%S %z %Y")  # X classic
        except ValueError:
            return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def normalize(platform: str, raw_items: list[dict]) -> list[NormalizedPost]:
    field_map = _FIELD_MAP.get(platform)
    if not field_map:
        return []
    window_start = datetime.now(timezone.utc) - timedelta(days=config.LOOKBACK_DAYS)
    posts: list[NormalizedPost] = []
    dropped_old = 0
    dropped_junk = 0
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        text = str(_first(item, field_map["text"]) or "")
        post_url = str(_first(item, field_map["post_url"]) or "")
        if not text and not post_url:
            dropped_junk += 1  # actor placeholder rows, e.g. {"noResults": true}
            continue
        date = str(_first(item, field_map["date"]) or "")
        posted_at = _parse_post_date(date)
        # Unparseable dates are kept — better sparse data than dropped posts.
        if posted_at is not None and posted_at < window_start:
            dropped_old += 1
            continue
        posts.append(
            NormalizedPost(
                text=text,
                post_url=post_url,
                date=date,
                likes=_as_int(_first(item, field_map["likes"])),
                comments=_as_int(_first(item, field_map["comments"])),
            )
        )
    if dropped_junk or dropped_old:
        log.info(
            "normalize %s: dropped %d placeholder row(s), %d post(s) older than %d days",
            platform, dropped_junk, dropped_old, config.LOOKBACK_DAYS,
        )
    return posts
