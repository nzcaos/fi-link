"""Django settings for Fichtelink — Phase 0 scaffold.

Settings are read from environment variables. In the docker-compose setup the
env_file directive loads `.env` into every service; for local development set
the variables yourself or use a tool like direnv.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from cryptography.fernet import Fernet

BASE_DIR = Path(__file__).resolve().parent.parent


def _csv_env(name: str, default: str = "") -> list[str]:
    return [item.strip() for item in os.environ.get(name, default).split(",") if item.strip()]


SECRET_KEY = os.environ["DJANGO_SECRET_KEY"]
DEBUG = os.environ.get("DJANGO_DEBUG", "False").lower() in ("1", "true", "yes", "on")

ALLOWED_HOSTS = _csv_env("ALLOWED_HOSTS")
CSRF_TRUSTED_ORIGINS = _csv_env("CSRF_TRUSTED_ORIGINS")

# Reverse-proxy boundary: TLS is terminated on a separate Apache host that
# forwards plain HTTP into the web container. See CLAUDE.md / README.md.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
USE_X_FORWARDED_HOST = True

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Third-party
    "procrastinate.contrib.django",
    # Fichtelink apps
    "accounts",
    "lists",
    "forms",
    "matrix",
]

AUTH_USER_MODEL = "accounts.User"

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

ROOT_URLCONF = "fichtelink.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "matrix.context_processors.matrix_flags",
                "fichtelink.context_processors.navigation",
            ],
        },
    },
]

WSGI_APPLICATION = "fichtelink.wsgi.application"
ASGI_APPLICATION = "fichtelink.asgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("POSTGRES_DB", "fichtelink"),
        "USER": os.environ.get("POSTGRES_USER", "fichtelink"),
        "PASSWORD": os.environ["POSTGRES_PASSWORD"],
        "HOST": os.environ.get("POSTGRES_HOST", "db"),
        "PORT": os.environ.get("POSTGRES_PORT", "5432"),
    }
}

AUTH_PASSWORD_VALIDATORS: list[dict] = []

LANGUAGE_CODE = "de-de"
TIME_ZONE = "Europe/Berlin"
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}

# When running `manage.py test`, fall back to the plain staticfiles storage.
# The manifest-backed one demands a `collectstatic`-generated entry for every
# {% static %} reference; without that, any view that renders a template using
# a freshly-added asset crashes with "Missing staticfiles manifest entry".
# Tests don't run collectstatic, so plain storage is the right choice there.
if "test" in sys.argv:
    STORAGES["staticfiles"]["BACKEND"] = (
        "django.contrib.staticfiles.storage.StaticFilesStorage"
    )

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Installation-wide Fernet key for at-rest encryption of LIST_RECORD_VALUE.
# Validated eagerly so a malformed key fails startup, not the first write.
FERNET_KEY = os.environ["FERNET_KEY"]
Fernet(FERNET_KEY.encode())
CRYPTOGRAPHY_KEY = FERNET_KEY  # consumed by django-cryptography (wired in Phase 1)

# WebAuthn relying-party identity (Phase 2). Must match the browser-visible host.
RP_ID = os.environ.get("RP_ID", "localhost")
RP_ORIGIN = os.environ.get("RP_ORIGIN", "http://localhost:8000")
RP_NAME = os.environ.get("RP_NAME", "Fichtelink")

# Mail backend for activation links. Console backend by default — production must
# set DJANGO_EMAIL_BACKEND to django.core.mail.backends.smtp.EmailBackend and the
# SMTP_* variables.
EMAIL_BACKEND = os.environ.get(
    "DJANGO_EMAIL_BACKEND", "django.core.mail.backends.console.EmailBackend"
)
DEFAULT_FROM_EMAIL = os.environ.get(
    "DJANGO_DEFAULT_FROM_EMAIL", "Fichtelink <noreply@example.com>"
)
EMAIL_HOST = os.environ.get("SMTP_HOST", "")
EMAIL_PORT = int(os.environ.get("SMTP_PORT", "587"))
EMAIL_HOST_USER = os.environ.get("SMTP_USER", "")
EMAIL_HOST_PASSWORD = os.environ.get("SMTP_PASS", "")
# STARTTLS (port 587) and implicit TLS (port 465) are mutually exclusive in
# Django — setting both True raises at connect time. SMTP_USE_SSL wins: if it is
# on, STARTTLS is forced off, so switching to a 465 endpoint means flipping one
# variable instead of remembering to also turn SMTP_USE_TLS off.
EMAIL_USE_SSL = os.environ.get("SMTP_USE_SSL", "False").lower() in ("1", "true", "yes", "on")
EMAIL_USE_TLS = (
    not EMAIL_USE_SSL
    and os.environ.get("SMTP_USE_TLS", "True").lower() in ("1", "true", "yes", "on")
)
# Bound every SMTP socket operation so a black-holed or wrong-mode endpoint
# fails fast (retryable) instead of hanging the worker indefinitely.
EMAIL_TIMEOUT = int(os.environ.get("SMTP_TIMEOUT", "30"))

# Mail domain used to construct list aliases (`<email_alias>@<MAIL_DOMAIN>`) and
# the alias/bounce tokens used in Phase 4 outbound. Tests fall back to a literal.
MAIL_DOMAIN = os.environ.get("MAIL_DOMAIN", "example.invalid")

# IMAP IDLE consumer (Phase 5a). Catch-all mailbox credentials and tunables.
IMAP_HOST = os.environ.get("IMAP_HOST", "")
IMAP_PORT = int(os.environ.get("IMAP_PORT", "993"))
IMAP_USER = os.environ.get("IMAP_USER", "")
IMAP_PASS = os.environ.get("IMAP_PASS", "")
IMAP_USE_SSL = os.environ.get("IMAP_USE_SSL", "True").lower() in ("1", "true", "yes", "on")
# Re-IDLE before the server's 30-minute kick (RFC 2177 recommendation).
IMAP_IDLE_TIMEOUT = int(os.environ.get("IMAP_IDLE_TIMEOUT", str(29 * 60)))
# Fallback poll: while IDLE'ing, wake every N seconds to catch missed
# notifications (network glitches, NAT timeouts, half-broken proxies).
IMAP_FALLBACK_POLL_INTERVAL = int(
    os.environ.get("IMAP_FALLBACK_POLL_INTERVAL", str(5 * 60))
)

# Phase 5b retention (CLAUDE.md / "Retention").
# IMAP EXPUNGE grace: processed (\Seen) catch-all mail is deleted from the
# server this many days after receipt — long enough that an operator can still
# inspect a raw message on the IMAP side during that window.
IMAP_EXPUNGE_GRACE_DAYS = int(os.environ.get("IMAP_EXPUNGE_GRACE_DAYS", "7"))
# Local Inbound/Outbound metadata retention: rows older than this are pruned
# by a periodic task. Kept 30–90 days for bounce correlation; default 90.
MAIL_METADATA_RETENTION_DAYS = int(
    os.environ.get("MAIL_METADATA_RETENTION_DAYS", "90")
)

# --- Matrix messenger integration (docs/matrix-implementation-plan.md) ---
# OFF by default so tests/CI and any pre-Phase-0 deploy never touch Matrix.
# Flip MATRIX_ENABLED on only after Synapse answers and the federation tester
# is green. Every Matrix code path is guarded by this flag.
MATRIX_ENABLED = os.environ.get("MATRIX_ENABLED", "False").lower() in ("1", "true", "yes", "on")
# Synapse is reachable ONLY inside the Docker network — 8008/8448 are never
# exposed publicly. Both the admin API and the client-server API go here.
MATRIX_BASE_URL = os.environ.get("MATRIX_BASE_URL", "http://synapse:8008")
# Unchangeable once Synapse is live: determines the @user:<server_name> id form.
MATRIX_SERVER_NAME = os.environ.get("MATRIX_SERVER_NAME", "fichtelink.caos.cloud")
# = homeserver.yaml `registration_shared_secret`. Drives the admin
# shared-secret registration API. NEVER logged, NEVER committed.
MATRIX_ADMIN_SHARED_SECRET = os.environ.get("MATRIX_ADMIN_SHARED_SECRET", "")
# Bound every Synapse HTTP op so a wedged homeserver fails fast (retryable in a
# procrastinate task) instead of hanging the worker.
MATRIX_HTTP_TIMEOUT = int(os.environ.get("MATRIX_HTTP_TIMEOUT", "30"))

LOGIN_URL = "/auth/login/"
# Land authenticated users straight on a list view, not a separate landing
# page (CLAUDE.md / *Post-login landing*). `lists:home` resolves which list.
# Kept as a path string (not a URL name) because login_finish echoes it back
# to the browser as a JSON redirect target.
LOGIN_REDIRECT_URL = "/lists/home/"
LOGOUT_REDIRECT_URL = "/"

# Logging: Django's default root logger is WARNING, which silently drops
# every log.info() from our own modules — including the IMAP daemon's
# `IMAP connected: ...` and `inbound persisted: ...` lines, which is how
# the operator confirms the daemon is alive. Bring our apps and the root
# logger to INFO on a StreamHandler so `docker compose logs` shows
# what's happening. Framework chatter that's noisy at INFO stays at
# WARNING (django.db.backends echoes every query, etc.). Phase 8 may
# tighten this further (file logging, structured JSON, etc.); for now
# console-INFO is the right floor.
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "simple": {
            "format": "[{asctime}] {levelname} {name}: {message}",
            "style": "{",
            "datefmt": "%Y-%m-%d %H:%M:%S",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "simple",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": "INFO",
    },
    "loggers": {
        "django.db.backends": {"level": "WARNING", "propagate": True},
        "django.utils.autoreload": {"level": "WARNING", "propagate": True},
        "asyncio": {"level": "WARNING", "propagate": True},
        # aioimaplib is chatty at DEBUG; leave it at WARNING so only
        # surprising server responses surface.
        "aioimaplib": {"level": "WARNING", "propagate": True},
    },
}
