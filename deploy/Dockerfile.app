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

# Railway injects PORT at runtime — default to 8000 for local docker compose
EXPOSE 8000

# railway.toml overrides this start command with $PORT substitution for Railway.
# docker-compose.yml uses this default CMD (port 8000).
CMD ["sh", "-c", "python src/backend/manage.py migrate && python src/backend/manage.py seed_demo --output-toml && gunicorn --bind 0.0.0.0:${PORT:-8000} --workers 3 --chdir src/backend config.wsgi:application"]
