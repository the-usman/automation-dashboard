"""LangChain LLM chains — the heart of the port.

Each chain replaces an n8n ``OpenAI ·`` node *plus* its ``Parse * JSON`` node:
the model is bound with ``.with_structured_output(Model)`` so it returns a
validated Pydantic object instead of a string we have to JSON-parse and pray.
"""
from __future__ import annotations

from functools import lru_cache

from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI, OpenAIEmbeddings

from .config import get_settings
from .schemas import IdeaList, Sentiment, TrendList


@lru_cache
def get_chat(temperature: float = 0.4) -> ChatOpenAI:
    s = get_settings()
    return ChatOpenAI(model=s.openai_model, api_key=s.openai_api_key, temperature=temperature)


@lru_cache
def get_embeddings() -> OpenAIEmbeddings:
    s = get_settings()
    return OpenAIEmbeddings(model=s.openai_embedding_model, api_key=s.openai_api_key)


# --------------------------------------------------------------------------- #
# Trend extraction
# --------------------------------------------------------------------------- #
_TREND_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "You are a social-media trend analyst for the niche '{niche}'. "
            "From the supplied posts, identify the distinct emerging content TRENDS "
            "(content formats, hooks, topics, audio, challenges) — not individual posts. "
            "Score each 0-100 on how big an opportunity it is for this niche, weighing "
            "engagement, recency, cross-platform spread and fit. Cite supporting post URLs.\n"
            "{rag_context}",
        ),
        (
            "human",
            "Niche keywords: {keywords}\n"
            "Platforms in scope: {platforms}\n\n"
            "POSTS (json lines):\n{posts}\n\n"
            "Extract the trends.",
        ),
    ]
)


def trend_chain():
    return _TREND_PROMPT | get_chat(0.3).with_structured_output(TrendList)


# --------------------------------------------------------------------------- #
# Sentiment analysis
# --------------------------------------------------------------------------- #
_SENTIMENT_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "You analyse audience sentiment across social posts. Return overall "
            "positive/neutral/negative percentages (must sum to ~100), the dominant "
            "keywords/themes driving the conversation, and a one-paragraph summary.",
        ),
        ("human", "POSTS (json lines):\n{posts}"),
    ]
)


def sentiment_chain():
    return _SENTIMENT_PROMPT | get_chat(0.0).with_structured_output(Sentiment)


# --------------------------------------------------------------------------- #
# Idea generation
# --------------------------------------------------------------------------- #
_IDEA_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "You are a content strategist for '{client_name}' (niche: {niche}). "
            "Brand voice: {brand_voice}. For EACH eligible trend produce up to "
            "{max_ideas} concrete, ready-to-brief content ideas. Each idea must set "
            "trend_ref to the trend name, and include a structured_brief, a quick_draft "
            "(caption + hashtags), a visual_brief, a cta, a recommended_pipeline and an "
            "estimated_effort. Tailor platform/format to where the trend is strongest.",
        ),
        (
            "human",
            "Eligible trends (json):\n{trends}\n\n"
            "Audience sentiment context:\n{sentiment}\n\n"
            "Generate the ideas.",
        ),
    ]
)


def idea_chain():
    return _IDEA_PROMPT | get_chat(0.7).with_structured_output(IdeaList)


# --------------------------------------------------------------------------- #
# Email report (free-text, not structured)
# --------------------------------------------------------------------------- #
_EMAIL_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "Write a concise, well-formatted HTML email summarising this run's social "
            "media trends and the recommended content ideas for the client. Lead with the "
            "top opportunities. Keep it skimmable.",
        ),
        ("human", "Trends:\n{trends}\n\nIdeas:\n{ideas}\n\nSentiment:\n{sentiment}"),
    ]
)


def email_chain():
    return _EMAIL_PROMPT | get_chat(0.5)
