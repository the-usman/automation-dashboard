# Social Media Reputation Checker

## Problem
A social-media/marketing agency onboards new clients whose existing online presence is
an unknown quantity. Today, judging a client's reputation, strengths, and shortcomings is
manual, subjective, and slow — and there's no consistent baseline to prove the agency
actually improved things later. The agency needs a fast, repeatable way to score a
client's public social presence at onboarding and re-score it afterward to demonstrate
progress.

## Evidence
- Clients/users have explicitly requested this capability (per product owner).
- Assumption to validate: that an automated score meaningfully matches the agency's own
  expert judgment of a brand's reputation — needs validation via spot-check against
  human-rated accounts.

## Users
- **Primary**: An agency operator (account manager / strategist) onboarding a new client.
  Trigger: a new client signs on → run a check to get a baseline score + strong/weak
  points before starting work. Later trigger: re-run to measure improvement.
- **Not for**: End consumers, recruiters/HR screening, or public-facing self-service.
  Not a monitoring/alerting product. Single account per platform only.

## Hypothesis
We believe an **on-demand reputation score with per-platform breakdown and
strong/weak-point summary** will let an **agency baseline and track a client's social
presence** for **agency operators onboarding clients**.
We'll know we're right when **the generated score and strong/weak points agree with an
agency expert's own assessment on a sample of real accounts**, and re-running after work
reflects real improvement.

## Success Metrics
| Metric | Target | How measured |
|---|---|---|
| Score/insight accuracy | Agency agrees with score & strong/weak points | Spot-check N real accounts vs. human expert rating (TBD — set N) |
| Graceful degradation | 1 platform failing never fails the whole request | Test with a bad/nonexistent username per platform |
| Valid structured output | 100% responses are schema-valid JSON | OpenAI structured output / schema validation on every response |
| Progress signal | Re-check after work shows a directionally correct score change | Before/after comparison on a client (manual until persistence exists) |

## Scope
**MVP** — `POST /check-reputation`: accept up to three optional usernames
(instagram, x, tiktok); for each provided username, scrape the profile via the matching
Apify actor and normalize posts to `{text, post_url, date, likes, comments}`; send all
collected items in one batch to OpenAI (structured/JSON output) scored on a
**general sentiment/quality** lens; return `overall_score`, per-platform scores
(null where skipped/failed), `strong_points`, and `weak_points`. Reject only when all
three usernames are null. Each platform scrape is isolated — a failure marks that
platform null and the request continues.

**Out of scope**
- Database / persistence — deferred; before/after comparison is manual for now.
- Scheduling, monitoring, or recurring re-checks — on-demand only.
- Authentication / multi-tenant accounts — deferred.
- Multiple accounts per platform, or platforms beyond IG/X/TikTok — deferred.
- Frontend / dashboard — backend returns JSON only.

## Delivery Milestones
<!-- Business outcomes, not engineering tasks. /plan turns each into a plan. -->
<!-- Status: pending | in-progress | complete -->

| # | Milestone | Outcome | Status | Plan |
|---|---|---|---|---|
| 1 | Single-platform scoring | Operator can POST one username and get a valid reputation JSON back | complete | `.claude/plans/social-media-reputation-checker.plan.md` |
| 2 | Multi-platform + resilient scrape | Any combination of the 3 platforms works; one platform failing marks it null, others still score | complete | `.claude/plans/social-media-reputation-checker.plan.md` |
| 3 | Scoring quality pass | Score + strong/weak points align with agency judgment on sample accounts | pending | — |

## Open Questions
- [ ] What defines "reputation quality" concretely for the OpenAI prompt (tone? consistency? controversy? engagement quality?) — needed so scores are defensible and repeatable.
- [ ] How many posts per platform should be scraped/scored, and how recent? (affects Apify cost and score stability)
- [ ] Which specific Apify actors for each platform, and what input limits/cost ceiling per check?
- [ ] Should per-platform scores be independent, or weighted into `overall_score` by post volume / follower reach?
- [ ] What's the acceptable latency for a check (Apify scrapes can be slow), and is async/polling needed or is a single blocking request fine for MVP?
- [ ] Sample size N and pass bar for the accuracy spot-check.

## Risks
| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Score doesn't match expert judgment (core hypothesis fails) | Medium | High | Define an explicit scoring rubric in the prompt; validate against human ratings before relying on it |
| Apify actor blocked / username not found / rate-limited | High | Medium | Per-platform isolation already required — mark null, continue; surface a per-platform status |
| Apify scrape latency makes a blocking request time out | Medium | Medium | Cap posts scraped; decide sync-vs-async in planning (Open Question) |
| OpenAI returns malformed/inconsistent JSON | Low | Medium | Use structured outputs / json_object mode + schema validation |
| Apify / OpenAI cost per check unbounded | Medium | Medium | Set post-count and actor input caps; log cost per check |
| Scoring the same account twice gives very different scores | Medium | Medium | Fix post window + prompt; test determinism as part of quality pass |

---
*Status: DRAFT — requirements only. Implementation planning pending via /plan.*
