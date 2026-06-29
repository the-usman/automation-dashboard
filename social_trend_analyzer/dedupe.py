"""Dedupe normalized posts against recent history (Trend History sheet).

Port of n8n "Load Recent History" + "Dedupe Against History": drop any
(platform, post_id) already captured within the dedupe window. If no sheet is
configured, only in-run dedupe is applied.
"""
from __future__ import annotations

import logging

from .schemas import Post
from .storage import load_recent_history

log = logging.getLogger(__name__)


def dedupe(posts: list[Post], config: dict, client_id: str) -> list[Post]:
    window_days = int(config.get("dedupe_window_days", 90))
    seen: set[str] = set()
    for h in load_recent_history(client_id, window_days):
        platform = h.get("platform")
        post_id = h.get("post_id")
        if platform and post_id:
            seen.add(f"{platform}::{post_id}")

    out: list[Post] = []
    for p in posts:
        key = f"{p.platform}::{p.post_id}"
        if key in seen:
            continue
        seen.add(key)  # also dedupe within this run
        out.append(p)
    log.info("Dedupe: %d -> %d posts", len(posts), len(out))
    return out
