# Implementation status

Status: M03 milestone complete and verified against PostgreSQL.

- **Current authorised milestone**: M03 (Frozen weighted rubric, explicit track grants, conflict checks, judge workspace, private evaluation, peer-score confidentiality, organiser coverage dashboard).
- **Current implementation location**: `src/backend/` (Django + DRF) and PostgreSQL 16.
- **Startup URL**: `http://localhost:8000` (Gallery at `/projects`, Workspace at `/workspace`, Judge console at `/judging`, Organiser coverage at `/events/sample-hack-2026/judging/manage/`).
- **Claimed Tiers in .dogfood.toml**: `["T1"]` (Passing checker for T1 and T2).

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

## Environment Limitations & Gaps

1. **Docker / Rootless Podman**:
   - Host uses Podman 5.8.4 instead of Docker. `podman` runs the PostgreSQL 16 container rootlessly on port 5432.
   - `podman-compose` is installed in `.venv`.
2. **Browser Subagent Playwright Context**:
   - Upstream CDN 404 downloading Playwright linux driver in subagent; verified via integration tests and acceptance checker.
3. **M03 Boundary**:
   - Milestone M03 is complete. Next milestone in execution plan is M04 (Scoring, independent normalisation checks, result preview/publication, CSV, core UX and adoption baseline).


