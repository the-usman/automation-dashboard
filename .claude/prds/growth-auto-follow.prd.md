# Description-Driven Instagram Auto-Follow Growth

## Problem
Today the growth feature requires the operator to supply a competitor's profile/post
link or keywords, then manually review candidates before following. The operator
wants to do none of that research: they want to provide only a **product description**,
and have the system find competitors, harvest a relevant audience, and run safe,
human-like follow/unfollow campaigns automatically. Leaving this manual costs operator
time and produces inconsistent targeting and pacing (raising account-ban risk).

## Evidence
- Assumption — needs validation via operator interview / usage analytics.
  The change is operator-requested but no quantified pain (time spent, ban incidents,
  current follow-back rate) has been recorded yet.

## Users
- **Primary**: The account operator / growth marketer running their own brand's
  Instagram, who knows their product but does not want to research competitors or
  babysit follow batches.
- **Not for**: Agencies needing per-client manual curation/approval workflows; users
  wanting to follow a hand-picked list (the existing link/keyword path already covers
  explicit targeting).

## Hypothesis
We believe **turning a product description into an automatically-discovered competitor
audience, followed on a randomized human-like schedule** will **remove manual research
and reduce action-block/ban risk** for **the solo account operator**.
We'll know we're right when **a campaign launched from a description alone achieves a
follow-back rate at or above the operator's current manual baseline, with zero action
blocks over the campaign window**.

## Success Metrics
| Metric | Target | How measured |
|---|---|---|
| Follow-back rate (primary) | TBD — set vs. manual baseline | Re-scrape own followers N days after each follow; (followed ∩ now-following-back) ÷ followed |
| Action blocks / bans per campaign | 0 | Login + action error monitoring during the run |
| Manual steps from description → running campaign | 0 | Operator provides description + budget only |
| Daily follow cap adherence | 100% within cap (≤ ~30/day, configurable) | Audit of executed actions per day |

## Scope

**MVP** — From a **product description** + a **budget** (total follows and number of
days, e.g. 200 over 2 days), the system:
1. Derives keywords from the description and finds competitor accounts via
   keyword→hashtag search.
2. Harvests candidate users from competitors' **recent-post commenters** and
   **followers**, dedupes, and ranks them for relevance.
3. Plans a **randomized human-like schedule**: per-day quota that varies day to day
   and stays under a daily cap (~30), randomized daily start time, and irregular
   intra-day pacing (e.g. ~10 actions per ~25 min with jitter).
4. Executes follows automatically within that envelope, from the stored IG credential,
   respecting the existing rate-limit/cap safeguards.
5. Tracks per-campaign progress and follow-back outcome.

**Out of scope**
- Manual approval-before-follow step — operator chose fully automatic. (Existing
  link/keyword + recommend endpoint remains for explicit targeting.)
- "Following" list as a candidate source — deferred; MVP uses commenters + followers only.
- DMs, likes, comments, story views, or any engagement beyond follow/unfollow.
- Multi-account concurrent campaigns and cross-account scheduling — single account per campaign for MVP.
- Paid/official Instagram Graph API integration — out of scope.

## Delivery Milestones
<!-- Business outcomes, not engineering tasks. /plan turns each into a plan. -->
<!-- Status: pending | in-progress | complete -->

| # | Milestone | Outcome | Status | Plan |
|---|---|---|---|---|
| 1 | Description → competitors | Operator submits only a description; system returns a verified competitor set | in-progress | `.claude/plans/growth-auto-follow.plan.md` |
| 2 | Audience harvesting + ranking | System produces a deduped, relevance-ranked candidate pool from competitors' commenters + followers | pending | — |
| 3 | Human-like scheduler | A budget (follows × days) becomes a randomized, capped, jittered per-day/per-action plan | pending | — |
| 4 | Automatic safe execution | Follows run unattended within the schedule and safety envelope; campaign can be monitored and stopped | pending | — |
| 5 | Follow-back tracking | Operator can see follow-back rate per campaign | pending | — |

## Open Questions
- [ ] Unfollow policy: does this campaign also auto-unfollow non-followers-back? After how many days, and under what daily cap?
- [ ] Follow-back measurement window: how many days after a follow do we check for a follow-back?
- [ ] How many competitors per campaign, and how is "competitor" confirmed (min followers/engagement threshold)?
- [ ] Cross-campaign dedup: never re-follow someone already followed/unfollowed before? Persist a per-account history?
- [ ] Daily cap default and ceiling — is ~30/day the hard maximum, and should it scale with account age/warmup?
- [ ] Budget feasibility: if requested total ÷ days exceeds the safe daily cap, does the system reject, extend the timeline, or cap-and-warn?
- [ ] What defines the active daily window for randomized start times (e.g. only 9am–9pm account-local)?
- [ ] Follower harvesting at scale: depth/limit per competitor and scraper cost/quota implications.

## Risks
| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Instagram action-block or ban from automated following | High | High | Randomized human-like pacing, conservative daily caps, jittered windows, single account; monitor action errors and halt on block |
| Poor targeting → low follow-back, wasted quota | Medium | Medium | Prefer high-intent commenters, relevance ranking, follow-back tracking to tune |
| Competitor discovery from description is inaccurate | Medium | Medium | Keyword→hashtag grounding in live data; (open) verify by reach threshold; allow operator override |
| Scraper cost/rate limits when harvesting followers | Medium | Medium | Cap harvest depth per competitor; cache/dedup; reuse existing Apify integration |
| Budget exceeds safe daily cap | Medium | Medium | Decide reject vs. auto-extend timeline (open question) and surface clearly |

---
*Status: DRAFT — requirements only. Implementation planning pending via /plan.*
