"""CLI for the Instagram Growth Agent.

    # Recommend users from competitor's recent posts' commenters
    python -m social_trend_analyzer.growth.cli recommend \
        --competitor https://www.instagram.com/competitor/ --product "AI fitness app"

    # Recommend users by product hashtags/keywords
    python -m social_trend_analyzer.growth.cli recommend \
        --product "AI fitness app" --keywords fitness,workout,gymtok

    # Execute follow / unfollow on a username list
    python -m social_trend_analyzer.growth.cli act --follow user1,user2 --credential-id cred01
    python -m social_trend_analyzer.growth.cli act --unfollow user1 --credential-id cred01
"""
from __future__ import annotations

import argparse
import json
import logging
import sys

from dotenv import load_dotenv

from .graph import GROWTH_GRAPH, run


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Instagram Growth Agent (LangGraph)")
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("--draw", action="store_true", help="print the graph as mermaid and exit")
    sub = parser.add_subparsers(dest="command")

    rec = sub.add_parser("recommend", help="discover + rank users to follow")
    rec.add_argument("--product", default="", help="product description for ranking")
    rec.add_argument("--keywords", default="", help="comma-separated hashtags/keywords (product mode)")
    rec.add_argument("--competitor", default="", help="competitor profile URL (commenter mode)")
    rec.add_argument("--n-posts", type=int, default=5, help="competitor recent posts to scan")
    rec.add_argument("--min-score", type=float, default=60)

    act = sub.add_parser("act", help="follow/unfollow a username list")
    group = act.add_mutually_exclusive_group(required=True)
    group.add_argument("--follow", default="", help="comma-separated usernames to follow")
    group.add_argument("--unfollow", default="", help="comma-separated usernames to unfollow")
    act.add_argument("--credential-id", required=True, help="MongoDB credential_id for the IG account")

    return parser


def _trigger_from_args(args: argparse.Namespace) -> dict:
    if args.command == "recommend":
        mode = "discover_competitor" if args.competitor else "discover_product"
        return {
            "mode": mode,
            "product_description": args.product,
            "keywords": args.keywords,
            "competitor_url": args.competitor,
            "n_posts": args.n_posts,
            "min_score": args.min_score,
        }
    # act
    action = "follow" if args.follow else "unfollow"
    return {
        "mode": "execute_actions",
        "action": action,
        "usernames": args.follow or args.unfollow,
        "credential_id": args.credential_id,
    }


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = _build_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(levelname)s %(name)s: %(message)s")

    if args.draw:
        print(GROWTH_GRAPH.get_graph().draw_mermaid())
        return 0

    if not args.command:
        parser.print_help()
        return 1

    result = run(_trigger_from_args(args))
    summary = {
        "run_id": result.get("run_id"),
        "mode": result.get("mode"),
        "recommendations": [r.model_dump() for r in result.get("recommendations", [])],
        "action_results": [a.model_dump() for a in result.get("action_results", [])],
        "errors": result.get("errors", []),
    }
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
