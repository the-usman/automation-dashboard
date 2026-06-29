"""CLI entry point.

    python -m social_trend_analyzer.cli --mode client00
    python -m social_trend_analyzer.cli --mode client --client-id test01 --platforms instagram,tiktok
    python -m social_trend_analyzer.cli --draw   # print the graph as mermaid

The --trigger-json flag mirrors the n8n webhook body for full control.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys

from dotenv import load_dotenv

from .graph import CLIENT_GRAPH, MAIN_GRAPH, run


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Social Media Trend Analyzer (LangGraph)")
    parser.add_argument("--mode", choices=["client", "client00"], default="client")
    parser.add_argument("--client-id", default="", help="restrict client-mode run to one client_id")
    parser.add_argument("--platforms", default="", help="comma-separated platform filter override")
    parser.add_argument("--deep-dive", action="store_true")
    parser.add_argument("--lookback-days", type=int, default=None)
    parser.add_argument("--trigger-json", default="", help="raw trigger payload JSON (overrides other flags)")
    parser.add_argument("--draw", action="store_true", help="print mermaid for both graphs and exit")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(levelname)s %(name)s: %(message)s")

    if args.draw:
        print("=== MAIN GRAPH ===")
        print(MAIN_GRAPH.get_graph().draw_mermaid())
        print("\n=== CLIENT SUBGRAPH ===")
        print(CLIENT_GRAPH.get_graph().draw_mermaid())
        return 0

    if args.trigger_json:
        trigger = json.loads(args.trigger_json)
    else:
        trigger = {"mode": args.mode, "deep_dive": args.deep_dive}
        if args.client_id:
            trigger["client_id"] = args.client_id
        if args.platforms:
            trigger["platforms"] = [p.strip() for p in args.platforms.split(",") if p.strip()]
        if args.lookback_days is not None:
            trigger["lookback_days_override"] = args.lookback_days

    result = run(trigger)
    summary = {
        "run_id": result.get("run_id"),
        "mode": result.get("mode"),
        "clients_processed": len(result.get("results", [])),
        "results": result.get("results", []),
    }
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
