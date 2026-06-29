"""Settings (credentials) and runtime config.

Mirrors the n8n workflow's two layers of configuration:

* **Credentials** — API keys / tokens (n8n "credentials"). Loaded from env / .env.
* **Runtime config** — the analysis tuning knobs that lived in the *Config*
  Google Sheet (``Parse Config`` node). Defaults live here; if a Google Sheet is
  configured they are overlaid by :func:`social_trend_analyzer.storage.load_config_sheet`.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Any

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Credentials + endpoints, sourced from environment / .env."""

    model_config = SettingsConfigDict(env_file=".env", env_prefix="TREND_", extra="ignore")

    # --- OpenAI ---
    openai_api_key: str = ""
    openai_model: str = "gpt-4o"
    openai_embedding_model: str = "text-embedding-3-small"

    # --- Apify (Instagram / TikTok / X / Facebook / LinkedIn) ---
    apify_token: str = ""

    # --- Free official APIs ---
    youtube_api_key: str = ""
    reddit_client_id: str = ""
    reddit_client_secret: str = ""
    reddit_user_agent: str = "social-trend-analyzer/1.0"

    # --- Optional integrations ---
    qdrant_url: str = ""
    qdrant_api_key: str = ""
    buzzsumo_api_key: str = ""
    notion_token: str = ""

    # --- Storage: Google Sheets ---
    # Path to a service-account JSON key file, and the spreadsheet id.
    google_service_account_json: str = ""
    google_sheet_id: str = ""

    # --- Instagram Growth Agent ---
    # Credentials live in MongoDB (same cluster as the email-outreach workflow),
    # never in env. Fernet key (optional) decrypts password/totp at rest.
    mongo_uri: str = ""
    mongo_db: str = "project_db"
    ig_credential_collection: str = "instagram_accounts"
    fernet_key: str = ""
    ig_session_dir: str = "./.ig_sessions"

    def has_openai(self) -> bool:
        return bool(self.openai_api_key)

    def has_mongo(self) -> bool:
        return bool(self.mongo_uri)


# Runtime config defaults — overridden by the Config sheet rows when present.
DEFAULT_CONFIG: dict[str, Any] = {
    "lookback_days": 14,
    "default_region": "US",
    "default_min_trend_score": 70,
    "apify_results_limit": 50,
    "apify_poll_max_attempts": 15,
    "apify_poll_interval_seconds": 20,
    "dedupe_window_days": 90,
    "buzzsumo_enabled": False,
    "qdrant_url": "",
    # client00 market-scan defaults
    "client00_keywords": ["viral", "trending", "popular"],
    "client00_subreddits": ["popular", "all", "InternetIsBeautiful", "OutOfTheLoop"],
    "client00_region": "US",
    # Apify actor overrides (org~actor). Empty -> use verified defaults in collectors/apify.py
    "apify_actor_instagram": "",
    "apify_actor_tiktok": "",
    "apify_actor_twitter": "",
    "apify_actor_facebook": "",
    "apify_actor_linkedin": "",
    # --- Instagram Growth Agent tuning knobs ---
    "ig_action_delay_seconds": 4,      # sleep between follow/unfollow (3-5s recommended)
    "ig_daily_action_cap": 150,        # hard ceiling on actions per run (stay 150-200/day)
    "growth_search_limit": 50,         # Apify resultsLimit for discovery scrapes
    "growth_min_score": 60,            # default LLM cutoff for action="follow"
}


@lru_cache
def get_settings() -> Settings:
    return Settings()
