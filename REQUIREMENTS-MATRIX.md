# Requirements and evidence matrix

Every required tier category has a verified implementation and acceptance evidence below. Status is DONE for all rows as of M11 (29 September 2026).

| ID | Requirement | Milestone | Design location | Acceptance target | Status |
|---|---|---|---|---|---|
| T1-01 | Authentication and sessions | M01 | User/session; architecture auth | Real login/logout, rotation and unauthorised calls | **DONE** — `test_auth_and_permissions.py` (9 tests) |
| T1-02 | Visitor, participant, judge, organiser, admin roles | M01/M03 | EventMembership; explicit read/write policy | Role and cross-event matrix; privileged overrides audited | **DONE** — `test_auth_and_permissions.py`; `test_judging_workflow.py` |
| T1-03 | Events with dates, tracks and prizes | M02 | Event/Track/Prize | Create/edit validation, safe Markdown/local assets, chronology | **DONE** — `test_participant_workflow.py` |
| T1-04 | Team formation by invite link | M02 | Team/TeamMember/TeamInvitation | Expiry, single-use redemption, capacity/concurrent joins | **DONE** — `test_participant_workflow.py` |
| T1-05 | Submission drafts and edits | M02 | Project/ProjectRevision/RevisionAsset | Autosave/version conflicts, official versus draft distinction | **DONE** — `test_participant_workflow.py` |
| T1-06 | Enforced submission deadline | M02 | Transactional mutation contract | Valid-CSRF before/equal/after, stale form and lock-wait tests | **DONE** — `test_participant_workflow.py` |
| T1-07 | Public searchable/filterable gallery | M01/M02 | Submitted-revision public selector | Fixture HTML, pagination, search/filter, draft exclusion | **DONE** — `test_fixture_importer.py`; acceptance checker PASS |
| T2-01 | Judge invitations and assignments | M03 | EventInvitation/JudgeAssignment/TrackPermission | Gate, conflicts, allowed track and balanced allocation | **DONE** — `test_judging_workflow.py` |
| T2-02 | Configurable weighted rubric | M03 | Rubric/Criterion/ReviewScore | Freeze, exact criterion set, DB score bounds | **DONE** — `test_judging_workflow.py` |
| T2-03 | Backend judge isolation | M03 | Scoped selectors and domain services | Peer score/detail/revision/export and cross-track denial | **DONE** — `test_judging_workflow.py`; acceptance checker T2 PASS |
| T2-04 | Live organiser progress | M03 | Assignments/review states; bounded polling | Real incomplete states; unknown import coverage labelled | **DONE** — `test_judging_workflow.py` |
| T2-05 | Documented cross-judge normalisation | M04 | ResultRun; deterministic offset model | Known answers, independent solver, fixtures and limitations | **DONE** — `test_results_workflow.py`; `JUDGING.md` |
| T2-06 | CSV export | M04/M10 | Organiser projections; CSV escaping | Permissions, real values, provisional status and safe text | **DONE** — `test_results_workflow.py`; acceptance checker csv PASS |
| T3-01 | Community voting and gates | M05 | VotingPolicy/VoterIdentity/Vote | Access modes, duplicate/state/window enforcement | **DONE** — `test_voting_workflow.py`; `T3-ACCEPTANCE.md` |
| T3-02 | Project comments | M06 | Comment/ModerationCase | Author/moderator permissions and script-safe rendering | **DONE** — `test_community_workflow.py`; `T3-ACCEPTANCE.md` |
| T3-03 | Results hidden during voting | M06 | ResultRun/CommunityResultRow/Publication | No early disclosure through any public path | **DONE** — `test_community_workflow.py`; `test_results_workflow.py` |
| T3-04 | Randomised ballot ordering | M05 | BallotSession | Stable per-identity shuffled pagination, eligibility updates | **DONE** — `test_voting_workflow.py`; `T3-ACCEPTANCE.md` |
| T3-05 | Rate limits, duplicates and audit | M05/M06 | RateLimitBucket/AbuseSignal/AuditEvent | Atomic limits, concurrent vote uniqueness, reviewed decisions | **DONE** — `test_voting_workflow.py`; `test_community_workflow.py` |
| T4-01 | REST API | M07 | ApiCredential; OpenAPI; action inventory | Every UI business action and scoped-key contract | **DONE** — `test_api_contracts.py` (12 tests); `API-COVERAGE.md` |
| T4-02 | Webhooks | M08 | DomainEvent/WebhookDelivery/Attempt | Atomicity, retries/replay, crash recovery, SSRF protection | **DONE** — `test_webhooks_and_jobs.py` (8 tests); `WEBHOOK-EVENTS.md` |
| T4-03 | Certificates and records | M09 | Template/IssuedCredential/AwardDecision | Eligibility, PDF/JSON hash integrity, tampering, trusted-key distinction, rotation/revocation and consent tests | **DONE** — `test_credentials.py` (25 tests) |
| T4-04 | Signed publicly verifiable judge records | M09 | SigningKey/StatusEvent/Consent | Exact bytes, tampering, trusted keys, rotation/revocation | **DONE** — `test_credentials.py` (25 tests); `T4-ACCEPTANCE.md` |
| T4-05 | Embeddable gallery | M10 | EmbedConfiguration/public selector | Different-origin frame, responsive use, public data only | **DONE** — `test_m10_embed_and_portable_archive.py` (10 tests) |
| T4-06 | Bulk import/export | M10 | BackgroundJob/PortableImportPlan | Dry-run, safe archive validation, idempotent domain/media round-trip | **DONE** — `test_m10_embed_and_portable_archive.py` (10 tests) |

## Cross-cutting requirements

| Requirement | Plan | Evidence |
|---|---|---|
| Docker Compose, local/offline operation | Bundled images/assets, DB/media volumes; optional integrations cannot block core | `docker-compose.yml`; `Dockerfile.app`; startup documented in `README.md` |
| Original shared fixture | Immutable source plus provenance and idempotent historical importer | `test_fixture_importer.py` — exact counts, source hash, duplicate and close-date preservation |
| Open source and public repository | OSI licence; user-authorised publication workflow | `LICENSE` (MIT) |
| New project code within event window | Preserve honest history/time provenance | Git repository history |
| Honest claims and acceptance report | Original checker plus independent T3/T4 evidence | `acceptance-report.txt`; `T3-ACCEPTANCE.md`; `T4-ACCEPTANCE.md` |
| Documentation and demo | Milestone deliverables and user-owned video | `README.md`; `ARCHITECTURE.md`; `DATA-MODEL.md`; `JUDGING.md`; `SECURITY.md`; `OPERATIONS.md`; `API-COVERAGE.md`; `WEBHOOK-EVENTS.md` |
| Fast, accessible, polished UX | Shared tokens, scoped queries, performance targets and responsive review | Template-rendered pages with semantic HTML; keyboard-accessible forms; responsive layouts |

## Test suite summary (M11)

```
96 passed in ~200s
```

Modules:
- `test_fixture_importer.py` — fixture import, counts, provenance
- `test_auth_and_permissions.py` — sessions, roles, cross-event isolation
- `test_participant_workflow.py` — team formation, drafts, deadline enforcement
- `test_judging_workflow.py` — rubrics, assignments, isolation, coverage
- `test_results_workflow.py` — normalisation, CSV, publication, confidentiality
- `test_voting_workflow.py` — community voting, gates, ballots, rate limits
- `test_community_workflow.py` — comments, moderation, abuse signals, snapshots
- `test_api_contracts.py` — REST API, scoped keys, idempotency
- `test_webhooks_and_jobs.py` — outbox, delivery, retry, crash recovery, SSRF
- `test_credentials.py` — certificates, Ed25519 signing, revocation, verification
- `test_m10_embed_and_portable_archive.py` — embed gallery, portable import/export

The optional bonuses (normalisation proof, threat model, API-first evidence, pairwise judging) are not claimed. The checker output is honest and unmodified.
