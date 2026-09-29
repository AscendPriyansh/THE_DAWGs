# Security Policy

## Supported Versions

| Version | Supported |
|---|---|
| M11 (current) | ✅ |
| M01–M10 | Historical milestones only |

---

## Security Architecture

### Authentication & Sessions

- **Database-backed sessions**: All sessions stored in `django_session` (PostgreSQL). No localStorage bearer tokens. Server-side session invalidation is immediate.
- **CSRF protection**: All state-changing endpoints require a valid CSRF token (`X-CSRFToken` header for API clients; `{% csrf_token %}` for HTML forms).
- **Password hashing**: Django default `PBKDF2PasswordHasher` with SHA-256.
- **Session rotation**: Sessions are rotated on login to prevent session fixation.

### Authorisation

- **Event-scoped roles**: `ORGANISER`, `JUDGE`, `PARTICIPANT`, `VISITOR` enforced per event via `EventMembership`. No global privilege escalation.
- **Judge isolation**: Judges can only read their own review scores. Peer scores, draft revisions, and other judges' notes are inaccessible.
- **Cross-event isolation**: All API endpoints filter by `event_slug`; cross-event data leakage is prevented by ORM-level scoping.
- **Scoped API keys**: REST API credentials carry explicit `WRITE_EVENTS`, `WRITE_JUDGING`, `WRITE_WEBHOOKS` scopes. Revocation is immediate and logged.

### Data Confidentiality

- **Judging isolation**: Private judge scores, per-judge breakdowns, and unpublished results are never included in public projections, embed galleries, or portable exports.
- **Credential payloads**: Signed credential JSON envelopes exclude password hashes, private keys, internal IDs, private comments, and email addresses.
- **Embed gallery**: Dynamically filters to published, eligible, non-draft projects. Drafts, duplicates, disqualified projects, and judge metadata are excluded from both initial HTML and JSON state.
- **Community votes**: Vote counts and ballot results are not disclosed before organiser publication. All public paths (HTML, API, preload data, embeds) are screened.

### Network Privacy

- **IP address handling**: Raw IP addresses are never stored for community voting and abuse detection. HMAC-SHA256 keyed digests (`ROTATING_NETWORK_KEY`) are used instead.
- **Webhook SSRF protection**: Webhook delivery enforces an allowlist of permitted destination hostnames. Private/loopback addresses are rejected.

### Signing Keys & Credentials

- **Ed25519 key pairs**: Certificate signing uses Ed25519 (via `cryptography` library). Private keys are stored in the PostgreSQL database and never exported.
- **Key rotation**: Keys can be rotated via the key management API. Old keys remain in `trusted_keys` for offline verification of previously issued credentials.
- **Key compromise**: Compromised keys can be immediately revoked, invalidating all credentials signed by them.
- **Deterministic payloads**: Credential JSON is canonicalised before signing to prevent signature malleability.

### File & Archive Security

- **Upload validation**: Media uploads enforce MIME type checks and size limits.
- **Portable archive validation**: Imported ZIP archives are rejected if they contain path traversal (`..`), absolute paths, symlinks, nested archives (outside `media/`), or files not declared in `manifest.json` with matching SHA-256 hashes.
- **Decompression limits**: Max 200 MB compressed, 1 GB uncompressed, 10,000 entries — enforced before any database writes.

### Rate Limiting

All rate limits are enforced atomically via PostgreSQL `RateLimitBucket` rows, shared across all application workers:

| Action | Limit | Window |
|---|---|---|
| Vote transitions | 60 | 60 seconds |
| New comments (burst) | 5 | 60 seconds |
| New comments (daily) | 50 | 24 hours |
| Email challenge (per address) | 3 | 1 hour |
| Content reports | 10 | 1 hour |
| Invalid link redemptions | 10 | 10 minutes |

### Content Security

- **CSP framing**: Embed gallery routes set `Content-Security-Policy: frame-ancestors <origins>` dynamically from organiser-configured allowed parent origins. Non-embed routes use `X-Frame-Options: DENY`.
- **Formula injection**: Exported CSV and portable archive text fields are sanitised against spreadsheet formula injection (`=`, `+`, `-`, `@` prefix escaping).
- **Plain-text comments**: Comment bodies are stored as plain text only. No HTML or Markdown is rendered in comments to prevent XSS.

### Audit Log

All security-relevant actions write an immutable `AuditEvent` row:

- Session creation and deletion
- Role assignments and changes
- Submission deadline enforcement
- Moderation decisions (with reason and actor)
- Key rotation and revocation
- Credential issuance and revocation
- API credential creation and revocation
- Webhook delivery attempts

Audit records are append-only and cannot be modified or deleted through the application layer.

---

## Reporting a Vulnerability

This is a competition submission. If you find a security issue, open an issue in the repository or contact the project maintainer directly. We aim to respond within 48 hours.

---

## Production Hardening Checklist

Before deploying in production:

- [ ] Set a strong, random `DJANGO_SECRET_KEY` (min 50 chars, cryptographically random)
- [ ] Set `DJANGO_DEBUG=False`
- [ ] Change default PostgreSQL password
- [ ] Set `ALLOWED_HOSTS` to your actual domain(s)
- [ ] Configure TLS termination at the reverse proxy (Nginx/Caddy)
- [ ] Set `SESSION_COOKIE_SECURE=True` and `CSRF_COOKIE_SECURE=True`
- [ ] Configure `ROTATING_NETWORK_KEY` to a strong random value
- [ ] Review and configure SMTP for email verification features
- [ ] Set up regular database and media backups
- [ ] Monitor application logs for anomalous patterns
