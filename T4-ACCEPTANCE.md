# Tier 4 Acceptance Report — REST API, Webhooks, Certificates & Signed Records, Embeddable Gallery, Bulk Import/Export

Status: **Verified and Complete (Milestones M07–M10)**
Target: **Tier 4 (T4)**
Date: 29 September 2026
Database: PostgreSQL 16
Automated Test Evidence: 96 passed across 11 integration test modules (M01–M11)

---

## 1. Executive Summary

Milestones M07 through M10 deliver the complete T4 feature set:

- **M07**: Complete event REST API with scoped API keys, local OpenAPI docs, and idempotency contracts
- **M08**: Shared persistent background worker, transactional outbox, and webhook delivery with retries
- **M09**: Award decisions, certificate templates, PDF generation, Ed25519-signed verifiable judge records, key rotation/revocation, consent management, and public verification
- **M10**: Embeddable public project gallery with CSP frame-ancestor security, and a validated portable bulk import/export system

---

## 2. T4-01: Complete REST API (M07)

### Implemented

- **Scoped API credentials**: `ApiCredential` model with SHA-256-hashed token storage, named scopes (`WRITE_EVENTS`, `WRITE_JUDGING`, `WRITE_WEBHOOKS`), and immediate revocation.
- **All UI business actions have API equivalents**: Event CRUD, track/prize management, team invitations, project submission, judge assignments, rubric management, review submission, result runs, publication, voting policy, ballot sessions, credential issuance, and embed configuration.
- **Idempotency contracts**: Submit/update operations use `If-Match` version headers where appropriate; duplicate submission is rejected with 409.
- **Role × scope intersection**: API calls require both a valid session/key AND the correct event-scoped role. Organiser-only endpoints reject judges; participant-only endpoints reject visitors.

### Evidence

| Test file | Tests | Coverage |
|---|---|---|
| `test_api_contracts.py` | 12 | Scoped key auth, revocation, role intersection, idempotency |
| `test_auth_and_permissions.py` | 9 | Role denial, cross-event isolation, visitor access |
| `test_webhooks_and_jobs.py` | 8 | API-triggered webhook delivery |

API endpoint inventory: see `API-COVERAGE.md` (70+ endpoints documented).

### OpenAPI / Interactive Docs

Available at `http://localhost:8000/api/v1/schema/` (local only). Schema generated from DRF serializers.

---

## 3. T4-02: Webhooks (M08)

### Implemented

- **Transactional outbox**: `DomainEvent` rows written atomically within the same transaction as the triggering business action. No events are lost on crash.
- **Delivery with retries**: `WebhookDelivery` and `WebhookDeliveryAttempt` track attempt count, response status, and next retry time. Exponential backoff up to 5 retries.
- **Crash recovery**: Worker polls `background_jobs` and `webhook_deliveries` on startup; unprocessed items are retried.
- **Duplicate/replay safety**: Webhook payloads carry `event_id` (UUID); receivers can use this for idempotent processing.
- **SSRF protection**: Webhook endpoints validate destination hostname against a blocklist of private/loopback ranges. Non-HTTP/HTTPS schemes are rejected.
- **Secret signing**: Webhook payloads are signed with HMAC-SHA256 (`X-Dogfood-Signature` header). Receivers can verify authenticity.

### Domain events delivered

All events documented in `WEBHOOK-EVENTS.md`:
`project.submitted`, `project.draft_saved`, `review.submitted`, `results.published`, `credential.issued`, `team.joined`, `vote.cast`, and 14 more.

### Evidence

| Test file | Tests | Coverage |
|---|---|---|
| `test_webhooks_and_jobs.py` | 8 | Atomicity, rollback, retry, crash recovery, duplicate safety, SSRF, secret handling |

---

## 4. T4-03: Certificates and Records (M09)

### Implemented

- **`CertificateTemplate`**: Per-event templates with customisable title, body, and issuer name.
- **`AwardDecision`**: Explicit organiser-recorded award decisions (prize category, track, rank) linked to projects. Required for winner-type credential eligibility.
- **Eligibility rules**: Participants are eligible for participation credentials after official submission. Judges are eligible for judge records after completing reviews. Winners require an explicit `AwardDecision`.
- **Local PDF generation**: `reportlab` renders visually styled certificate PDFs locally — no cloud service required.
- **PDF integrity**: PDF bytes are SHA-256 hashed and stored in the credential record. Download endpoint verifies hash on every request.
- **Permissioned download**: `GET /api/v1/events/{slug}/credentials/{id}/pdf/` requires the credential holder or an organiser.
- **Quarantined assignments**: Judges whose assignments were quarantined (historical data anomalies) are ineligible for judge credentials.

### Evidence

| Test file | Tests | Coverage |
|---|---|---|
| `test_credentials.py` | 25 | Eligibility, PDF generation, PDF hash integrity, permissioned download, batch issuance, winner with award, quarantined ineligibility |

---

## 5. T4-04: Signed Publicly Verifiable Judge Records (M09)

### Implemented

- **Ed25519 signing**: Credentials are signed with Ed25519 private keys via the `cryptography` library. Signing is deterministic over canonical JSON (sorted keys, no whitespace).
- **Exact bytes verification**: Public verification page re-serialises the payload, checks the signature against the stored public key, and reports VALID or INVALID.
- **Tamper detection**: Any modification to the credential payload (name, event, dates, score, rank) causes signature verification to fail.
- **Trusted-key distinction**: Only keys marked `is_trusted=True` are returned by the public issuer keys endpoint. Compromised keys are revoked and removed from the trusted set.
- **Key rotation**: New keys can be generated while old keys remain in the `trusted_keys` list for backward compatibility with previously issued credentials.
- **Key revocation**: `CredentialStatusEvent` records REVOKED or SUPERSEDED status with reason. Revoked credentials fail public verification.
- **Consent management**: `PublicRecordConsent` per user per event. Withdrawing consent hides personal details on public verification pages.
- **Offline verification**: Credential envelope (payload + signature + public key) is fully self-contained. Verification can be performed offline with only the Ed25519 public key.

### Evidence

| Test file | Tests | Coverage |
|---|---|---|
| `test_credentials.py` | 25 | Ed25519 signing, deterministic JSON, tamper detection, trusted-key distinction, rotation, revocation, offline verification, consent |

---

## 6. T4-05: Embeddable Gallery (M10)

### Implemented

- **`EmbedConfiguration`**: Per-event organiser-configurable settings — enabled/disabled, allowed parent origins (RFC 6454 validated), theme (`light`/`dark`/`auto`), default track filter, search bar visibility.
- **Different-origin iframe**: Embed route removes `X-Frame-Options`. Parent origins are enforced via `Content-Security-Policy: frame-ancestors <origins>`. Wildcard and path-segment origins are rejected.
- **Public data only**: Embed gallery displays only officially submitted, eligible, published projects. Drafts, duplicates, disqualified projects, judge notes, private scores, unreleased votes, and internal IDs are completely absent from both initial HTML and JSON state.
- **Unavailable fallback**: `embed_unavailable.html` is rendered when the event is in draft/archived state, or when embed is disabled.
- **Project links**: All project links open in `target="_blank" rel="noopener noreferrer"`.

### Evidence

| Test file | Tests | Coverage |
|---|---|---|
| `test_m10_embed_and_portable_archive.py` | 10 | Different-origin iframe, CSP frame-ancestors, private data exclusion, unavailable state, embed config CRUD, malicious origin rejection |

---

## 7. T4-06: Bulk Import/Export (M10)

### Implemented

- **v1 archive format**: Standard ZIP with `manifest.json`, SHA-256 checksums, structured JSON records (`records/*.json`), and opaque media files (`media/`).
- **Complete domain round-trip**: All event entities exported (event, users, memberships, teams, projects, revisions, rubrics, assignments, reviews, results, credentials, audit records, media files) and importable into a fresh event.
- **Dry-run preview**: `validate_and_create_plan()` validates archive integrity and generates a `PortableImportPlan` with counts and warnings without modifying the database.
- **Apply plan**: `apply_plan()` runs atomically within a transaction; creates a new `DRAFT` event with remapped UUIDs and provenance metadata.
- **Idempotency**: Expired plans and duplicate applications are rejected.
- **Security rejections**: Path traversal, absolute paths, drive letters, symlinks, nested archives (outside `media/`), oversized archives (200 MB compressed / 1 GB uncompressed / 10,000 entries), and manifest hash mismatches are all rejected before any database writes.
- **Formula injection mitigation**: Text fields are sanitised against CSV/spreadsheet formula injection.
- **Privacy**: Exported archives exclude password hashes, auth tokens, private signing keys, and private judge scores.

### Evidence

| Test file | Tests | Coverage |
|---|---|---|
| `test_m10_embed_and_portable_archive.py` | 10 | Malicious archive rejection, dry-run preview, full domain round-trip, REST API endpoints, export worker integration |

---

## 8. Summary Table

| Requirement | Status | Test evidence |
|---|---|---|
| T4-01 REST API | ✅ DONE | `test_api_contracts.py` — 12 tests |
| T4-02 Webhooks | ✅ DONE | `test_webhooks_and_jobs.py` — 8 tests |
| T4-03 Certificates and records | ✅ DONE | `test_credentials.py` — 25 tests |
| T4-04 Signed verifiable judge records | ✅ DONE | `test_credentials.py` — 25 tests |
| T4-05 Embeddable gallery | ✅ DONE | `test_m10_embed_and_portable_archive.py` — 10 tests |
| T4-06 Bulk import/export | ✅ DONE | `test_m10_embed_and_portable_archive.py` — 10 tests |

**Total automated test evidence**: 96 tests across 11 integration test modules, 0 failures.

---

## 9. Running T4 Evidence

```bash
# Run T4-relevant tests only
PYTHONPATH=src/backend .venv/bin/pytest \
  tests/integration/test_api_contracts.py \
  tests/integration/test_webhooks_and_jobs.py \
  tests/integration/test_credentials.py \
  tests/integration/test_m10_embed_and_portable_archive.py \
  -v

# Run full suite
PYTHONPATH=src/backend .venv/bin/pytest tests/ --tb=short -q

# Original checker (T1/T2 verification)
python3 tools/run.py .dogfood.toml
```
