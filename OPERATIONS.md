# Operations Guide

## Service Architecture

The application consists of three Docker Compose services:

| Service | Image | Role |
|---|---|---|
| `db` | `postgres:16-alpine` | Primary PostgreSQL database |
| `app` | `deploy/Dockerfile.app` | Django web application (Gunicorn, 3 workers) |
| `worker` | `deploy/Dockerfile.app` | Background job worker (PostgreSQL outbox poller) |

All services share a `media` volume for user-uploaded files. PostgreSQL data persists in a `pgdata` named volume.

---

## Starting and Stopping

```bash
# Start all services (build images on first run)
docker compose up --build -d

# View logs
docker compose logs -f app
docker compose logs -f worker

# Stop all services (preserves data volumes)
docker compose stop

# Stop and remove containers (preserves volumes)
docker compose down

# Stop and remove containers AND volumes (destroys all data)
docker compose down -v
```

---

## First-Boot Sequence

On first boot, the `app` container automatically:
1. Runs `python src/backend/manage.py migrate` — applies all database migrations
2. Runs `python src/backend/manage.py seed_demo --output-toml` — imports fixture data, creates demo users and sessions, writes `.dogfood.toml`
3. Starts Gunicorn with 3 workers on port 8000

Subsequent boots detect the existing data and skip re-seeding (idempotent).

---

## Health Checks

### Database health check

PostgreSQL uses Docker health checks with `pg_isready`:
```yaml
healthcheck:
  test: ["CMD-SHELL", "pg_isready -U dogfood -d dogfood"]
  interval: 5s
  timeout: 5s
  retries: 5
```

The `app` service waits for `db` to be `healthy` before starting.

### Application health check (manual)

```bash
curl -s http://localhost:8000/projects | head -5
# Should return HTML starting with <!doctype html>
```

### Worker health check (manual)

The worker logs each poll cycle. Check it is running:
```bash
docker compose logs worker --tail=20
```

---

## Migrations

### Apply pending migrations

```bash
docker compose exec app python src/backend/manage.py migrate
```

### Check migration status

```bash
docker compose exec app python src/backend/manage.py showmigrations
```

### Check for model changes not yet migrated

```bash
docker compose exec app python src/backend/manage.py makemigrations --check --dry-run
# "No changes detected" means all models are in sync with migrations
```

### Create new migrations after model changes

```bash
PYTHONPATH=src/backend .venv/bin/python src/backend/manage.py makemigrations <app_label>
```

---

## Backup and Restore

### Backup (recommended daily)

```bash
# Database dump
docker compose exec db pg_dump -U dogfood dogfood > backup-$(date +%Y%m%d-%H%M).sql

# Media files
docker compose cp app:/app/media ./media-backup-$(date +%Y%m%d-%H%M)
```

Store backups off-host (e.g., encrypted object storage or external drive).

### Restore procedure

```bash
# 1. Stop application services to prevent writes during restore
docker compose stop app worker

# 2. Drop and recreate the database
docker compose exec db psql -U dogfood postgres -c "DROP DATABASE dogfood;"
docker compose exec db psql -U dogfood postgres -c "CREATE DATABASE dogfood OWNER dogfood;"

# 3. Restore database
cat backup-20261001.sql | docker compose exec -T db psql -U dogfood dogfood

# 4. Restore media files
rm -rf ./media-restore-tmp
cp -r ./media-backup-20261001 ./media-restore-tmp
docker compose cp ./media-restore-tmp app:/app/media

# 5. Restart services
docker compose start app worker

# 6. Verify
curl -s http://localhost:8000/projects | head -5
```

---

## Background Worker

The worker service polls the `background_jobs` outbox table every 5 seconds using a blocking select. It handles:

| Job type | Trigger | Description |
|---|---|---|
| `export_event` | Organiser export request | Creates portable ZIP archive of event data |
| `webhook_delivery` | Domain events | Delivers webhook payloads with exponential retry |

### Worker configuration

The worker runs the `run_worker` management command:
```bash
python src/backend/manage.py run_worker
```

No Redis, Celery, or external broker is required. The worker is crash-safe — unprocessed jobs remain in the outbox and are picked up on restart.

### Monitoring worker lag

```bash
# Check for stuck jobs (pending > 5 minutes)
docker compose exec db psql -U dogfood dogfood -c \
  "SELECT id, job_type, status, created_at FROM background_jobs WHERE status='PENDING' AND created_at < NOW() - INTERVAL '5 minutes';"
```

---

## Signing Keys and Credentials

Ed25519 signing keys for certificate issuance are stored in the `signing_keys` table. To manage them:

```bash
# List active keys
curl -H "Authorization: ..." http://localhost:8000/api/v1/issuer/keys/

# Keys are created automatically when the first credential is issued
# Rotate a key via the admin or credential API
```

**Important**: Back up the database regularly — private keys are stored in PostgreSQL and cannot be recovered if the database is lost.

---

## Session Management

Sessions are stored in the `django_session` table. To force all users to re-authenticate:

```bash
docker compose exec db psql -U dogfood dogfood -c "DELETE FROM django_session;"
```

To clear expired sessions only:
```bash
docker compose exec app python src/backend/manage.py clearsessions
```

---

## Log Collection

Application logs are written to stdout/stderr, captured by Docker:

```bash
# Follow all logs
docker compose logs -f

# Filter to errors
docker compose logs app 2>&1 | grep -i error

# Export logs
docker compose logs app > app-logs-$(date +%Y%m%d).txt
```

For production, configure Docker logging drivers (e.g., `json-file` with rotation or forward to a log aggregator).

---

## Disaster Recovery Scenarios

### Database corruption

1. Stop services: `docker compose stop app worker`
2. Restore from most recent backup (see Restore procedure above)
3. Restart: `docker compose start app worker`

### Media volume loss

Media files are user uploads and generated PDFs. If the volume is lost:
1. Restore from media backup
2. Credential PDFs can be re-generated (they are deterministic from database content)
3. User-uploaded project files must be restored from backup

### Application crash loop

1. Check logs: `docker compose logs app --tail=50`
2. Common causes: missing environment variable, database connection failure, migration not applied
3. Run migrations manually: `docker compose run --rm app python src/backend/manage.py migrate`

### Worker not processing jobs

1. Check worker logs: `docker compose logs worker --tail=20`
2. Restart worker: `docker compose restart worker`
3. Check for database connectivity from worker container

---

## Performance Notes

- **Workers**: Gunicorn runs 3 synchronous workers. Increase `--workers` in `Dockerfile.app` CMD for higher concurrency.
- **Database connections**: Each Gunicorn worker holds one persistent connection. With 3 workers, peak connections = 3 (app) + 1 (worker) + headroom.
- **Media**: All uploads are stored on a local volume. For large deployments, consider a network-attached volume.
- **Static files**: Served by Django in development mode. For production, configure Nginx to serve `STATIC_ROOT` directly.
