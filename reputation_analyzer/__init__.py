"""Social Media Reputation Analyzer — standalone feature.

Scrapes a brand/person's public profiles (Instagram / X / TikTok) via Apify,
normalizes their posts, and asks OpenAI for a single reputation score with
per-platform breakdown and strong/weak points.

Completely independent of ``social_trend_analyzer`` — its own config, Apify
client, and OpenAI scorer. Reads the plain env names ``OPENAI_API_KEY`` and
``APIFY_API_TOKEN``.
"""
from __future__ import annotations

from .logfile import setup_file_logging
from .schemas import ReputationRequest, ReputationResponse
from .service import check_reputation

# Persist every reputation log line to logs/reputation/reputation.log.
setup_file_logging()

__all__ = ["ReputationRequest", "ReputationResponse", "check_reputation"]
