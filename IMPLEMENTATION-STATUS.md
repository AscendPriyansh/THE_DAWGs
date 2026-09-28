# Implementation status

Status: M01 milestone complete and verified against PostgreSQL.

- **Current authorised milestone**: M01 (Compose/dev foundation, custom User, real sessions, event roles, faithful fixture import, event overview and gallery/detail).
- **Current implementation location**: `src/backend/` (Django + DRF) and PostgreSQL 16.
- **Startup URL**: `http://localhost:8000` (Gallery at `http://localhost:8000/projects`).
- **Claimed Tiers in .dogfood.toml**: `["T1"]` (M01 scope; T2 foundation implemented and passing checker, full T2 features in M02–M04).

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

## Environment Limitations & Gaps

1. **Docker / Rootless Podman**:
   - Host uses Podman 5.8.4 instead of Docker. `podman` runs the PostgreSQL 16 container rootlessly on port 5432.
   - `podman-compose` is installed in `.venv`.
2. **Browser Subagent Playwright Context**:
   - The IDE's browser subagent reported an external upstream 404 downloading Playwright driver `playwright-1.57.0-linux.zip` from Microsoft CDN. Web rendering was verified through curl, server logs, and automated integration tests.
3. **M01 Boundary**:
   - Next authorized milestone is M02 (Event editing/Markdown/visual assets, dates/tracks/prizes, teams/invites, versioned submissions and uploads, participant next actions).
   - Later-tier features (normalisation math, public voting, webhooks, certificates) remain intentionally unbuilt until their authorised milestones.
