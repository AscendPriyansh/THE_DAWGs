FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src/backend

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    libpq-dev \
    gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Collect static files at build time
RUN python src/backend/manage.py collectstatic --noinput --settings=config.settings 2>/dev/null || true

# Railway injects PORT at runtime — default to 8000 for local docker compose
EXPOSE 8000

# Start: migrate then gunicorn. seed_demo is optional (run manually via Railway shell).
# Uses ${PORT:-8000} so Railway's dynamic port works, while local docker-compose uses 8000.
CMD ["sh", "-c", "python src/backend/manage.py migrate --noinput && gunicorn --bind 0.0.0.0:${PORT:-8000} --workers 2 --timeout 120 --chdir src/backend config.wsgi:application"]
