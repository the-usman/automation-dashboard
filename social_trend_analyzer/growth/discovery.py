"""Discovery via Apify (reads only — never touches the logged-in account).

Two strategies:

* :func:`search_users` — scrape posts under the product's hashtags/keywords and
  collect the post authors as follow candidates.
* :func:`competitor_commenters` — scrape a competitor's most recent N posts and
  collect the users who commented as follow candidates.

Both reuse :func:`social_trend_analyzer.collectors.apify.run_actor_sync` and the
verified default Instagram actor. Guarded: with no Apify token they return ``[]``.
"""
from __future__ import annotations

import logging

from ..collectors.apify import DEFAULT_ACTORS, run_actor_sync
from .schemas import CandidateUser

log = logging.getLogger(__name__)


def _ig_actor(config: dict) -> str:
    return config.get("apify_actor_instagram") or DEFAULT_ACTORS["instagram"]


def _owner(item: dict) -> str:
    return (
        item.get("ownerUsername")
        or item.get("ownerUserName")
        or (item.get("owner") or {}).get("username")
        or ""
    )


def search_users(keywords: list[str], config: dict, limit: int | None = None) -> list[CandidateUser]:
    """Hashtag/keyword discovery → unique post authors as candidates."""
    tags = [str(k).lstrip("#").strip() for k in (keywords or []) if str(k).strip()]
    if not tags:
        return []
    results_limit = int(limit or config.get("growth_search_limit", 50))
    tag_urls = [f"https://www.instagram.com/explore/tags/{t}/" for t in tags]
    items = run_actor_sync(
        _ig_actor(config),
        {"resultsType": "posts", "directUrls": tag_urls, "resultsLimit": results_limit},
    )

    out: list[CandidateUser] = []
    seen: set[str] = set()
    for it in items:
        handle = _owner(it)
        if not handle or handle in seen:
            continue
        seen.add(handle)
        out.append(CandidateUser(
            username=handle,
            full_name=it.get("ownerFullName", ""),
            source="search",
            source_ref=", ".join(tags),
            context=(it.get("caption") or "")[:200],
        ))
    return out


def _normalize_profile_url(profile_url: str) -> str:
    p = (profile_url or "").strip()
    if not p:
        return ""
    if p.startswith("http"):
        return p
    return f"https://www.instagram.com/{p.strip('/').lstrip('@')}/"


def competitor_commenters(profile_url: str, n_posts: int, config: dict) -> list[CandidateUser]:
    """Competitor's recent ``n_posts`` posts → unique commenters as candidates."""
    url = _normalize_profile_url(profile_url)
    if not url:
        return []
    n = int(n_posts or 5)
    posts = run_actor_sync(
        _ig_actor(config),
        {"resultsType": "posts", "directUrls": [url], "resultsLimit": n},
    )

    out: list[CandidateUser] = []
    seen: set[str] = set()
    for post in posts[:n]:
        comments = post.get("latestComments") or post.get("comments") or []
        for c in comments:
            handle = (
                c.get("ownerUsername")
                or c.get("username")
                or (c.get("owner") or {}).get("username")
                or ""
            )
            if not handle or handle in seen:
                continue
            seen.add(handle)
            out.append(CandidateUser(
                username=handle,
                full_name=c.get("ownerFullName", "") or (c.get("owner") or {}).get("full_name", ""),
                source="competitor",
                source_ref=url,
                context=(c.get("text") or "")[:200],
            ))
    return out
