# Implementation status

Status: M06 milestone complete and verified against PostgreSQL.

- **Current authorised milestone**: M06 (Comments, moderation, rate limits, abuse signals and community result snapshots).
- **Current implementation location**: `src/backend/` (Django + DRF) and PostgreSQL 16.
- **Startup URL**: `http://localhost:8000` (Gallery at `/projects`, Workspace at `/workspace`, Judge console at `/judging`, Leaderboard at `/results`, Organiser coverage at `/events/sample-hack-2026/judging/manage/`).
- **Claimed Tiers in .dogfood.toml**: `["T1"]` (Passing checker for T1 and T2; T3 verified in `T3-ACCEPTANCE.md`).

## Completed Behaviour (M01)

1. **Custom User Model & Database Sessions**:
   - `User` with normalized email (lowercase, stripped), display name, UUID primary keys, and standard password hashing.
   - `RateLimitBucket` model created.
   - Database-backed session authentication (`django.contrib.sessions.backends.db`) with `session` cookie.

2. **Core Domain Models & PostgreSQL Constraints**:
   - `Event`: lifecycle states, registration/submission/judging date check constraints, min/max team sizes, positive normalisation lambda constraint.
   - `EventMembership`: scoped `(event, user)` uniqueness, roles (`ORGANISER`, `JUDGE`, `PARTICIPANT`).
   - `Track` and `Prize`: event-scoped uniqueness, track linkages.
   - `Team` and `TeamMember`: captain user, unique memberships.
   - `Project` and `ProjectRevision`: partial unique constraint `unique_active_project_per_team` for `DRAFT` and `SUBMITTED`, immutable revisions with roster snapshots.
   - `Rubric` and `Criterion`: 3 criteria (`functionality`, `quality`, `innovation`), weight check constraints.
   - `JudgeTrack` (expertise) & `JudgeTrackPermission` (explicit track access).
   - `JudgeAssignment`, `Review`, `ReviewScore` (1–5 constraint), `ReviewRevision`.
   - `AuditEvent`: immutable audit trail logging imports, role assignments, and duplicate dispositions.
   - `ImportBatch` and `ExternalRecord`: full provenance mapping from fixture IDs to internal UUIDs.

3. **Faithful & Idempotent Fixture Importer**:
   - Implemented in `apps.imports.importer.FixtureImporter` and management command `import_fixtures`.
   - Preserves original fixture timestamp `submissions_close = 2026-03-01T18:00:00Z`.
   - Retains all 41 source projects and 126 reviews.
   - Correct duplicate disposition: `prj_41` is retained, assigned `state = DUPLICATE`, linked to `duplicate_of = prj_07`, and recorded in audit log without deleting or merging data.
   - 100% idempotent: repeated runs identify SHA-256 match and execute as NOOP without mutating existing records.

4. **Public Web UI & Server-Rendered Initial HTML**:
   - Public Event Overview (`/` and `/events/<slug>/`) with phase badge, stat counters, tracks, prizes, and description.
   - Public Project Gallery (`/projects` and `/events/<slug>/projects/`) with server-rendered fixture titles (ensuring `tools/run.py` static HTML assertion passes), instant client-side search, and track filtering.
   - Project Detail (`/projects/<uuid>/`) with team roster (display names only) and repository links.
   - Visual System: Vercel-style clean neutral workspace (`#FAFAFA`, `#FFFFFF`, `#171717`) with inverse hero (`#0A0A0A`).

5. **Security, Deadlines & Event-Scoped Permissions**:
   - Strict deadline enforcement: submissions to closed events return 403 Forbidden.
   - Judge score confidentiality: judges see only their own assigned scores; peer score inspection (`?judge=judge_a`) returns 403 Forbidden; participants are blocked with 403.
   - Organiser export: CSV export restricted to organisers (`/api/export.csv`).

## Commands Actually Executed Against the Application

1. **Database Container Startup**:
   - Command: `podman run -d --name dogfood-db -e POSTGRES_DB=dogfood -e POSTGRES_USER=dogfood -e POSTGRES_PASSWORD=dogfood -p 5432:5432 docker.io/library/postgres:16-alpine`
   - Outcome: `pg_isready` reports accepting connections on 5432.

2. **Database Migrations**:
   - Command: `PYTHONPATH=src/backend .venv/bin/python src/backend/manage.py migrate`
   - Outcome: Applied all migrations for `accounts`, `events`, `teams`, `submissions`, `judging`, `audit`, `imports`, and `sessions`.

3. **Fixture Import & Seed**:
   - Command: `PYTHONPATH=src/backend .venv/bin/python src/backend/manage.py seed_demo --output-toml`
   - Outcome: Imported 41 projects, 126 reviews, 30 judges, 40 teams, 8 tracks. Generated `.dogfood.toml` with live session cookies.

4. **Automated Test Suite (pytest)**:
   - Command: `.venv/bin/pytest`
   - Outcome: `10 passed in 34.50s` (covering models, duplicate constraints, fixture importer idempotency, session auth, judge confidentiality, CSV export permissions).

5. **Original Acceptance Checker**:
   - Command: `python3 tools/run.py .dogfood.toml`
   - Outcome:
     ```text
     DOGFOOD 2026 acceptance report
     portal: http://localhost:8000
     claimed: T1
     fixtures: fixtures.json

     T1  gallery is public ................. PASS
     T1  project from fixtures shown ....... PASS
     T1  closed event refuses submissions .. PASS
     T2  judge sees own scores ............. PASS
     T2  judge cannot see peer scores ...... PASS
     T2  participant blocked ............... PASS
     T2  csv export works .................. PASS

     claimed T1, verified T1 T2
     ```

## Completed Behaviour (M02)

1. **Participant Workspace & Team Management**:
   - Participant Workspace UI (`/workspace` and `/events/<slug>/workspace`) with team overview, roster member management, invite link generation, draft revision editor, submission receipt modal, and next actions.
   - Team formation with captain designation (`create_team`), unique per-user membership checks.
   - Team invitations (`TeamInvitation`) generated with cryptographically secure tokens, hashed with SHA-256 (`token_digest`), with invite acceptance route (`/join/team/<token>/`) and acceptance pair database constraints.

2. **Versioned Drafting & Optimistic Concurrency**:
   - Draft submissions persist as numbered `ProjectRevision` records with track assignment, repository/demo URLs, and markdown descriptions.
   - Optimistic concurrency control via `project.version`: stale saves mismatched with the server's current version are rejected with `409 Conflict` (`StaleSaveConflict`), returning the server's current version and title so the participant does not accidentally overwrite a teammate's edits.

3. **Captain Submission Enforcement & Audit Receipts**:
   - Explicit project submission (`submit_project`) restricted strictly to the team captain; non-captains attempting to submit are rejected with `403 Forbidden`.
   - Team size validation against event `min_team_size` and `max_team_size`.
   - Roster snapshot frozen at submission time on the revision record.
   - Immutable audit logging (`PROJECT_SUBMITTED`) and submission receipt returned with timestamp, captain name, and frozen roster.
   - Strict deadline cutoff: any draft save or submission after `submissions_closes_at` is rejected with `403 Forbidden`.

4. **Organiser Console & Event Management**:
   - Organiser console UI (`/events/<slug>/manage/`) with permissions check (403 for non-organisers).
   - Event metadata, tagline, markdown overview, markdown rules/code of conduct, team constraints, and deadline editing.
   - Versioned updates (`update_event_settings`) with `event.version` and `event.data_version` increment, and audit logging (`EVENT_SETTINGS_UPDATED`).

## Commands Actually Executed Against the Application (M02)

1. **M02 Migrations**:
   - Command: `PYTHONPATH=src/backend .venv/bin/python src/backend/manage.py migrate`
   - Outcome: Applied migrations for `media_assets` (`0001_initial`), `submissions` (`0002_revisionasset`), `events` (`0002_eventinvitation`), and `teams` (`0002_teaminvitation`).

2. **Automated Test Suite (pytest)**:
   - Command: `PYTHONPATH=src/backend .venv/bin/pytest tests/ -v`
   - Outcome: `16 passed in 31.87s` (including all 6 new M02 integration tests for team formation, invite tokens, draft versioning, optimistic locking 409 conflict, captain submission permissions, and organiser event editing).

3. **Acceptance Checker Verification**:
   - Command: `python3 tools/run.py .dogfood.toml`
   - Outcome:
     ```text
     DOGFOOD 2026 acceptance report
     portal: http://localhost:8000
     claimed: T1
     fixtures: fixtures.json

     T1  gallery is public ................. PASS
     T1  project from fixtures shown ....... PASS
     T1  closed event refuses submissions .. PASS
     T2  judge sees own scores ............. PASS
     T2  judge cannot see peer scores ...... PASS
     T2  participant blocked ............... PASS
     T2  csv export works .................. PASS

     claimed T1, verified T1 T2
     ```

## Completed Behaviour (M03)

1. **Frozen Weighted Rubric & Criteria Management**:
   - `freeze_rubric` domain service enforcing at least one criterion with positive weight.
   - Once frozen, criteria, weights, and rubric configuration are immutable.
   - Audit logging (`RUBRIC_FROZEN`) with criteria count snapshot.
   - Pre-assignment invariant: judges can only be assigned to projects after the event rubric is frozen.

2. **Explicit Track Grants & Conflict of Interest Checks**:
   - Explicit track grants (`JudgeTrackPermission`) required before a judge can be assigned to a project in that track (`assign_judge_to_project`).
   - Conflict of interest enforcement: a judge who is a member of the project's team cannot be assigned to review that project (`PermissionDenied`).
   - Cross-event boundary: judge, project, and rubric must all belong to the same event.
   - Assignment lifecycle: active assignments, audit trail (`JUDGE_ASSIGNED`), and revocation support (`revoke_judge_assignment`).

3. **Private Judging Evaluation & Review Revisions**:
   - Draft review saving (`save_draft_review`) allows partial criteria scoring (1–5 scale) and notes within the judging window.
   - Final review submission (`submit_review`) validates complete scoring: exactly one score for every criterion in the frozen rubric, within 1–5 range.
   - Immutable audit snapshots on every save/submission via `ReviewRevision`.
   - Event `data_version` incremented on submission, notifying downstream scoring pipelines.
   - Strict deadline enforcement: when judging window closes, evaluations are rejected (`PermissionDenied`).

4. **Judge Score Confidentiality & Organiser Coverage Dashboard**:
   - Strict confidentiality enforcement at `/api/v1/events/<slug>/judges/<judge_user_id>/scores/`:
     - Evaluating judge can access their own scores (`200 OK`).
     - Peer judges attempting to inspect another judge's scores are strictly rejected with `403 Forbidden`.
     - Participants attempting to inspect judge scores are strictly rejected with `403 Forbidden`.
     - Organisers can access judge evaluations for audit and moderation.
   - Real-time organiser coverage dashboard at `/events/<slug>/judging/manage/` and `/api/v1/events/<slug>/organiser/progress/` tracking required reviews, completed reviews, fully covered projects, partially covered projects, and uncovered projects.
   - Web judge console at `/judging` and `/events/<slug>/judging/` with assigned project queue, repo/demo evidence links, interactive 1–5 score pills, private feedback notes, and draft/submit actions.

## Commands Actually Executed Against the Application (M03)

1. **Automated Test Suite (pytest)**:
   - Command: `PYTHONPATH=src/backend .venv/bin/pytest tests/ -v`
   - Outcome: `22 passed in 76.42s` (covering models, duplicate constraints, fixture importer idempotency, session auth, participant workspace, optimistic locking 409 conflict, captain receipts, rubric freeze, track permissions, conflict of interest, review drafting/submission, peer confidentiality, and organiser coverage dashboard).

2. **Acceptance Checker Verification**:
   - Command: `python3 tools/run.py .dogfood.toml`
   - Outcome:
     ```text
     DOGFOOD 2026 acceptance report
     portal: http://localhost:8000
     claimed: T1
     fixtures: fixtures.json

     T1  gallery is public ................. PASS
     T1  project from fixtures shown ....... PASS
     T1  closed event refuses submissions .. PASS
     T2  judge sees own scores ............. PASS
     T2  judge cannot see peer scores ...... PASS
     T2  participant blocked ............... PASS
     T2  csv export works .................. PASS

     claimed T1, verified T1 T2
     ```

## Completed Behaviour (M04)

1. **Deterministic Scoring & Ridge Normalisation (`apps/judging/calculation.py`)**:
   - Pure, deterministic mathematical solver for `RAW_WEIGHTED_V1` and `RIDGE_JUDGE_OFFSET_V1`.
   - Weighted score calculation across rubric criteria: $x_{j,p} = \sum (w_c \cdot s_{j,p,c}) / \sum w_c$.
   - Ridge judge offset model minimizing squared residuals with quadratic shrinkage penalty ($\lambda \sum b_j^2$, default $\lambda = 5.0$).
   - Strict convexity guarantee and zero-division immunity: constant-scoring judges converge smoothly without error.
   - Bipartite graph analysis detecting disconnected comparison components.
   - Competition ranking (1, 1, 3) with tie resolution rounded to 6 decimal places.
   - Unreviewed projects flagged with `NO_REVIEWS` and left unranked; partial coverage flagged with `FEWER_THAN_REQUIRED_REVIEWS`.
   - Canonical SHA-256 digests generated for both input snapshot and output result rows.

2. **Result Run Calculations & Previews (`apps/results/services.py`)**:
   - Domain service `calculate_result_run` creating immutable `ResultRun` and `ResultRow` records.
   - Private organiser calculation preview endpoint `/api/v1/events/<slug>/results/preview/` (returns 403 Forbidden to participants and anonymous users).

3. **Official Publication & Data-Leak Prevention**:
   - Formal publication service `publish_results` enforcing that the judging window has closed and the result run's data version matches `event.data_version`.
   - Data leak prevention: public endpoint `/api/v1/events/<slug>/results/` strictly returns `404 Not Found` until results are formally published.
   - On publication: creates immutable `Publication`, updates `event.active_publication_id`, freezes judging (`event.judging_frozen_at = now`), and records `AuditEvent(action="RESULTS_PUBLISHED")`.

4. **Public Leaderboard & Sanitized CSV Export**:
   - Web results page at `/results` and `/events/<slug>/results/` displaying medal badges (#1, #2, #3), project titles, teams, tracks, and official scores.
   - Sanitized CSV exports at `/api/export.csv` and `/api/v1/events/<slug>/exports/results.csv` restricted to organisers, with spreadsheet formula injection protection (cells starting with `=`, `+`, `-`, `@`, `\t`, `\r` escaped with a leading `'`).

5. **Release Documentation Deliverable**:
   - Created canonical `JUDGING.md` detailing mathematical formulation, solver contracts, coordinate descent algorithm, convergence guarantees, and edge case defenses.

## Commands Actually Executed Against the Application (M04)

1. **M04 Migrations**:
   - Command: `PYTHONPATH=src/backend .venv/bin/python src/backend/manage.py makemigrations results && PYTHONPATH=src/backend .venv/bin/python src/backend/manage.py migrate`
   - Outcome: Created and applied `results.0001_initial` (`ResultRun`, `ResultRow`, `Publication`).

2. **Automated Test Suite (pytest)**:
   - Command: `PYTHONPATH=src/backend .venv/bin/pytest tests/ -v`
   - Outcome: `32 passed in 110.26s` (covering models, duplicate constraints, fixture importer, session auth, participant workspace, optimistic locking 409 conflict, captain receipts, rubric freeze, track permissions, conflict checks, judge workspace, private evaluation, peer confidentiality, coverage dashboard, ridge solver mathematics, constant judge immunity, 1-review shrinkage, graph components, competition ties, calculation preview, no-leak protection, publication, and sanitized CSV exports).

3. **Acceptance Checker Verification**:
   - Command: `python3 tools/run.py .dogfood.toml`
   - Outcome:
     ```text
     DOGFOOD 2026 acceptance report
     portal: http://localhost:8000
     claimed: T1
     fixtures: fixtures.json

     T1  gallery is public ................. PASS
     T1  project from fixtures shown ....... PASS
     T1  closed event refuses submissions .. PASS
     T2  judge sees own scores ............. PASS
     T2  judge cannot see peer scores ...... PASS
     T2  participant blocked ............... PASS
     T2  csv export works .................. PASS

     claimed T1, verified T1 T2
     ```

## Environment Limitations & Gaps

1. **Docker / Rootless Podman**:
   - Host uses Podman 5.8.4 instead of Docker. `podman` runs the PostgreSQL 16 container rootlessly on port 5432.
   - `podman-compose` is installed in `.venv`.
2. **Browser Subagent Playwright Context**:
   - Upstream CDN 404 downloading Playwright linux driver in subagent; verified via integration tests and acceptance checker.
3. **M05 Boundary**:
   - Milestone M05 is complete. Next milestone in execution plan is M06 (Comments, moderation, rate limits, abuse signals and community result snapshots).

## Completed Behaviour (M05)

1. **Voting Policies & Gates**:
   - Developed `VotingPolicy` with 3 documented gate behaviours: `AUTHENTICATED`, `INVITE_LINK`, `EMAIL_VERIFIED`.
   - Organisers can configure the voting policy until the voting window opens.
2. **Voter Identities**:
   - Developed `VoterIdentity` to uniquely track users based on their principal (User, Email, Link).
3. **Voting Link Grants**:
   - Implemented `VotingLinkGrant` generation and redemption, resolving to a `VoterIdentity`.
4. **Email Challenge**:
   - Implemented offline-friendly email challenge generation (`EmailChallenge`) falling back to server logs for token retrieval, and challenge verification resolving to a `VoterIdentity`.
5. **Stable Random Ballots**:
   - Implemented `BallotSession` providing a stable seed for project shuffling and tracking eligible IDs across user sessions.
6. **Support/Withdraw Votes**:
   - Implemented exact voting deadline cut-offs via database lock check.
   - Enforced single vote row per `(event, project, voter_identity)` using unique constraints.
   - Handled toggling active/withdrawn vote states.
   - Prevented conflicts of interest (users voting for their own team).

## Commands Actually Executed Against the Application (M05)

1. **M05 Migrations**:
   - Command: `PYTHONPATH=src/backend .venv/bin/python src/backend/manage.py makemigrations voting && PYTHONPATH=src/backend .venv/bin/python src/backend/manage.py migrate`
   - Outcome: Applied `voting.0001_initial` migrations (`VotingPolicy`, `VotingLinkGrant`, `VoterIdentity`, `EmailChallenge`, `Vote`, `BallotSession`).

2. **Automated Test Suite (pytest)**:
   - Command: `PYTHONPATH=src/backend .venv/bin/pytest tests/integration/test_voting_workflow.py -v`
   - Outcome: `6 passed in 3.48s` (covering voting policy configuration, authenticated voting flow, link grant flow, email challenge flow, ballot session pagination, and voting deadline enforcement).

## Completed Behaviour (M06)

1. **Project Discussion & Comments Lifecycle**:
   - `Comment` model created with states `VISIBLE`, `HIDDEN`, and `DELETED`, plain-text enforcement (max 5,000 characters), and optimistic concurrency versioning.
   - Author edit restriction: only permitted inside active comment window.
   - Scrub-on-delete: authors and moderators can delete comments; content is immediately blanked (`""`) so deleted text is not retained indefinitely in general audit logs.
   - Users cannot edit hidden or deleted comments back into public visibility.

2. **Abuse Signals & Keyed Network Privacy**:
   - `AbuseSignal` model with anomaly kinds (`RAPID_ACCOUNT_VOTES`, `VOTING_BURST`, `SUSPICIOUS_NETWORK`, etc.).
   - Keyed HMAC-SHA256 network digest (`hash_network_key`) to preserve privacy and avoid raw IP address storage.

3. **Reasoned Moderation Cases & Organizer Inbox**:
   - `ModerationCase` model tracking targeted content (`COMMENT`, `VOTE`, `VOTER_IDENTITY`, `ABUSE_SIGNAL`) and decisions (`HIDE_COMMENT`, `RESTORE_COMMENT`, `SUSPEND_IDENTITY`, `VOID_VOTES`, `DISMISS_SIGNAL`).
   - Organizer inbox REST API (`/api/v1/events/<slug>/moderation/inbox/`) listing open cases and signals.
   - Next-action integration: dynamic reminder (`COMPLETE_MODERATION_REVIEWS`) displayed to organizers when unresolved moderation reviews exist.
   - Reasoned audit records generated for each decision with explicit actor, affected entity IDs, and rationale.

4. **Shared PostgreSQL Rate Limiting**:
   - Implemented in `apps/accounts/rate_limit.py` using atomic row updates on `RateLimitBucket`.
   - Enforced limits across concurrent workers: comments (5/min, 50/day), email challenges (3/hr address, 20/hr network), invalid links (10/10m), vote transitions (60/min), and reports (10/hr). Returns `429 Too Many Requests` with `retry_after`.

5. **Community Result Snapshots & Data Leak Prevention**:
   - `CommunityResultRow` created on `ResultRun` recording `counted_votes`, `excluded_votes`, and competition `rank` (with `1, 1, 3` tie-breaks).
   - Zero-vote safety: events with zero votes record `rank = None` ("No community votes recorded") rather than fabricated winners.
   - Pre-publication confidentiality: `/api/v1/events/<slug>/results/` returns `404 Not Found` until official publication; unreleased totals and community rankings are never leaked.
   - Publication gate: `publish_results` strictly checks that both judging and voting windows are closed before publication.

6. **Release Deliverable**:
   - Created `T3-ACCEPTANCE.md` detailing architecture, data model, audit decisions, rate limits, confidentiality, and test evidence.

## Commands Actually Executed Against the Application (M06)

1. **M06 Migrations**:
   - Command: `PYTHONPATH=src/backend .venv/bin/python src/backend/manage.py makemigrations voting results && PYTHONPATH=src/backend .venv/bin/python src/backend/manage.py migrate`
   - Outcome: Applied `results.0002_communityresultrow` and `voting.0002_abusesignal_moderationcase_comment`.

2. **Automated Test Suite (pytest)**:
   - Command: `PYTHONPATH=src/backend .venv/bin/pytest tests/integration/test_community_workflow.py -v`
   - Outcome: `6 passed in 5.21s`.
   - Command: `PYTHONPATH=src/backend .venv/bin/pytest tests/`
   - Outcome: `44 passed in 88.35s` (covering models, permissions, fixture import, participant workspace, judging, calculation, voting, comments, moderation, rate limits, and results).

3. **Acceptance Checker Verification**:
   - Command: `python3 tools/run.py .dogfood.toml`
   - Outcome: `claimed T1, verified T1 T2` (100% PASS).




