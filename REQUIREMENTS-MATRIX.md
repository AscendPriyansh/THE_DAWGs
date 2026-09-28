# Requirements and evidence matrix

Every required tier category has a planned implementation and acceptance target below. Status is PLANNED for all rows until code/evidence is reviewed. This is not a claim of 100% working functionality or a mathematical measurement of the earlier approximately 96% planning estimate.

| ID | Requirement | Milestone | Design location | Acceptance target | Status |
|---|---|---|---|---|---|
| T1-01 | Authentication and sessions | M01 | User/session; architecture auth | Real login/logout, rotation and unauthorised calls | PLANNED |
| T1-02 | Visitor, participant, judge, organiser, admin roles | M01/M03 | EventMembership; explicit read/write policy | Role and cross-event matrix; privileged overrides audited | PLANNED |
| T1-03 | Events with dates, tracks and prizes | M02 | Event/Track/Prize | Create/edit validation, safe Markdown/local assets, chronology | PLANNED |
| T1-04 | Team formation by invite link | M02 | Team/TeamMember/TeamInvitation | Expiry, single-use redemption, capacity/concurrent joins | PLANNED |
| T1-05 | Submission drafts and edits | M02 | Project/ProjectRevision/RevisionAsset | Autosave/version conflicts, official versus draft distinction | PLANNED |
| T1-06 | Enforced submission deadline | M02 | Transactional mutation contract | Valid-CSRF before/equal/after, stale form and lock-wait tests | PLANNED |
| T1-07 | Public searchable/filterable gallery | M01/M02 | Submitted-revision public selector | Fixture HTML, pagination, search/filter, draft exclusion | PLANNED |
| T2-01 | Judge invitations and assignments | M03 | EventInvitation/JudgeAssignment/TrackPermission | Gate, conflicts, allowed track and balanced allocation | PLANNED |
| T2-02 | Configurable weighted rubric | M03 | Rubric/Criterion/ReviewScore | Freeze, exact criterion set, DB score bounds | PLANNED |
| T2-03 | Backend judge isolation | M03 | Scoped selectors and domain services | Peer score/detail/revision/export and cross-track denial | PLANNED |
| T2-04 | Live organiser progress | M03 | Assignments/review states; bounded polling | Real incomplete states; unknown import coverage labelled | PLANNED |
| T2-05 | Documented cross-judge normalisation | M04 | ResultRun; deterministic offset model | Known answers, independent solver, fixtures and limitations | PLANNED |
| T2-06 | CSV export | M04/M10 | Organiser projections; CSV escaping | Permissions, real values, provisional status and safe text | PLANNED |
| T3-01 | Community voting and gates | M05 | VotingPolicy/VoterIdentity/Vote | Access modes, duplicate/state/window enforcement | PLANNED |
| T3-02 | Project comments | M06 | Comment/ModerationCase | Author/moderator permissions and script-safe rendering | PLANNED |
| T3-03 | Results hidden during voting | M06 | ResultRun/CommunityResultRow/Publication | No early disclosure through any public path | PLANNED |
| T3-04 | Randomised ballot ordering | M05 | BallotSession | Stable per-identity shuffled pagination, eligibility updates | PLANNED |
| T3-05 | Rate limits, duplicates and audit | M05/M06 | RateLimitBucket/AbuseSignal/AuditEvent | Atomic limits, concurrent vote uniqueness, reviewed decisions | PLANNED |
| T4-01 | REST API | M07 | ApiCredential; OpenAPI; action inventory | Every UI business action and scoped-key contract | PLANNED |
| T4-02 | Webhooks | M08 | DomainEvent/WebhookDelivery/Attempt | Atomicity, retries/replay, crash recovery, SSRF protection | PLANNED |
| T4-03 | Certificates and records | M09 | Template/IssuedCredential/AwardDecision | Eligibility, local PDF generation and permissioned download | PLANNED |
| T4-04 | Signed publicly verifiable judge records | M09 | SigningKey/StatusEvent/Consent | Exact bytes, tampering, trusted keys, rotation/revocation | PLANNED |
| T4-05 | Embeddable gallery | M10 | EmbedConfiguration/public selector | Different-origin frame, responsive use, public data only | PLANNED |
| T4-06 | Bulk import/export | M10 | BackgroundJob/PortableImportPlan | Dry-run, safe archive validation, idempotent domain/media round-trip | PLANNED |

## Cross-cutting requirements

| Requirement | Plan | Evidence |
|---|---|---|
| Docker Compose, local/offline operation | Bundled images/assets, DB/media volumes; optional integrations cannot block core | Actual offline boot after explicit image preparation |
| Original shared fixture | Immutable source plus provenance and idempotent historical importer | Exact counts, source hash, duplicate and close-date preservation |
| Open source and public repository | OSI licence; user-authorised publication workflow | Licence and eventual accessible repository |
| New project code within event window | Preserve honest history/time provenance | Actual repository history; do not claim later changes were submitted earlier |
| Honest claims and acceptance report | Original checker plus independent T3/T4 evidence | Real outputs and feature inventory |
| Documentation and demo | Milestone deliverables and user-owned video | Tested docs and actual full lifecycle |
| Fast, accessible, polished UX | Shared tokens, scoped queries, performance targets and responsive review | Measured latency, browser checks and visual review |

The optional bonuses are normalisation proof, threat model, API-first evidence and pairwise judging. Pairwise remains a separately scoped bonus and is not a missing T4 requirement.
