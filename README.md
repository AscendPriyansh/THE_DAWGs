# THE_DAWGs Hackathon Portal

A self-hostable hackathon submission and judging portal built with Django, PostgreSQL, and a clean template-rendered frontend. Supports full participant, judge, and organiser workflows with a REST API, webhooks, signed verifiable credentials, community voting, and portable bulk import/export.

---

## Quick Start (Local Development)

### Prerequisites

- Python 3.12+
- PostgreSQL 16+ running locally
- `pip`

### 1. Clone and set up

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure PostgreSQL

Create the database and user (password: `dogfood`):

```bash
createdb dogfood
createuser dogfood --password
psql -c "GRANT ALL PRIVILEGES ON DATABASE dogfood TO dogfood;"
```

### 3. Apply migrations and seed

```bash
PYTHONPATH=src/backend python src/backend/manage.py migrate
PYTHONPATH=src/backend python src/backend/manage.py seed_demo --output-toml
```

`seed_demo` imports the fixture data, creates demo users, generates real session cookies, and writes them to `.dogfood.toml`.

### 4. Run the development server

```bash
PYTHONPATH=src/backend python src/backend/manage.py runserver
```

Portal is now available at **http://localhost:8000**

---

## Demo Login Credentials

After running `seed_demo`, the following accounts are available (password: `dogfoodpass123`):

| Role | Email |
|---|---|
| Organiser | organizer@example.org |
| Judge A | tomas.varga@example.org |
| Judge B | mariana.costa@example.org |
| Participant | priya1@example.org |

Login at: http://localhost:8000/login

---

## Docker Compose Deployment

```bash
docker compose up --build
```

This starts:
- **`db`** — PostgreSQL 16 on port 5432
- **`app`** — Django app on port 8000 (runs migrations + seed automatically)
- **`worker`** — Background job worker for async exports and webhooks

### Environment Variables

| Variable | Default | Description |
|---|---|---|
| `DJANGO_SECRET_KEY` | insecure dev key | **Change in production** |
| `DJANGO_DEBUG` | `True` | Set to `False` in production |
| `POSTGRES_DB` | `dogfood` | Database name |
| `POSTGRES_USER` | `dogfood` | Database user |
| `POSTGRES_PASSWORD` | `dogfood` | **Change in production** |
| `POSTGRES_HOST` | `db` (Docker) | PostgreSQL hostname |
| `POSTGRES_PORT` | `5432` | PostgreSQL port |

### Offline Operation

Once images are built (`docker compose build`), the application runs fully offline — no external API calls, CDN requests, or cloud services are required.

---

## Migration and Backup

### Run migrations

```bash
# Local dev
PYTHONPATH=src/backend python src/backend/manage.py migrate

# Docker
docker compose exec app python src/backend/manage.py migrate
```

### Check for pending migrations

```bash
PYTHONPATH=src/backend python src/backend/manage.py makemigrations --check --dry-run
# Outputs: "No changes detected" when all models are in sync
```

### Backup

```bash
# Database
docker compose exec db pg_dump -U dogfood dogfood > backup-$(date +%Y%m%d).sql

# Media files
docker compose cp app:/app/media ./media-backup-$(date +%Y%m%d)
```

### Restore

```bash
docker compose stop app worker
cat backup.sql | docker compose exec -T db psql -U dogfood dogfood
docker compose cp ./media-backup app:/app/media
docker compose start app worker
```

---

## Running Tests

```bash
# Full integration test suite
PYTHONPATH=src/backend .venv/bin/pytest tests/

# Run acceptance checker (requires running server at localhost:8000)
python3 tools/run.py .dogfood.toml
```

---

## Claimed Tiers

This portal implements **T1, T2, T3, and T4** with independent evidence:

- `tests/integration/` — 11 test modules, 96 tests covering all milestones M01–M11
- `REQUIREMENTS-MATRIX.md` — full mapping of spec requirements to implemented features
- `SECURITY.md` — security model, threat mitigations, and configuration guide
- `OPERATIONS.md` — operational runbook: backup, restore, migrations, monitoring

---

## Known Limits

- **Email**: SMTP is optional. Email verification OTPs are logged to stdout when SMTP is not configured.
- **Worker polling**: Background worker polls PostgreSQL outbox every 5 seconds. No Redis or external broker required.
- **Import archive limits**: Max 200 MB compressed, 1 GB uncompressed, 10,000 entries.
- **Signing keys**: Ed25519 private keys are stored in PostgreSQL — back them up with the database.
- **Python version**: Tested on Python 3.12. Docker image uses `python:3.12-slim`.

---

## Project Structure

```
.
├── src/backend/           # Django application
│   ├── config/            # Settings, URLs, WSGI
│   └── apps/
│       ├── accounts/      # Custom User model, sessions, rate limiting
│       ├── events/        # Events, tracks, prizes, memberships
│       ├── teams/         # Teams, invitations, roster management
│       ├── submissions/   # Projects, revisions, assets, deadlines
│       ├── judging/       # Rubrics, assignments, reviews, scores
│       ├── results/       # Normalisation, result runs, publications
│       ├── voting/        # Community voting, ballots, gates
│       ├── audit/         # Immutable audit log
│       ├── imports/       # Fixture importer, seed, portable archive
│       ├── integrations/  # REST API, webhooks, outbox worker
│       ├── credentials/   # Certificates, signing keys, verification
│       └── media_assets/  # File upload management
├── tests/integration/     # Integration test suite (M01–M11)
├── deploy/Dockerfile.app  # Production Docker image
├── tools/run.py           # Acceptance checker
├── fixtures/              # Supplied fixture data
├── docker-compose.yml     # Full stack: db + app + worker
├── REQUIREMENTS-MATRIX.md # Spec-to-implementation mapping
├── SECURITY.md            # Security model and configuration
├── OPERATIONS.md          # Operational runbook
└── requirements.txt
```

---

## License

MIT — see [LICENSE](LICENSE).
