"""Instagram Growth Agent — a LangGraph agent that recommends users to follow
and executes follow/unfollow actions.

Reads (competitor posts, commenters, hashtag/keyword discovery) go through the
Apify scraper already wired into ``collectors/apify.py`` so they never touch the
logged-in account. Writes (follow/unfollow) go through instagrapi, the only
library that can perform actions. Credentials are loaded from MongoDB, never env.

Public API:
    from social_trend_analyzer.growth import run
    run({"mode": "discover_competitor", "competitor_url": "https://instagram.com/...",
         "product_description": "..."})
"""
from .competitors import discover_competitors, extract_keywords
from .graph import GROWTH_GRAPH, run

__all__ = ["run", "GROWTH_GRAPH", "discover_competitors", "extract_keywords"]
