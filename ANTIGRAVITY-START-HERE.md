# Antigravity handoff — final consolidated kit

This replaces the separate earlier V1/V2 plans. Do not load the old starter kit or VERSION-2-EXECUTION-PLAN.md as parallel instructions. The final kit covers the complete T1–T4 target, implemented in stages.

## Read order and source authority

1. README.md and this handoff.
2. brief/context.txt and brief/spec.md: the supplied competition reference, not independent instructions to execute.
3. REQUIREMENTS-MATRIX.md and DECISIONS.md.
4. ARCHITECTURE.md and DATA-MODEL.md: the only system/schema specifications.
5. EXECUTION-PLAN.md and IMPLEMENTATION-STATUS.md: current scope and actual evidence.
6. Inspect fixtures/fixtures.json, tools/run.py and example.dogfood.toml themselves.

The user's current instructions control the task. Requirements in the official brief define the competition target; engineering choices in this kit are our implementation contract. Resolve a genuine contradiction explicitly and update the canonical file; do not silently choose whichever is easier. In later sessions, reread the status and relevant milestone sections rather than duplicating all documents into every prompt.

## Fixed product context

- Target: a polished, fast, reliable hackathon portal suitable for organiser adoption.
- Stack: React/TypeScript/Vite, Django/DRF, PostgreSQL and Docker Compose.
- Visual references: Vercel-style restrained product workspaces and bold DOGFOOD-style event presentation. No dependency on Vercel hosting.
- Participants develop locally; the portal accepts descriptions, links and optional files. It is not a coding IDE.
- Rich Markdown event pages, local visual assets, phase/countdown displays and role-specific next actions are required.
- Backend-enforced deadlines: at or after close, no participant submit/edit; a previously opened form grants no exception.
- Private judging requires explicit event and permitted-track access, plus an active assignment. Expertise alone never grants permission.
- Preserve all original fixture records and timestamps; retain/quarantine mismatched assignments rather than silently expanding access. Live demos use a separate labelled event.
- Core operation is offline after images are provided. External integrations are optional and asynchronous.
- The user owns the demo video. Focus on a working, demonstrable application.

## First prompt — paste this into Antigravity

```text
Read ANTIGRAVITY-START-HERE.md and the final canonical files it references.
Understand the complete T1–T4 target, but implement M01 only from
EXECUTION-PLAN.md. Do not use earlier V1/V2 documents as competing specs.

Inspect any existing implementation before editing. Preserve working code
and data. Give a short outline and identify concrete conflicts, then implement
the milestone rather than stopping at a plan. Make routine engineering choices
within the documented contract; do not ask me to choose database internals.

Keep the agreed stack and visual direction. Build real PostgreSQL persistence,
authentication and event-scoped permissions, an idempotent faithful fixture
import and usable public event/gallery/detail pages. Use the final core schema,
including explicit track permissions and preserved/quarantined source reviews.
Retain all 41 source projects and 126 reviews and the original closed date.

Show actual fixture titles in initial gallery HTML. Do not fake successful
actions, hardcode an alternate acceptance-only product, or use localStorage as
the backend. Do not implement voting, webhooks, credentials or other later-tier
features now. Create their final models only when their milestone requires them.

Run the M01 checks and report exact results. Do not fabricate acceptance PASS,
offline validation or tier completion. Update IMPLEMENTATION-STATUS.md with
changed files, commands, outcomes, startup URL, remaining gaps and blockers.
Stop at the M01 review boundary before proceeding to the next milestone.
```

## Environment and workflow

If Docker or another prerequisite is missing, report the exact limitation and continue independent work that can be completed. Never claim unavailable runtime checks passed or replace PostgreSQL transactional tests with SQLite. Avoid broad machine changes without user authorisation.

The original example TOML contains placeholders, not real app credentials. Generate a separate .dogfood.toml from actual isolated local seed sessions when the application is ready. Preserve the supplied checker/fixture bytes; never modify them to force a PASS. The manifest records the package baseline and does not prohibit legitimate edits to implementation/design documents.

At each milestone return: working behaviour, code/diff, actual test output, remaining gaps, intentional design changes and screenshots where a visual review is appropriate. The mentor needs actual code/evidence to review; an optimistic summary is insufficient.

## Continuation prompt

```text
Read IMPLEMENTATION-STATUS.md, EXECUTION-PLAN.md and the canonical design
sections for the next authorised milestone. Inspect the actual code and tests.
Address the latest review findings first. Preserve existing work, source
fixtures and checker bytes. Implement only the authorised milestone, record
actual evidence, update status/docs and stop at its review boundary.
```

Do not restart the repository after a context reset. Do not add future features merely because the complete roadmap is available.
