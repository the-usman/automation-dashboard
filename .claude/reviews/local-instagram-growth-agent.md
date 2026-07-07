# Code Review: Instagram Growth Agent (local, uncommitted)

**Reviewed**: 2026-06-28
**Scope**: `social_trend_analyzer/growth/**`, `tests/growth/**`, edits to `config.py`,
`collectors/apify.py`, `requirements.txt`, `.env.example`, `pyproject.toml`
**Decision**: APPROVE with comments

## Summary
A new LangGraph subpackage adding Instagram follow/unfollow + LLM-ranked user
recommendations. Well-aligned with existing project conventions, strong credential
security, fully guarded/offline-testable. No CRITICAL/HIGH issues; a few MEDIUM/LOW.

## Findings

### CRITICAL
None.

### HIGH
None.

### MEDIUM
- **Path traversal via username in session filename** — `growth/instagram.py` `_session_path()`
  interpolates `cred.username` (from Mongo) into a file path. Sanitize to `[A-Za-z0-9._-]`.
- **`init_run` can raise on non-numeric trigger input** — `growth/graph.py` `int(n_posts)` /
  `float(min_score)` are uncaught inside the node. Validate/guard the trigger at the boundary.
- **`cli.py` lacks tests** — `_trigger_from_args` mapping (recommend-product / recommend-competitor /
  act) is untested. Add a unit test.

### LOW
- Magic numbers: fallback score `50`, candidate cap `100`, context truncation `200`. Consider constants.
- Real-LLM `recommend` path untested (only offline fallback). Acceptable without an API key.
- Out-of-scope: `project_email_outreach` memory holds a live-looking Mongo URI w/ password — rotate & move to env.

## Validation Results

| Check | Result |
|---|---|
| Lint (ruff) | Pass |
| Compile (py_compile) | Pass |
| Tests (pytest, 27) | Pass |
| Build | N/A (library) |

## Files Reviewed
- Added: `growth/{__init__,schemas,state,credentials,instagram,discovery,recommend,graph,cli}.py`
- Added: `tests/growth/test_{credentials,discovery,instagram,recommend,graph}.py`, `tests/growth/__init__.py`
- Modified: `config.py`, `collectors/apify.py`, `requirements.txt`, `.env.example`, `pyproject.toml`
