"""One-time Instagram session bootstrapper.

Instagram challenges password logins from unrecognized servers/IPs
(``ChallengeRequired``). Run this ONCE, interactively, on a trusted machine to
solve the checkpoint and save a session file. The growth campaign/task runners
then load that session (``ig_session_dir/<username>.json``) and stop getting
challenged.

Usage:
    python scripts/ig_login.py                 # prompts for username + password
    python scripts/ig_login.py <username>      # prompts for password only

When Instagram asks, paste the code it emails/SMSes you. On success the session
is written to the configured ig_session_dir.
"""
from __future__ import annotations

import sys
from getpass import getpass
from pathlib import Path

# Allow running as `python scripts/ig_login.py` — put the repo root (parent of
# this scripts/ dir) on sys.path so `social_trend_analyzer` imports.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from instagrapi import Client

from social_trend_analyzer.config import get_settings


def _code_handler(username: str, choice) -> str:
    return input(f"Enter the verification code Instagram sent for @{username} ({choice}): ").strip()


def main() -> int:
    username = sys.argv[1] if len(sys.argv) > 1 else input("Instagram username: ").strip()
    if not username:
        print("No username given.")
        return 1
    password = getpass("Instagram password (hidden): ")

    cl = Client()
    cl.challenge_code_handler = _code_handler
    cl.change_password_handler = lambda u: getpass(f"New password for @{u} (if asked): ")

    print(f"Logging in as @{username} … solve any prompt Instagram shows.")
    cl.login(username, password)

    session_dir = Path(get_settings().ig_session_dir)
    session_dir.mkdir(parents=True, exist_ok=True)
    safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in username)
    path = session_dir / f"{safe}.json"
    cl.dump_settings(str(path))

    print(f"\n✓ Session saved to {path}")
    print("The server will now reuse this session — restart it and run the campaign again.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
