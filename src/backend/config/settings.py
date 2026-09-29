import os
import hashlib
import base64
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get(
    "DJANGO_SECRET_KEY", "dogfood-insecure-dev-secret-key-replace-in-production-2026"
)

DEBUG = os.environ.get("DJANGO_DEBUG", "True").lower() in ("true", "1", "yes")

ALLOWED_HOSTS = ["*"]
CSRF_TRUSTED_ORIGINS = [
    h for h in os.environ.get("CSRF_TRUSTED_ORIGINS", "").split(",") if h.strip()
]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "apps.accounts",
    "apps.events",
    "apps.teams",
    "apps.media_assets",
    "apps.submissions",
    "apps.judging",
    "apps.results",
    "apps.audit",
    "apps.imports",
    "apps.voting",
    "apps.integrations",
    "apps.credentials",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

# ── Database ──────────────────────────────────────────────────────────────────
# Support both Railway-style DATABASE_URL and individual env vars
_database_url = os.environ.get("DATABASE_URL", "")
if _database_url:
    # Parse DATABASE_URL: postgres://user:pass@host:port/dbname
    import urllib.parse
    _parsed = urllib.parse.urlparse(_database_url)
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": urllib.parse.unquote(_parsed.path.lstrip("/")),
            "USER": urllib.parse.unquote(_parsed.username or ""),
            "PASSWORD": urllib.parse.unquote(_parsed.password or ""),
            "HOST": _parsed.hostname,
            "PORT": str(_parsed.port or 5432),
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": os.environ.get("POSTGRES_DB", "dogfood"),
            "USER": os.environ.get("POSTGRES_USER", "dogfood"),
            "PASSWORD": os.environ.get("POSTGRES_PASSWORD", "dogfood"),
            "HOST": os.environ.get("POSTGRES_HOST", "localhost"),
            "PORT": os.environ.get("POSTGRES_PORT", "5432"),
        }
    }

AUTH_USER_MODEL = "accounts.User"

SESSION_ENGINE = "django.contrib.sessions.backends.db"
SESSION_COOKIE_NAME = "session"
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 8}},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
# Only add STATICFILES_DIRS if the directory actually exists (avoids errors on fresh containers)
_static_dir = BASE_DIR / "static"
if _static_dir.exists():
    STATICFILES_DIRS = [_static_dir]
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_STORAGE = "whitenoise.storage.CompressedManifestStaticFilesStorage"

MEDIA_URL = "/media/"
MEDIA_ROOT = os.environ.get("MEDIA_ROOT", str(BASE_DIR / "media"))

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "apps.integrations.authentication.ApiKeyAuthentication",
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.AllowAny",
    ],
    "DEFAULT_RENDERER_CLASSES": [
        "rest_framework.renderers.JSONRenderer",
    ],
}

# ── Webhook & Worker Configuration ────────────────────────────────────────────
WEBHOOK_ENCRYPTION_KEY = os.environ.get("DOGFOOD_WEBHOOK_ENCRYPTION_KEY")
if not WEBHOOK_ENCRYPTION_KEY:
    _digest = hashlib.sha256(f"webhook-secret-salt:{SECRET_KEY}".encode("utf-8")).digest()
    WEBHOOK_ENCRYPTION_KEY = base64.urlsafe_b64encode(_digest).decode("ascii")

_raw_allowed_hosts = os.environ.get("DOGFOOD_ALLOWED_WEBHOOK_HOSTS", "testserver,localhost,127.0.0.1")
DOGFOOD_ALLOWED_WEBHOOK_HOSTS = [h.strip() for h in _raw_allowed_hosts.split(",") if h.strip()]

WEBHOOK_TIMEOUT_CONNECT = int(os.environ.get("DOGFOOD_WEBHOOK_TIMEOUT_CONNECT", "5"))
WEBHOOK_TIMEOUT_READ = int(os.environ.get("DOGFOOD_WEBHOOK_TIMEOUT_READ", "10"))
WEBHOOK_MAX_RESPONSE_BYTES = int(os.environ.get("DOGFOOD_WEBHOOK_MAX_RESPONSE_BYTES", "65536"))
WEBHOOK_LEASE_DURATION_SECONDS = int(os.environ.get("DOGFOOD_WEBHOOK_LEASE_DURATION_SECONDS", "60"))
