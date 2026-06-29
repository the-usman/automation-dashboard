"""Social Media Trend Analyzer — LangGraph / LangChain port of the n8n workflow.

Public API:
    from social_trend_analyzer import run
    run({"mode": "client00"})
"""
from .graph import CLIENT_GRAPH, MAIN_GRAPH, run

__all__ = ["run", "MAIN_GRAPH", "CLIENT_GRAPH"]
