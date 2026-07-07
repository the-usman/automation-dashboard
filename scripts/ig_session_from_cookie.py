"""Bootstrap an Instagram session from a BROWSER sessionid cookie.

More reliable than password login: your browser is already logged in and trusted
by Instagram, so reusing its ``sessionid`` avoids the ``ChallengeRequired``
checkpoint entirely.

How to get the cookie:
    1. Log in to https://www.instagram.com in Chrome as the target account.
    2. Open DevTools (F12) -> Application -> Storage -> Cookies ->
       https://www.instagram.com
    3. Copy the VALUE of the `sessionid` cookie
       (looks like: 12345678%3AAbCdEf...%3A12%3A...).

Usage:
    python scripts/ig_session_from_cookie.py
    python scripts/ig_session_from_cookie.py "<sessionid-value>"

On success the session is saved to ig_session_dir/<username>.json and the server
reuses it — restart the server and run the campaign again.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Allow running as `python scripts/ig_session_from_cookie.py` — put the repo root
# (parent of this scripts/ dir) on sys.path so `social_trend_analyzer` imports.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from instagrapi import Client

from social_trend_analyzer.config import get_settings


def main() -> int:
    sessionid = sys.argv[1] if len(sys.argv) > 1 else input("Paste the browser sessionid cookie: ").strip()
    sessionid = sessionid.strip().strip('"').strip("'")
    if not sessionid:
        print("No sessionid given.")
        return 1

    cl = Client()
    print("Validating sessionid with Instagram …")
    cl.login_by_sessionid(sessionid)

    username = cl.account_info().username
    session_dir = Path(get_settings().ig_session_dir)
    session_dir.mkdir(parents=True, exist_ok=True)
    safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in username)
    path = session_dir / f"{safe}.json"
    cl.dump_settings(str(path))

    print(f"\n✓ Logged in as @{username}")
    print(f"✓ Session saved to {path}")
    print("Restart the server and run the campaign again — it will reuse this session.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
