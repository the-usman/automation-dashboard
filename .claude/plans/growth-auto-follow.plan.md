# Plan: Description → Competitors (Auto-Follow Growth, Milestone 1)

**Source PRD**: `.claude/prds/growth-auto-follow.prd.md`
**Selected Milestone**: #1 — Description → competitors (operator submits only a description; system returns a verified competitor set)
**Complexity**: Small–Medium

## Summary
Add a read-only step that turns a free-text **product description** into a ranked set
of competitor Instagram accounts: an LLM derives search keywords from the description,
those keywords drive a hashtag search via the existing Apify actor, and the post authors
are deduped and ranked by reach/engagement into a `CompetitorList`. This is the entry
point for the new "description-only" growth flow; later milestones (audience harvesting,
scheduler, execution) consume the competitor set this produces.

## Patterns to Mirror
| Category | Source | Pattern |
|---|---|---|
| LLM structured chain + guarded fallback | `social_trend_analyzer/growth/recommend.py:21-41,64-80` | `_PROMPT = ChatPromptTemplate(...)`; `def x_chain(): return _PROMPT \| get_chat(0.3).with_structured_output(Model)`; wrap `.invoke` in try/except, fall back to neutral result when `get_settings().has_openai()` is false |
| Apify hashtag discovery | `social_trend_analyzer/growth/discovery.py:36-62` | `run_actor_sync(_ig_actor(config), {"resultsType":"posts","directUrls":tag_urls,"resultsLimit":n})`; dedupe authors via `_owner(item)` + a `seen` set; return `[]` when no token/keywords |
| Pydantic structured-output models | `social_trend_analyzer/growth/schemas.py:28-47` | Plain `BaseModel` + `Field(description=...)`, list-wrapper model (`RecommendationList`) for `.with_structured_output` |
| Config constants | `social_trend_analyzer/config.py` (`DEFAULT_CONFIG`: `growth_search_limit`, `growth_min_score`) | Add tunables to `DEFAULT_CONFIG`, read via `config.get("key", default)` |
| Tests (guarded, monkeypatched) | `tests/growth/test_discovery.py`, `tests/growth/test_recommend.py` | pytest + monkeypatch `run_actor_sync` / disable OpenAI; assert offline fallback and dedupe behavior; AAA structure |

## Files to Change
| File | Action | Why |
|---|---|---|
| `social_trend_analyzer/growth/schemas.py` | UPDATE | Add `CompetitorAccount` and `CompetitorList` (structured-output target); optionally `KeywordSet` for keyword extraction |
| `social_trend_analyzer/growth/competitors.py` | CREATE | New cohesive module: `extract_keywords(description, config)` (LLM chain + fallback) and `discover_competitors(description, config, limit)` (keywords → hashtag search → rank) |
| `social_trend_analyzer/config.py` | UPDATE | Add `DEFAULT_CONFIG` tunables: `growth_competitor_limit` (default 10), `growth_keyword_limit` (default 8) |
| `social_trend_analyzer/growth/__init__.py` | UPDATE | Export `discover_competitors` / `extract_keywords` for downstream milestones |
| `tests/growth/test_competitors.py` | CREATE | Unit tests: keyword fallback when LLM disabled, hashtag discovery dedupe/ranking with monkeypatched `run_actor_sync`, empty-input guards |

## Tasks

### Task 1: Add competitor schemas
- **Action**: In `schemas.py`, add `CompetitorAccount(username: str, full_name: str = "", score: int = 0 [0-100], reason: str = "", followers: int = 0, source_ref: str = "")` and `CompetitorList(competitors: list[CompetitorAccount])`. Optionally `KeywordSet(keywords: list[str])` for the extraction chain's structured output.
- **Mirror**: `schemas.py:38-47` (`UserRecommendation` / `RecommendationList`) — `Field(description=...)`, list-wrapper model.
- **Validate**: `python -c "from social_trend_analyzer.growth.schemas import CompetitorAccount, CompetitorList; CompetitorList(competitors=[])"`

### Task 2: Keyword extraction from description
- **Action**: In new `competitors.py`, add `_KEYWORD_PROMPT` + `keyword_chain()` and `extract_keywords(product_description, config)` returning `list[str]`. Guarded: when `get_settings().has_openai()` is false (or LLM raises), fall back to a deterministic extraction (lowercase, strip stopwords/punctuation, take top `growth_keyword_limit` tokens). Cap output length via `growth_keyword_limit`.
- **Mirror**: `recommend.py:21-41,64-80` — prompt | structured output, try/except → `_fallback`.
- **Validate**: `pytest tests/growth/test_competitors.py -k keyword -q`

### Task 3: Discover + rank competitors
- **Action**: In `competitors.py`, add `discover_competitors(product_description, config, limit=None)`:
  1. `keywords = extract_keywords(product_description, config)`; return `[]` if empty.
  2. Build hashtag explore URLs and call `run_actor_sync(_ig_actor(config), {"resultsType":"posts","directUrls":tag_urls,"resultsLimit":growth_search_limit})`.
  3. Aggregate by author handle: count occurrences across posts and sum available engagement (`likesCount`/`commentsCount`); capture `ownerFullName` and any follower count if the actor returns it.
  4. Rank by reach/frequency proxy, return top `limit or growth_competitor_limit` as `CompetitorAccount` with a short `reason` (e.g. "appears in N posts under #kw").
- **Mirror**: `discovery.py:23-62` (`_ig_actor`, `_owner`, dedupe + `seen`, `[]` guards). Reuse `_ig_actor`/`_owner` (import or factor a shared helper — do not duplicate).
- **Validate**: `pytest tests/growth/test_competitors.py -k discover -q`

### Task 4: Config tunables + package export
- **Action**: Add `growth_competitor_limit: 10` and `growth_keyword_limit: 8` to `DEFAULT_CONFIG` in `config.py`. Export `discover_competitors`, `extract_keywords` from `growth/__init__.py`.
- **Mirror**: existing `DEFAULT_CONFIG` entries (`growth_search_limit`, `growth_min_score`) and current `__init__.py` exports.
- **Validate**: `python -c "from social_trend_analyzer.growth import discover_competitors, extract_keywords; print('ok')"`

### Task 5: Tests
- **Action**: Create `tests/growth/test_competitors.py`: (a) `extract_keywords` returns deterministic tokens with OpenAI disabled; (b) `discover_competitors` dedupes authors and ranks by frequency given a monkeypatched `run_actor_sync` returning synthetic posts; (c) empty description and no-token paths return `[]`.
- **Mirror**: `tests/growth/test_discovery.py` / `test_recommend.py` monkeypatch + AAA style.
- **Validate**: `pytest tests/growth/test_competitors.py -q`

## Validation
```bash
# unit tests for the new module
pytest tests/growth/test_competitors.py -q
# full growth suite still green
pytest tests/growth -q
# import smoke
python -c "from social_trend_analyzer.growth import discover_competitors, extract_keywords; print('ok')"
```

## Risks
| Risk | Likelihood | Mitigation |
|---|---|---|
| LLM-derived keywords are off-target → wrong competitors | Medium | Ground in live hashtag results; rank by reach; deterministic fallback keeps it running offline; allow operator override later |
| Apify actor may not return follower counts → "reach" ranking weak | Medium | Rank by occurrence frequency + post engagement as proxy; treat follower-threshold verification as a PRD open question (deferred) |
| Scope creep into harvesting/scheduling (later milestones) | Medium | This milestone stops at returning `CompetitorList`; no follow/unfollow, no graph/API wiring here |
| Duplicated Apify helpers between `discovery.py` and `competitors.py` | Low | Reuse `_ig_actor`/`_owner` rather than re-implementing |

## Open Questions Carried From PRD (affect this milestone)
- How is "competitor" confirmed — minimum followers/engagement threshold, or just top-N by frequency for MVP?
- How many competitors per campaign (`growth_competitor_limit` default 10 — confirm)?

## Acceptance
- [ ] All tasks complete
- [ ] `pytest tests/growth -q` passes (new + existing)
- [ ] `discover_competitors("<description>")` returns a ranked `CompetitorList` with OpenAI on, and a non-crashing fallback with OpenAI off
- [ ] Patterns mirrored (recommend.py chain, discovery.py Apify/dedup), not reinvented
- [ ] No follow/unfollow or scheduling logic introduced in this milestone
```
