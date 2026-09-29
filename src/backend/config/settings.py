import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get(
    "DJANGO_SECRET_KEY", "dogfood-insecure-dev-secret-key-replace-in-production-2026"
)

DEBUG = os.environ.get("DJANGO_DEBUG", "True").lower() in ("true", "1", "yes")

ALLOWED_HOSTS = ["*"]

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

# Database: PostgreSQL
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
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

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

# Webhook & Worker Configuration (M08)
import hashlib
import base64

WEBHOOK_ENCRYPTION_KEY = os.environ.get("DOGFOOD_WEBHOOK_ENCRYPTION_KEY")
if not WEBHOOK_ENCRYPTION_KEY:
    # Derive a stable 32-byte urlsafe base64 key from SECRET_KEY
    _digest = hashlib.sha256(f"webhook-secret-salt:{SECRET_KEY}".encode("utf-8")).digest()
    WEBHOOK_ENCRYPTION_KEY = base64.urlsafe_b64encode(_digest).decode("ascii")

# Allowed webhook hosts for local/offline testing or demo receivers
_raw_allowed_hosts = os.environ.get("DOGFOOD_ALLOWED_WEBHOOK_HOSTS", "testserver,localhost,127.0.0.1")
DOGFOOD_ALLOWED_WEBHOOK_HOSTS = [h.strip() for h in _raw_allowed_hosts.split(",") if h.strip()]

WEBHOOK_TIMEOUT_CONNECT = int(os.environ.get("DOGFOOD_WEBHOOK_TIMEOUT_CONNECT", "5"))
WEBHOOK_TIMEOUT_READ = int(os.environ.get("DOGFOOD_WEBHOOK_TIMEOUT_READ", "10"))
WEBHOOK_MAX_RESPONSE_BYTES = int(os.environ.get("DOGFOOD_WEBHOOK_MAX_RESPONSE_BYTES", "65536"))
WEBHOOK_LEASE_DURATION_SECONDS = int(os.environ.get("DOGFOOD_WEBHOOK_LEASE_DURATION_SECONDS", "60"))
