# Tier 3 Acceptance Report — Community, Voting, Moderation, & Result Snapshots

Status: **Verified and Complete (Milestones M05 & M06)**  
Target: **Tier 3 (T3)**  
Date: 28 September 2026  
Database: PostgreSQL 16 (running via Podman)  
Automated Test Evidence: 44 passed in 88.35s (covering core models, judging, voting, moderation, rate limiting, and community snapshots)

---

## 1. Executive Summary

Milestone M05 established community voting policies, three gate mechanisms (`AUTHENTICATED`, `INVITE_LINK`, `EMAIL_VERIFIED`), cryptographic challenges, and stable randomized ballots. Milestone M06 delivers:
1. **Project Discussion & Comments**: Authenticated, plain-text comment lifecycle with editing inside active window, author deletion with complete body scrubbing for privacy, and organizer moderation.
2. **Abuse Signals & Keyed Network Privacy**: Automated abuse signal detection without raw IP address storage (using rotating HMAC-SHA256 keyed digests).
3. **Reasoned Moderation Cases & Organizer Inbox**: Full case reporting, organizer inbox, decision actions (`HIDE_COMMENT`, `RESTORE_COMMENT`, `SUSPEND_IDENTITY`, `VOID_VOTES`, `DISMISS_SIGNAL`), and immutable `AuditEvent` records documenting explicit reasons and affected IDs.
4. **PostgreSQL-Enforced Atomic Rate Limiting**: Shared database-backed `RateLimitBucket` ensuring strict rate limits across concurrent application workers with `429 Too Many Requests` / `Retry-After` enforcement.
5. **Community Result Snapshots**: Reproducible community vote counting, competition ties (`1, 1, 3`), zero-vote safety ("No community votes recorded" instead of invented winners), and strictly zero data leaks prior to publication.

---

## 2. Implemented Schema & Entities

The following entities defined in `DATA-MODEL.md` Section 11 are implemented and migrated in PostgreSQL:

| Entity | Table Name | Purpose & Enforcement |
|---|---|---|
| `VotingPolicy` | `voting_policies` | One per event; modes: `AUTHENTICATED`, `INVITE_LINK`, `EMAIL_VERIFIED`; `comments_enabled`; `minimum_account_age_seconds`. |
| `VoterIdentity` | `voter_identities` | Scoped to event; polymorphic principal (User, Email HMAC, Link grant); statuses: `ACTIVE`, `SUSPENDED`. |
| `VotingLinkGrant` | `voting_link_grants` | Cryptographically random single-use or multi-use grants with SHA-256 token digests. |
| `EmailChallenge` | `email_challenges` | Short-lived (15 min) email verification challenges with honest fallback to logging when SMTP is offline. |
| `Vote` | `votes` | Unique per `(event_id, project_id, voter_identity_id)`; states: `ACTIVE`, `WITHDRAWN`, `VOID`. |
| `BallotSession` | `ballot_sessions` | Stable random ordering seed and reconciled eligible project snapshots per voter. |
| `Comment` | `comments` | Plain text (max 5,000 chars); states: `VISIBLE`, `HIDDEN`, `DELETED`; versioned for optimistic updates. |
| `AbuseSignal` | `abuse_signals` | Behavioral anomalies (`RAPID_ACCOUNT_VOTES`, `VOTING_BURST`, etc.) with keyed network hashes. |
| `ModerationCase` | `moderation_cases` | Event-scoped target review (`COMMENT`, `VOTE`, `VOTER_IDENTITY`, `ABUSE_SIGNAL`) with status and decision. |
| `RateLimitBucket` | `rate_limit_buckets` | Atomic PostgreSQL counters keyed by `(scope, subject_key, window_start)` with expiration cleanup. |
| `CommunityResultRow` | `community_result_rows` | Immutable snapshot of `counted_votes`, `excluded_votes`, and competition `rank` linked to `ResultRun`. |

---

## 3. Comments & Content Moderation

### Comment Lifecycle
- **Creation Window**: Comments can only be posted when the event is `PUBLISHED`, after `submissions_opens_at`, and before `voting_closes_at` (or `judging_closes_at` if unconfigured).
- **Authentication**: Requires a verified account (`author_user`). Voting credentials alone (like temporary link grants) cannot post comments without account authentication.
- **Plain-Text Scrubbing**: Comment body is strictly plain text up to 5,000 characters. When deleted by the author or moderator, `comment.body` is cleared (`""`) immediately; audit logs retain only action metadata and never copy deleted text indefinitely.
- **State Integrity**: Users cannot edit `HIDDEN` or `DELETED` comments back into public visibility.

### Moderation Decisions
Organizers can review and resolve cases via `/api/v1/events/<slug>/moderation/cases/<id>/resolve/`:
- `HIDE_COMMENT`: Hides abusive or spam comments from public view.
- `RESTORE_COMMENT`: Restores false positives to `VISIBLE`.
- `SUSPEND_IDENTITY`: Suspends a voter identity, immediately preventing future votes and invalidating `event.data_version`.
- `VOID_VOTES`: Voids specific fraudulent votes (`state = VOID`), incrementing `event.data_version` to invalidate stale result calculations.
- `DISMISS_SIGNAL`: Dismisses review signals with a recorded reason.

Every resolution writes an `AuditEvent` with action `MODERATION_CASE_RESOLVED`, recording the actor, affected entity IDs, and the organizer's reason.

---

## 4. Enforced Rate Limits

Implemented in `apps/accounts/rate_limit.py` using atomic PostgreSQL row updates on `RateLimitBucket`:

| Action | Configured Scope | Limit | Window | Key Subject |
|---|---|---|---|---|
| Vote transitions | `vote_transition` | 60 | 60 seconds | `{event_id}:{voter_identity_id}` |
| New comments (burst) | `new_comment_min` | 5 | 60 seconds | `{event_id}:{user_id}` |
| New comments (daily) | `new_comment_day` | 50 | 86,400 seconds | `{event_id}:{user_id}` |
| Email challenge (address) | `email_challenge_addr` | 3 | 3,600 seconds | `{event_id}:{email_digest}` |
| Email challenge (network) | `email_challenge_net` | 20 | 3,600 seconds | `{event_id}:{network_key}` |
| Invalid link redemption | `invalid_link` | 10 | 600 seconds | `{network_key}` |
| Content reports | `report_case` | 10 | 3,600 seconds | `{event_id}:{user_id}` |

Exceeding any limit immediately returns `429 Too Many Requests` with a calculated `retry_after` response header/body.

---

## 5. Community Result Snapshots & Data Confidentiality

### Strict No-Leak Guarantee
- **Pre-Publication**: Public endpoints (`/api/v1/events/<slug>/results/`) strictly return `404 Not Found`. Project detail and gallery views never leak project vote totals, community rankings, or unreleased scores.
- **Publication Requirement**: `publish_results` enforces that **both** the judging window (`judging_closes_at`) and voting window (`voting_closes_at`) must be closed before publication is permitted.
- **Competition Ranking**:
  - Eligible submitted projects are ranked by `counted_votes` descending.
  - Ties receive competition ranking (e.g. `1, 1, 3`).
  - Events with zero votes cast cleanly record `rank = None` ("No community votes recorded") rather than fabricating artificial winners.
- **Excluded Votes Accounting**: Withdrawn votes, organizer-voided votes, and votes from suspended identities are classified as `excluded_votes` and excluded from `counted_votes`.

---

## 6. Verification & Automated Test Evidence

### Pytest Integration Suite (`tests/integration/test_community_workflow.py`)
```text
tests/integration/test_community_workflow.py::test_comment_lifecycle_and_scrubbing PASSED
tests/integration/test_community_workflow.py::test_comment_window_and_policy_enforcement PASSED
tests/integration/test_community_workflow.py::test_rate_limiting_enforcement PASSED
tests/integration/test_community_workflow.py::test_moderation_cases_and_decisions PASSED
tests/integration/test_community_workflow.py::test_community_result_snapshots_and_confidentiality PASSED
tests/integration/test_community_workflow.py::test_community_api_endpoints_and_next_action PASSED
```

### Full Repository Test Suite
```text
======================== 44 passed in 88.35s (0:01:28) =========================
```

### Official Acceptance Checker (`python3 tools/run.py .dogfood.toml`)
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
