# WEBHOOK-EVENTS — Canonical Webhook & Transactional Outbox Specification

Status: **Canonical and Implemented (Milestone M08)**  
Updated: 28 September 2026  
Specification Version: `1.0`  
Standard Header Prefix: `Dogfood-`

---

## 1. Architectural Overview & Delivery Contracts

The Dogfood 2026 Hackathon Portal implements a durable, database-backed transactional outbox pattern for webhook notifications. Outbound deliveries run through a dedicated persistent worker (`python manage.py run_worker`) using PostgreSQL compare-and-set row locks.

### Core Delivery Guarantees
1. **At-Least-Once Delivery**:
   - Webhook delivery guarantees at-least-once delivery.
   - Receiver crashes or network timeouts after receiver processing but before local status commit can cause duplicate deliveries.
   - Every delivery includes an immutable `id` (DomainEvent UUID) and `delivery_id` (WebhookDelivery UUID) so receivers can deduplicate idempotently.
2. **Atomic Transactional Outbox**:
   - `DomainEvent` records and initial `WebhookDelivery` rows are created inside the **same database transaction** as the originating business mutation.
   - If an event update, submission, or review submission transaction rolls back, outbox records are rolled back atomically in PostgreSQL. Workers cannot process or transmit rolled-back changes.
3. **Lease Model via `SKIP LOCKED`**:
   - Workers query due deliveries using `SELECT ... FOR UPDATE SKIP LOCKED` and acquire an exclusive 60-second lease (`lease_owner`, `lease_until`).
   - HTTP network I/O executes strictly outside the database transaction.
   - Terminal status or retry backoff is recorded in a subsequent transaction only if the worker still holds a valid lease. Stale or reclaimed leases are discarded safely.
4. **Confidentiality Invariants**:
   - Webhook payloads contain strictly identifiers, state transitions, version tags, and public-safe metadata.
   - Payloads **never** transmit:
     - Unreleased judge scores or criterion score matrices
     - Private judging comments or peer reviews
     - Interim voting tallies or voter identities prior to or during voting
     - Authentication credentials, tokens, or invitation secrets
     - User email addresses (only public display names)
     - Raw uploaded asset bytes

---

## 2. Delivery State Machine & Retry Policy

```
[ PENDING ] ────────► [ LEASED ] (60s lease)
                           │
       ┌───────────────────┼────────────────────┐
       ▼                   ▼                    ▼
  [ SUCCEEDED ]        [ RETRY ]             [ DEAD ]
   (HTTP 2xx)      (Net Err, 408, 429, 5xx)  (Non-retryable 4xx or exhausted retries)
                           │                    │
                           └───────► [ REPLAY ] ◄
                             (Organiser Replay Action)
                               (Increments Gen)
```

### Retry Schedule & Jitter
- **Initial Attempt**: Dispatched immediately upon lease claim.
- **Maximum Retries**: Up to 7 retries per replay generation (8 total attempts per generation).
- **Backoff Intervals**:
  1. Attempt 2: ~5 seconds (±10% random jitter)
  2. Attempt 3: ~30 seconds (±10% random jitter)
  3. Attempt 4: ~2 minutes (120s)
  4. Attempt 5: ~10 minutes (600s)
  5. Attempt 6: ~1 hour (3,600s)
  6. Attempt 7: ~6 hours (21,600s)
  7. Attempt 8: ~24 hours (86,400s)
- **Retry-After Header**: HTTP 429 (Too Many Requests) and HTTP 503 (Service Unavailable) responses containing a valid integer `Retry-After` header are respected up to a bounded cap of 86,400 seconds (24 hours).
- **Terminal Status**: HTTP 4xx client errors (excluding 408 Request Timeout and 429 Rate Limit) indicate invalid endpoint routes or bad configurations and terminate immediately into the `DEAD` state without retrying.

### Replay Policy
- Organisers can trigger a manual replay of any `DEAD`, `FAILED`, or `SUCCEEDED` delivery.
- Replaying increments `replay_generation`, resets `attempts_in_generation` to `0`, schedules `next_attempt_at = now()`, and moves state to `PENDING`.
- Lifetime attempt history is strictly preserved in `WebhookAttempt`.
- Replaying a currently active leased delivery is forbidden.

---

## 3. Webhook Security, Encryption & SSRF Protection

### Secret Storage & Key Versioning
- Each `WebhookEndpoint` is issued a high-entropy secret (32 bytes urlsafe base64 prefixed with `whsec_`).
- The plaintext secret is revealed to the organiser **once** at registration.
- Secrets are encrypted at rest using an external deployment key (`WEBHOOK_ENCRYPTION_KEY` held outside the database).

### SSRF Protection
- **HTTPS Enforcement**: Webhook URLs must use the `https://` scheme (default port 443).
- **Userinfo Disallowed**: URLs containing embedded credentials (`https://user:pass@host`) are rejected.
- **Port Restrictions**: Non-standard ports are prohibited unless matching a deployment test allowlist.
- **Network Filtering**: Every destination hostname is resolved via DNS and checked against prohibited IP spaces:
  - Loopback (`127.0.0.0/8`, `::1`)
  - Private RFC-1918 / RFC-4193 subnets (`10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`, `fc00::/7`)
  - Link-local (`169.254.0.0/16`, `fe80::/10`)
  - Cloud provider metadata endpoints (`169.254.169.254`, `100.100.100.200`, `fd00:ec2::254`)
- **Connection Pinning**: Connection destinations are validated immediately before transmission.

### Signature Headers & Verification
Each outbound HTTP POST request includes the following standard headers:

| Header | Description | Example |
|---|---|---|
| `Content-Type` | JSON payload MIME type | `application/json` |
| `User-Agent` | Platform delivery agent | `Dogfood-Webhook-Delivery/1.0` |
| `Dogfood-Event-Id` | Unique UUID of the DomainEvent | `3fa85f64-5717-4562-b3fc-2c963f66afa6` |
| `Dogfood-Event-Type` | Registered event category | `results.published` |
| `Dogfood-Delivery-Id`| Unique UUID of this delivery | `7c9e6679-7425-40de-944b-e07fc1f90ae7` |
| `Dogfood-Timestamp` | Unix timestamp of dispatch (seconds) | `1727500000` |
| `Dogfood-Signature` | Signature digest with version prefix | `v1=a3f892bc...` |

#### Signature Formula
```
signature = HMAC-SHA256(
    key = webhook_secret.encode('utf-8'),
    msg = ASCII(Dogfood-Timestamp) + '.' + raw_body_bytes
).hexdigest()
```
Header format: `Dogfood-Signature: v1=<signature_hex>`

---

## 4. Canonical Event Catalog & Schemas

### 1. `event.updated`
Triggered when an organiser updates event title, tagline, rules markdown, or team constraints.
```json
{
  "id": "e0a12b3c-4d5e-6f7a-8b9c-0d1e2f3a4b5c",
  "event_id": "9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d",
  "type": "event.updated",
  "schema_version": "1.0",
  "entity_id": "9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d",
  "timestamp": "2026-09-28T12:00:00Z",
  "delivery_id": "f1b2c3d4-e5f6-7a8b-9c0d-1e2f3a4b5c6d",
  "payload": {
    "event_id": "9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d",
    "slug": "dogfood-2026",
    "version": 3,
    "data_version": 7
  }
}
```

### 2. `submission.created` & `submission.updated`
Triggered when a team captain explicitly submits or updates an official submission.
```json
{
  "id": "a1b2c3d4-e5f6-7a8b-9c0d-1e2f3a4b5c6d",
  "event_id": "9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d",
  "type": "submission.created",
  "schema_version": "1.0",
  "entity_id": "c1d2e3f4-a5b6-7c8d-9e0f-1a2b3c4d5e6f",
  "timestamp": "2026-09-28T14:30:00Z",
  "delivery_id": "8a7b6c5d-4e3f-2a1b-0c9d-8e7f6a5b4c3d",
  "payload": {
    "project_id": "c1d2e3f4-a5b6-7c8d-9e0f-1a2b3c4d5e6f",
    "title": "Autonomous Edge Vision",
    "revision_number": 1,
    "state": "SUBMITTED",
    "submitted_at": "2026-09-28T14:30:00Z"
  }
}
```

### 3. `rubric.frozen`
Triggered when an organiser officially locks criteria weights and scoring configuration.
```json
{
  "id": "b2c3d4e5-f6a7-8b9c-0d1e-2f3a4b5c6d7e",
  "event_id": "9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d",
  "type": "rubric.frozen",
  "schema_version": "1.0",
  "entity_id": "d2e3f4a5-b6c7-8d9e-0f1a-2b3c4d5e6f7a",
  "timestamp": "2026-09-28T15:00:00Z",
  "delivery_id": "7b6c5d4e-3f2a-1b0c-9d8e-7f6a5b4c3d2e",
  "payload": {
    "rubric_id": "d2e3f4a5-b6c7-8d9e-0f1a-2b3c4d5e6f7a",
    "version": 2,
    "criteria_count": 4,
    "frozen_at": "2026-09-28T15:00:00Z"
  }
}
```

### 4. `review.submitted`
Triggered when an assigned judge submits their score sheet. *Notice: Private criterion values and feedback comments are strictly withheld to preserve blind evaluation confidentiality.*
```json
{
  "id": "c3d4e5f6-a7b8-9c0d-1e2f-3a4b5c6d7e8f",
  "event_id": "9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d",
  "type": "review.submitted",
  "schema_version": "1.0",
  "entity_id": "e3f4a5b6-c7d8-9e0f-1a2b-3c4d5e6f7a8b",
  "timestamp": "2026-09-28T18:15:00Z",
  "delivery_id": "6c5d4e3f-2a1b-0c9d-8e7f-6a5b4c3d2e1f",
  "payload": {
    "review_id": "e3f4a5b6-c7d8-9e0f-1a2b-3c4d5e6f7a8b",
    "assignment_id": "f4a5b6c7-d8e9-0f1a-2b3c-4d5e6f7a8b9c",
    "project_id": "c1d2e3f4-a5b6-7c8d-9e0f-1a2b3c4d5e6f",
    "status": "SUBMITTED",
    "submitted_at": "2026-09-28T18:15:00Z"
  }
}
```

### 5. `results.published`
Triggered when official leaderboard results are published by an organiser after window closure.
```json
{
  "id": "d4e5f6a7-b8c9-0d1e-2f3a-4b5c6d7e8f9a",
  "event_id": "9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d",
  "type": "results.published",
  "schema_version": "1.0",
  "entity_id": "a5b6c7d8-e9f0-1a2b-3c4d-5e6f7a8b9c0d",
  "timestamp": "2026-09-28T20:00:00Z",
  "delivery_id": "5d4e3f2a-1b0c-9d8e-7f6a-5b4c3d2e1f0a",
  "payload": {
    "publication_id": "a5b6c7d8-e9f0-1a2b-3c4d-5e6f7a8b9c0d",
    "publication_number": 1,
    "result_run_id": "b6c7d8e9-f0a1-2b3c-4d5e-6f7a8b9c0d1e",
    "published_at": "2026-09-28T20:00:00Z"
  }
}
```

### 6. `community.moderated`
Triggered when an organiser resolves an abuse report or moderation case.
```json
{
  "id": "e5f6a7b8-c9d0-1e2f-3a4b-5c6d7e8f9a0b",
  "event_id": "9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d",
  "type": "community.moderated",
  "schema_version": "1.0",
  "entity_id": "c7d8e9f0-a1b2-3c4d-5e6f-7a8b9c0d1e2f",
  "timestamp": "2026-09-28T20:45:00Z",
  "delivery_id": "4e3f2a1b-0c9d-8e7f-6a5b-4c3d2e1f0a9b",
  "payload": {
    "case_id": "c7d8e9f0-a1b2-3c4d-5e6f-7a8b9c0d1e2f",
    "decision": "HIDE_COMMENT",
    "resolved_at": "2026-09-28T20:45:00Z"
  }
}
```

### 7. `job.completed`
Triggered when a persistent background job (e.g. export, certificate generation) finishes execution.
```json
{
  "id": "f6a7b8c9-d0e1-2f3a-4b5c-6d7e8f9a0b1c",
  "event_id": "9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d",
  "type": "job.completed",
  "schema_version": "1.0",
  "entity_id": "d8e9f0a1-b2c3-4d5e-6f7a-8b9c0d1e2f3a",
  "timestamp": "2026-09-28T21:10:00Z",
  "delivery_id": "3f2a1b0c-9d8e-7f6a-5b4c-3d2e1f0a9b8c",
  "payload": {
    "job_id": "d8e9f0a1-b2c3-4d5e-6f7a-8b9c0d1e2f3a",
    "kind": "EXPORT_EVENT",
    "state": "SUCCEEDED"
  }
}
```

---

## 5. Reference Python Receiver & Verifier

The following self-contained Python script demonstrates exact signature verification and constant-time digest comparison according to the platform specification:

```python
import hmac
import hashlib
import json
import time
from http.server import HTTPServer, BaseHTTPRequestHandler

WEBHOOK_SECRET = "whsec_test_secret_for_demonstration_purposes"
TOLERANCE_SECONDS = 300  # 5 minutes replay window


def verify_signature(secret: str, timestamp_str: str, raw_body: bytes, signature_header: str) -> bool:
    """Verifies HMAC-SHA256 signature against ASCII(timestamp) + '.' + raw_body."""
    try:
        ts = float(timestamp_str)
    except (ValueError, TypeError):
        return False

    # 1. Check timestamp tolerance
    if abs(time.time() - ts) > TOLERANCE_SECONDS:
        return False

    # 2. Extract v1 signature
    prefix = "v1="
    if not signature_header or not signature_header.startswith(prefix):
        return False
    received_digest = signature_header[len(prefix):].strip()

    # 3. Compute expected digest
    message_to_sign = f"{timestamp_str}.".encode("ascii") + raw_body
    expected_digest = hmac.new(
        secret.encode("utf-8"),
        message_to_sign,
        hashlib.sha256
    ).hexdigest()

    # 4. Constant-time comparison
    return hmac.compare_digest(expected_digest, received_digest)


class WebhookHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", 0))
        raw_body = self.rfile.read(content_length)

        timestamp = self.headers.get("Dogfood-Timestamp")
        signature = self.headers.get("Dogfood-Signature")
        event_id = self.headers.get("Dogfood-Event-Id")
        event_type = self.headers.get("Dogfood-Event-Type")

        if not verify_signature(WEBHOOK_SECRET, timestamp, raw_body, signature):
            self.send_response(401)
            self.end_headers()
            self.wfile.write(b'{"error": "Invalid or expired webhook signature"}')
            return

        payload = json.loads(raw_body.decode("utf-8"))
        print(f"[Webhook Received] Event={event_type} ID={event_id}")

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"status": "ACK"}')


if __name__ == "__main__":
    server = HTTPServer(("0.0.0.0", 9000), WebhookHandler)
    print("Webhook receiver listening on http://0.0.0.0:9000...")
    server.serve_forever()
```
