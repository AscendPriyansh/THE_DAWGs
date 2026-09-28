# Consolidation decisions

This file records resolved differences; it is not an alternative schema. DATA-MODEL.md and ARCHITECTURE.md contain the resulting rules.

| Earlier difference | Final decision |
|---|---|
| T3/T4 described as deferred extensions | All four tiers are in scope; build in the single staged roadmap |
| Judge track tags treated only as expertise | Keep expertise tags and add separate explicit JudgeTrackPermission from the core judging milestone |
| Fixture reviews may cross declared track tags | Preserve every source score, quarantine mismatched assignments, report and explicitly reconcile; no silent access expansion |
| Vote originally pointed directly to User | Final Vote points to VoterIdentity; new builds use that schema immediately at its milestone |
| Authenticated voting only versus configurable gates | Default authenticated; add invite-link and configured email-verified modes with explicit offline/identity limits |
| No worker in initial Compose versus later asynchronous work | Foundation stays small; add a PostgreSQL-backed worker at M08 using the same backend image |
| Prize text only versus award certificates | Prize descriptions exist in core; AwardDecision and credentials arrive at M09 |
| Portable exports deferred | Full validated archive round-trip is required at M10; normal DB/media backup remains a separate operational capability |
| Two different execution orders | EXECUTION-PLAN.md is the only order; M01–M11 |
| Warm/lime visual proposal versus user references | Neutral Vercel-inspired workspaces and expressive DOGFOOD-inspired event pages |
| Percentage estimates presented too precisely | Use explicit requirement/evidence mapping; approximately 96% is historical planning shorthand only |

## Evidence limits retained deliberately

- Fixtures contain 41 projects, 40 teams, 30 judges, 8 tracks and 126 scores, but no explicit assignment batches, criterion weights or authoritative normalised ranking.
- Imports label inferred dates/weights/captains and preserve originals. Unknown assignment coverage is never displayed as 100%.
- The supplied checker makes six requests for seven assertions, checks only T1/T2 and returns zero even after failed assertions.
- A CSRF rejection can satisfy its late-submission probe. Independent valid-CSRF deadline tests are required.
- Offline startup assumes Docker and the packaged images are already available; image transfer/loading is explicit preparation.
- Authentication, link possession and email verification each have limits as proof of one human per voter.
- A signed record proves integrity relative to a key, not organiser identity without a trusted key; offline revocation freshness is limited.
- Webhooks are at least once. Duplicate delivery and recovery are designed for rather than denied.
- Normalisation remains a model with assumptions, requiring independent numerical evidence and clear comparability warnings.

No deferred pairwise bonus is necessary to complete T4. Bonus completion must have separate evidence.
