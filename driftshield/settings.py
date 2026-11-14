import os
from pathlib import Path
from urllib.parse import urlsplit

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent
DEBUG = os.environ.get("DS_DEV") == "1"
SECRET_KEY = os.environ.get("DS_SECRET_KEY", "django-insecure-local-development-only" if DEBUG else "")
APP_ORIGIN = os.environ.get("DS_ORIGIN", "http://127.0.0.1:8000" if DEBUG else "").rstrip("/")
origin = urlsplit(APP_ORIGIN)
if not DEBUG and (len(SECRET_KEY) < 50 or origin.scheme != "https" or not origin.hostname):
    raise ImproperlyConfigured("Set a random DS_SECRET_KEY (50+ characters) and HTTPS DS_ORIGIN.")
if origin.path or origin.query or origin.fragment or origin.username:
    raise ImproperlyConfigured("DS_ORIGIN must be a scheme and hostname only.")
ALLOWED_HOSTS = [origin.hostname] + (["localhost", "testserver", "127.0.0.1"] if DEBUG else ["127.0.0.1"])
CSRF_TRUSTED_ORIGINS = [APP_ORIGIN]
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = not DEBUG
SECURE_REDIRECT_EXEMPT = [r"^healthz$"]
SECURE_HSTS_SECONDS = 31536000 if not DEBUG else 0
# The installer does not control the deployment hostname's child domains or preload status.
SILENCED_SYSTEM_CHECKS = ["security.W005", "security.W021"]
SECURE_REFERRER_POLICY = "same-origin"
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
SESSION_COOKIE_AGE = 43200
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
X_FRAME_OPTIONS = "DENY"
DATA_UPLOAD_MAX_MEMORY_SIZE = 32768
DATA_UPLOAD_MAX_NUMBER_FIELDS = 100

INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "governance",
    "infrastructure",
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
    "driftshield.middleware.response_policy",
]
ROOT_URLCONF = "driftshield.urls"
TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates",
    "DIRS": [BASE_DIR / "templates"],
    "APP_DIRS": True,
    "OPTIONS": {"context_processors": [
        "django.template.context_processors.request",
        "django.contrib.auth.context_processors.auth",
        "django.contrib.messages.context_processors.messages",
    ]},
}]
WSGI_APPLICATION = "driftshield.wsgi.application"
STATIC_URL = "/static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}
if DEBUG:
    STORAGES["staticfiles"] = {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}
if os.environ.get("DS_DB_NAME"):
    DATABASES = {"default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ["DS_DB_NAME"],
        "USER": os.environ.get("DS_DB_USER", "driftshield"),
        "PASSWORD": os.environ.get("DS_DB_PASSWORD", ""),
        "HOST": os.environ.get("DS_DB_HOST", "127.0.0.1"),
        "PORT": os.environ.get("DS_DB_PORT", "5432"),
        "OPTIONS": {"sslmode": os.environ.get("DS_DB_SSLMODE", "prefer"), "connect_timeout": 5},
    }}
elif DEBUG:
    DATABASES = {"default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": os.environ.get("DS_SQLITE", BASE_DIR / "local.sqlite3"),
        "OPTIONS": {"transaction_mode": "IMMEDIATE", "timeout": 15},
    }}
else:
    raise ImproperlyConfigured("Production requires PostgreSQL. Set DS_DB_NAME.")
PASSWORD_HASHERS = ["django.contrib.auth.hashers.PBKDF2PasswordHasher"]
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 14}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
LOGIN_URL = "/login/"
LOGIN_REDIRECT_URL = "/"
LOGOUT_REDIRECT_URL = "/login/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
USE_TZ = True
TIME_ZONE = "UTC"

EMAIL_HOST = os.environ.get("DS_SMTP_HOST", "")
EMAIL_PORT = int(os.environ.get("DS_SMTP_PORT", "587"))
EMAIL_HOST_USER = os.environ.get("DS_SMTP_USER", "")
EMAIL_HOST_PASSWORD = os.environ.get("DS_SMTP_PASSWORD", "")
EMAIL_USE_TLS = os.environ.get("DS_SMTP_TLS", "1") == "1"
EMAIL_TIMEOUT = 12
DEFAULT_FROM_EMAIL = os.environ.get("DS_FROM_EMAIL", "DriftShield <driftshield@localhost>")
GITHUB_KEY = os.environ.get("DS_GITHUB_ENCRYPTION_KEY", "")
