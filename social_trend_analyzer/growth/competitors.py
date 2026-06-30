"""Description → competitors (read-only).

Turns a free-text product description into a ranked set of competitor Instagram
accounts:

1. :func:`extract_keywords` — an LLM derives search keywords from the description
   (mirrors ``recommend.py``: prompt | structured output, guarded fallback so it
   runs offline with no OpenAI key).
2. :func:`discover_competitors` — those keywords drive a hashtag search via the
   existing Apify actor; the post authors are aggregated, ranked by reach/frequency,
   and returned as a :class:`CompetitorList`.

Reads only — never touches the logged-in account. Guarded: with no Apify token (or
no keywords) discovery returns an empty list.
"""
from __future__ import annotations

import logging
import re

from langchain_core.prompts import ChatPromptTemplate

from ..collectors.apify import run_actor_sync
from ..config import get_settings
from ..llm import get_chat
from .discovery import _ig_actor, _owner  # reuse, do not duplicate
from .schemas import CompetitorAccount, CompetitorList, KeywordSet

log = logging.getLogger(__name__)

_TOKEN = re.compile(r"[A-Za-z0-9]+")
_STOPWORDS = frozenset({
    "the", "a", "an", "and", "or", "for", "with", "of", "to", "in", "on", "at",
    "by", "from", "that", "this", "is", "are", "be", "our", "your", "their",
    "we", "you", "they", "it", "as", "product", "products", "app", "platform",
    "service", "solution", "best", "new", "online",
})


_KEYWORD_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "You are an Instagram growth strategist. Given a PRODUCT description, return "
            "up to {limit} short search keywords/hashtags (single words or tight phrases, "
            "no '#') that this product's competitors and target audience would use on "
            "Instagram. Prefer niche, specific terms over generic ones. Return only the "
            "keywords.",
        ),
        ("human", "PRODUCT:\n{product}"),
    ]
)


def keyword_chain():
    return _KEYWORD_PROMPT | get_chat(0.3).with_structured_output(KeywordSet)


def _fallback_keywords(product_description: str, limit: int) -> list[str]:
    """Deterministic keyword extraction used when the LLM is unavailable."""
    seen: set[str] = set()
    out: list[str] = []
    for raw in _TOKEN.findall(product_description or ""):
        token = raw.lower()
        if len(token) < 3 or token in _STOPWORDS or token in seen:
            continue
        seen.add(token)
        out.append(token)
        if len(out) >= limit:
            break
    return out


def _clean_keywords(keywords: list[str], limit: int) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for k in keywords:
        token = str(k).lstrip("#").strip()
        if not token or token.lower() in seen:
            continue
        seen.add(token.lower())
        out.append(token)
        if len(out) >= limit:
            break
    return out


def extract_keywords(product_description: str, config: dict) -> list[str]:
    """Derive search keywords from a product description (LLM, guarded fallback)."""
    limit = int(config.get("growth_keyword_limit", 8))
    description = (product_description or "").strip()
    if not description:
        return []
    if not get_settings().has_openai():
        log.info("extract_keywords: OpenAI not configured — using deterministic fallback")
        return _fallback_keywords(description, limit)
    try:
        result = keyword_chain().invoke({"product": description, "limit": limit})
        keywords = _clean_keywords(result.keywords, limit)
        return keywords or _fallback_keywords(description, limit)
    except Exception as e:  # noqa: BLE001
        log.warning("Keyword extraction LLM failed: %s — using fallback", e)
        return _fallback_keywords(description, limit)


def _engagement(item: dict) -> int:
    return int(item.get("likesCount") or 0) + int(item.get("commentsCount") or 0)


def _followers(item: dict) -> int:
    return int(
        item.get("ownerFollowersCount")
        or (item.get("owner") or {}).get("followersCount")
        or 0
    )


def discover_competitors(
    product_description: str, config: dict, limit: int | None = None
) -> CompetitorList:
    """Description → keywords → hashtag search → ranked competitor accounts.

    Authors are aggregated across the scraped posts; ranking is a reach proxy
    (occurrence frequency + summed post engagement), since the actor does not
    reliably return follower counts.
    """
    keywords = extract_keywords(product_description, config)
    if not keywords:
        return CompetitorList(competitors=[])

    results_limit = int(config.get("growth_search_limit", 50))
    top_n = int(limit or config.get("growth_competitor_limit", 10))
    tag_urls = [f"https://www.instagram.com/explore/tags/{k}/" for k in keywords]
    items = run_actor_sync(
        _ig_actor(config),
        {"resultsType": "posts", "directUrls": tag_urls, "resultsLimit": results_limit},
    )

    agg: dict[str, dict] = {}
    for it in items:
        handle = _owner(it)
        if not handle:
            continue
        row = agg.setdefault(handle, {
            "username": handle,
            "full_name": it.get("ownerFullName", ""),
            "posts": 0,
            "engagement": 0,
            "followers": 0,
        })
        row["posts"] += 1
        row["engagement"] += _engagement(it)
        row["followers"] = max(row["followers"], _followers(it))
        if not row["full_name"]:
            row["full_name"] = it.get("ownerFullName", "")

    ranked = sorted(
        agg.values(), key=lambda r: (r["posts"], r["engagement"], r["followers"]), reverse=True
    )

    keyword_ref = ", ".join(keywords)
    max_posts = ranked[0]["posts"] if ranked else 1
    competitors = [
        CompetitorAccount(
            username=r["username"],
            full_name=r["full_name"],
            score=min(100, round(100 * r["posts"] / max_posts)),
            reason=f"appears in {r['posts']} post(s) under matched keywords",
            followers=r["followers"],
            source_ref=keyword_ref,
        )
        for r in ranked[:top_n]
    ]
    return CompetitorList(competitors=competitors)
