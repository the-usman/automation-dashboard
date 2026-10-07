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
    # Growth campaigns write a per-campaign activity log (timestamped) here:
    # discovery/harvest results, Instagram suggestions, and every follow.
    growth_log_dir: str = "./logs/growth"

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
    "ig_action_delay_seconds": 4,      # small floor sleep inside the client after an action
    # Long, randomized gap the CAMPAIGN waits between follows so Instagram does not
    # rate-limit (429) or block the device. Default 5-10 min → a batch spreads across
    # the day. Tune down only for tiny test runs.
    "ig_follow_min_seconds": 300,      # 5 minutes
    "ig_follow_max_seconds": 600,      # 10 minutes
    "ig_read_delay_seconds": 8,        # sleep between READ calls (hashtag/comments/likers) — larger than actions; reads trigger anti-bot faster
    "ig_delay_jitter_seconds": 4,      # random extra 0..N sec added to every delay so it isn't robotic
    "ig_daily_action_cap": 150,        # hard ceiling on actions per run (stay 150-200/day)
    "growth_search_limit": 50,         # Apify resultsLimit for discovery scrapes
    "growth_min_score": 60,            # default LLM cutoff for action="follow"
    "growth_keyword_limit": 8,         # max keywords derived from a product description
    "growth_competitor_limit": 10,     # max competitor accounts returned per discovery
    # Read-volume caps — the single biggest cause of session logouts is fetching
    # ALL comments/likers of every post. Keep these small.
    "growth_hashtags_max": 6,          # max distinct hashtags to scan for competitors
    "growth_posts_per_hashtag": 10,    # top posts pulled per hashtag
    "growth_comments_per_post": 25,    # cap comments read per post (never "all")
    "growth_likers_per_post": 25,      # cap likers read per post (never "all")
    # instagram_suggested mode — reload the "Suggested for you" list until the
    # follow target is met, then stop and report partial.
    "growth_suggested_max_empty_reloads": 5,  # consecutive reloads with no new users before giving up
    "growth_suggested_max_reloads": 40,       # absolute hard cap on reload calls
    # Follow PEOPLE only: check each candidate's profile right before following
    # and skip business/brand pages (one cached user_info read per candidate).
    "growth_skip_business_accounts": True,
    # Bulk unfollow — cap on how much of the following list is fetched in one
    # read (0 = all); huge lists paginate the private API hard.
    "growth_following_fetch_max": 1000,
    # --- Lead Generation workflow (Apify Google Maps + Leads Finder) ---
    # Actor ids. Google Maps is pinned to the immutable actor ID (safe against slug
    # renames); it is the "compass/crawler-google-places" scraper. Apify also accepts
    # the "compass~crawler-google-places" slug form. Leads Finder uses the slug form.
    "leadgen_google_maps_actor": "compass~crawler-google-places",
    "leadgen_leads_finder_actor": "code_crafter~leads-finder",
    # Google Maps cost caps / defaults applied to generated filters.
    "leadgen_gmaps_max_places": 50,        # maxCrawledPlacesPerSearch ceiling
    "leadgen_gmaps_scrape_contacts": True, # enrich with website emails/socials (extra cost)
    "leadgen_gmaps_language": "en",
    # Employee-leads enrichment (maximumLeadsEnrichmentRecords) is a pricier add-on; the
    # generator leaves it off (0) and this caps it if the LLM/user turns it on.
    "leadgen_gmaps_max_leads_per_place": 10,
    # Leads Finder — free Apify plan caps this actor at 100 leads per run.
    "leadgen_leads_fetch_count": 100,
    # These actors (esp. Google Maps with contact enrichment) run longer than the
    # growth scrapes, so poll with a bigger budget: 15s * 80 ≈ 20 min.
    "leadgen_poll_interval_seconds": 15,
    "leadgen_poll_max_attempts": 80,
}


@lru_cache
def get_settings() -> Settings:
    return Settings()
