"""Minimal Django settings for running aa_intel_watcher's test suite without
a full Alliance Auth install.

Run from the repo root:

    python -m django test --settings=tests.settings
    # or
    python manage.py test --settings=tests.settings   (inside a Django project)

This intentionally uses stock django.contrib.auth - Alliance Auth's User
model is a superset of it, so permission checks behave the same way.
"""

SECRET_KEY = "test-only-secret-key"
DEBUG = True
USE_TZ = True
ALLOWED_HOSTS = ["*"]

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}

INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "aa_intel_watcher",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
]

ROOT_URLCONF = "tests.urls"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

INTEL_WATCHER_MEDIAMTX_SECRET = "test-webhook-secret"
INTEL_WATCHER_HLS_BASE_URL = "/hls"

PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
