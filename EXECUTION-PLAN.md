# Unified execution plan — T1 through T4

This is the sole milestone roadmap. Read the final architecture/schema once before starting, then load the sections relevant to the current milestone. Implement one complete, reviewable slice at a time. Do not build superseded V1/V2 schemas or consult the earlier plans as competing instructions.

## Milestones

| ID | Scope | Required exit evidence |
|---|---|---|
| M01 | Compose/dev foundation, custom User, real sessions, event roles, faithful fixture import, event overview and gallery/detail | Actual startup; original counts and duplicate retained; search/filter; initial HTML fixture title; login/logout and role denial; repeatable seed/restart |
| M02 | Event editing/Markdown/visual assets, dates/tracks/prizes, teams/invites, versioned submissions and uploads, participant next actions | Full participant workflow; valid-CSRF boundary/lock-wait tests; stale-save conflict; submitted snapshot and roster preserved |
| M03 | Frozen weighted rubric, explicit track grants, invitation/assignment/conflicts, private judging and organiser progress | Own/peer/cross-track/cross-event read/write tests; partial/complete review distinction; real coverage dashboard |
| M04 | Scoring, independent normalisation checks, result preview/publication, CSV, core UX and adoption baseline | Reproducible result inputs; constant/sparse/disconnected cases; publication and no-leak tests; real T1/T2 checker; offline/restart/restore baseline |
| M05 | Voting policies, identities/gates, stable random ballots and support/withdraw | Three documented gate behaviours; unique/concurrent votes; exact cutoff; stable pagination; unavailable SMTP handled honestly |
| M06 | Comments, moderation, rate limits, abuse signals and community result snapshots | No unreleased totals; reasoned audit decisions; final result snapshot; T3 acceptance evidence |
| M07 | Complete event REST API, scoped keys, local OpenAPI docs and idempotency contracts | UI business-action inventory; role/scope intersection; revocation and API contract tests |
| M08 | Shared persistent worker/jobs, transactional outbox and webhook delivery | Rollback, retries, worker crash, duplicate/replay, queue leases, SSRF and secret handling tests |
| M09 | Templates, award decisions, certificates, signed judge records and verification | Eligibility, PDF/JSON hash integrity, tampering, trusted-key distinction, rotation/revocation and consent tests |
| M10 | Public gallery embed and validated bulk import/export | Different-origin iframe; no private data; archive dry-run/apply; malicious input rejection; full domain/media round-trip |
| M11 | Full integration, visual/accessibility review, release docs and offline packaging | All tier matrix rows evidenced; original checker report; independent T3/T4 reports; full restart/migration/restore and browser lifecycle |

The architecture sections labelled T3/T4 describe eventual behaviour; their presence does not authorise building ahead of the current milestone. All original event timing requirements still apply to a competition submission. Later releases must be identified as later releases.

## M01 assignment in detail

Inspect existing code/tools before editing. Establish the custom User model before its first migration. Add only core entities needed for the fixture import and foundation; imported scores need real relationships even though the scoring UI is not yet built. Preserve all original IDs through provenance, all 41 projects and 126 reviews, the duplicate disposition and the closed event timestamp.

Seed declared track grants and quarantine out-of-scope historical assignments rather than broadening access silently. Record inferred defaults and unknown assignment coverage. The foundation must retain imported evidence for later review without claiming the full judging workflow is operational.

Implement PostgreSQL-backed event overview/gallery/detail, real sessions, public/private projections and event-scoped role denial. Show actual fixture titles in initial HTML at the checker gallery route. Start a coherent neutral visual system now. Unimplemented actions must be absent or visibly unavailable; no fake success responses or localStorage-only business data.

Provide the real local startup URL and demo login instructions. Check seed repeatability and restart persistence. Record missing environment prerequisites precisely. Do not fabricate a full acceptance PASS: T2 will not yet be complete.

## How every milestone is completed

1. Inspect working code and IMPLEMENTATION-STATUS.md; identify the next incomplete authorised milestone.
2. State a short implementation outline and concrete contradictions, if any. Make routine choices within the canonical contract.
3. Implement migration/service/API/UI together where applicable, with named constraints and explicit permissions.
4. Run focused meaningful checks, then required integration/browser checks for the affected behaviour.
5. Record exact commands and observed outcomes, including failures/untested items. Update the canonical docs for intentional deviations.
6. Stop at the review boundary; do not continue adding tiers while important failures remain unresolved.

## Evidence standards

- Core real-browser lifecycle: create event, form team, draft/save, submit, judge, inspect coverage, publish.
- PostgreSQL integration tests cover transactions and locks; SQLite is not a substitute for those checks.
- Fixture presence is not a correctness oracle. Known-answer mathematical cases and an independent solver check validate scoring.
- Public confidentiality checks include HTML, lists, details, exports, preload data, embeds, webhook payloads and errors.
- All asynchronous work has restart/retry/idempotency tests and does not expose stale permission results.
- Offline claims require actual network-disabled startup with local images; backup claims require actual restoration including media/keys.
- Visual review covers desktop/mobile, keyboard focus, errors, empty states and saved-state feedback, not only the hero page.

## Release documentation and deliverables

| Deliverable | Owner milestone |
|---|---|
| README with tested startup, limits and migration/backup procedure | Start M01, finalise M11 |
| Canonical ARCHITECTURE.md and DATA-MODEL.md updated to implemented state | Every milestone |
| JUDGING.md with defended maths, assignment rules and evidence | M03/M04 |
| LICENSE using an OSI-approved licence | M01; default MIT unless user chooses otherwise |
| .dogfood.toml with valid isolated demo credentials and honest claims | M04, review M11 |
| acceptance-report.txt from original checker | M04 and M11 |
| T3-ACCEPTANCE.md and T4-ACCEPTANCE.md | M06 and M11 |
| API-COVERAGE.md and WEBHOOK-EVENTS.md | M07/M08 |
| SECURITY.md and OPERATIONS.md | Incremental, finalise M11 |
| OpenAPI schema and local interactive docs | M07 |
| docker-compose.yml, pinned images/dependencies and offline package instructions | Incremental, finalise M11 |
| Meaningful application test suite | Every milestone |
| Five-minute demo video | User; support with a working lifecycle |

T4 requires all its listed capabilities, not merely a REST API. The supplied checker does not verify T3/T4, so preserve its honest output and link independent evidence. Claim only fully completed tiers. Bonuses are separately evidenced and never add to the published weighted score.

## Resuming work

In a new session, read the handoff, implementation status and the current milestone's design sections, then inspect code before continuing. Never regenerate the repository from scratch just because a context window was reset. If existing work implements earlier schemas, plan non-destructive migrations and use the final definitions as the destination.
