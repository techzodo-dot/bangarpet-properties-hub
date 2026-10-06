"""
Base settings shared by every environment.

All secrets and environment-specific values are read from environment
variables (optionally loaded from a local ``.env`` file). Never hardcode
credentials here.
"""
import os
from pathlib import Path

import dj_database_url
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent.parent

load_dotenv(BASE_DIR / ".env")


def env(name, default=None):
    return os.environ.get(name, default)


def env_bool(name, default=False):
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_int(name, default=0):
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def env_list(name, default=""):
    return [item.strip() for item in os.environ.get(name, default).split(",") if item.strip()]


SECRET_KEY = env("SECRET_KEY", "")
DEBUG = env_bool("DEBUG", False)
ALLOWED_HOSTS = env_list("ALLOWED_HOSTS", "localhost,127.0.0.1")
CSRF_TRUSTED_ORIGINS = env_list("CSRF_TRUSTED_ORIGINS", "")
SITE_URL = env("SITE_URL", "http://localhost:8000").rstrip("/")

INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
    "django.contrib.sitemaps",
    # Project apps
    "core",
    "accounts",
    "properties",
    "enquiries",
    "subscriptions",
    "payments",
    "notifications",
    "moderation",
    "dashboard",
    "adminpanel",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "core.middleware.SecurityHeadersMiddleware",
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
                "django.template.context_processors.i18n",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "core.context_processors.platform",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# ---------------------------------------------------------------------------
# Database: SQLite by default, any DATABASE_URL (e.g. PostgreSQL) when set.
# ---------------------------------------------------------------------------
DATABASES = {
    "default": dj_database_url.parse(
        env("DATABASE_URL") or f"sqlite:///{BASE_DIR / 'db.sqlite3'}",
        conn_max_age=60,
    )
}
if DATABASES["default"]["ENGINE"] == "django.db.backends.sqlite3":
    # Wait for locks instead of failing immediately under light concurrency.
    DATABASES["default"].setdefault("OPTIONS", {})["timeout"] = 20
    DATABASES["default"]["CONN_MAX_AGE"] = 0

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

AUTH_USER_MODEL = "accounts.User"
AUTHENTICATION_BACKENDS = ["accounts.backends.EmailBackend"]
LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "accounts:post_login"
LOGOUT_REDIRECT_URL = "core:home"

PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2SHA1PasswordHasher",
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.BCryptSHA256PasswordHasher",
    "django.contrib.auth.hashers.ScryptPasswordHasher",
]

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 8}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

PASSWORD_RESET_TIMEOUT = 60 * 60 * 2  # 2 hours

LANGUAGE_CODE = "en"
# Customers can switch the public site between English and Kannada.
LANGUAGES = [("en", "English"), ("kn", "ಕನ್ನಡ")]
LOCALE_PATHS = [BASE_DIR / "locale"]
LANGUAGE_COOKIE_NAME = "bph_language"
LANGUAGE_COOKIE_AGE = 60 * 60 * 24 * 365
TIME_ZONE = "Asia/Kolkata"
USE_I18N = True
USE_TZ = True

# ---------------------------------------------------------------------------
# Static & media files
# ---------------------------------------------------------------------------
STATIC_URL = "/static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = Path(env("STATIC_ROOT") or BASE_DIR / "staticfiles")

MEDIA_URL = "/media/"
MEDIA_ROOT = Path(env("MEDIA_ROOT") or BASE_DIR / "media")

# Serve public media through Django only when the web server cannot (e.g. shared
# hosting without an alias for /media/). Prefer serving /media/ from Nginx/Apache.
SERVE_MEDIA = env_bool("SERVE_MEDIA", False)

# Verification documents live OUTSIDE MEDIA_ROOT and are never served directly.
PRIVATE_MEDIA_ROOT = Path(env("PRIVATE_MEDIA_ROOT") or BASE_DIR / "private_media")
# Dotted path of an alternative private storage class (e.g. core.storage.DatabaseStorage).
PRIVATE_FILE_STORAGE = env("PRIVATE_FILE_STORAGE", "")

STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}

# Upload limits
FILE_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024
DATA_UPLOAD_MAX_MEMORY_SIZE = 30 * 1024 * 1024
DATA_UPLOAD_MAX_NUMBER_FILES = 25
FILE_UPLOAD_PERMISSIONS = 0o640
MAX_IMAGE_UPLOAD_MB = 5
MAX_DOCUMENT_UPLOAD_MB = 5
MAX_IMAGES_PER_PROPERTY = 20

# ---------------------------------------------------------------------------
# Sessions & security defaults (hardened further in production settings)
# ---------------------------------------------------------------------------
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_AGE = 60 * 60 * 24 * 14
CSRF_COOKIE_SAMESITE = "Lax"
X_FRAME_OPTIONS = "DENY"
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"
SECURE_CROSS_ORIGIN_OPENER_POLICY = "same-origin-allow-popups"

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "bph-default",
    }
}

MESSAGE_TAGS = {40: "danger"}

# ---------------------------------------------------------------------------
# Email
# ---------------------------------------------------------------------------
EMAIL_HOST = env("EMAIL_HOST", "")
EMAIL_PORT = env_int("EMAIL_PORT", 587)
EMAIL_HOST_USER = env("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = env_bool("EMAIL_USE_TLS", True)
EMAIL_TIMEOUT = 10
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", "Bangarpet Property Hub <no-reply@localhost>")
SERVER_EMAIL = DEFAULT_FROM_EMAIL
if EMAIL_HOST:
    EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
else:
    EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
# True only when a real SMTP server is configured; used for honest status reporting.
EMAIL_DELIVERY_CONFIGURED = bool(EMAIL_HOST)

# One-time codes sent by email: sign-up verification, sign-in with a code,
# password reset and the admin 2-step sign-in. Codes are only used once an SMTP
# server is configured (e.g. Gmail with an app password), so nobody is locked
# out while email is not set up.
EMAIL_OTP_ENABLED = env_bool("EMAIL_OTP_ENABLED", True)
# Admin logins such as "bph@admin" are not real mailboxes: their 2-step codes go here.
ADMIN_OTP_EMAIL = env("ADMIN_OTP_EMAIL", "").strip()

# ---------------------------------------------------------------------------
# Third-party integrations (all optional; features degrade gracefully)
# ---------------------------------------------------------------------------
GOOGLE_MAPS_API_KEY = env("GOOGLE_MAPS_API_KEY", "")

RAZORPAY_KEY_ID = env("RAZORPAY_KEY_ID", "")
RAZORPAY_KEY_SECRET = env("RAZORPAY_KEY_SECRET", "")
RAZORPAY_WEBHOOK_SECRET = env("RAZORPAY_WEBHOOK_SECRET", "")
RAZORPAY_API_BASE = "https://api.razorpay.com/v1"

WHATSAPP_ACCESS_TOKEN = env("WHATSAPP_ACCESS_TOKEN", "")
WHATSAPP_PHONE_NUMBER_ID = env("WHATSAPP_PHONE_NUMBER_ID", "")
WHATSAPP_API_VERSION = env("WHATSAPP_API_VERSION", "v20.0")
WHATSAPP_TEMPLATE_LANGUAGE = env("WHATSAPP_TEMPLATE_LANGUAGE", "en")

VERIFICATION_DOC_RETENTION_DAYS = env_int("VERIFICATION_DOC_RETENTION_DAYS", 180)

# ---------------------------------------------------------------------------
# Rate limits: (max attempts, window seconds)
# ---------------------------------------------------------------------------
RATE_LIMITS = {
    "login": (5, 15 * 60),
    "register": (10, 60 * 60),
    "password_reset": (5, 60 * 60),
    "contact_unlock": (30, 60 * 60),  # owner contacts unlocked per customer
    "otp_send": (5, 60 * 60),       # codes emailed per account
    "otp_request": (10, 60 * 60),   # code requests per network
    "otp_verify": (20, 15 * 60),    # code guesses per network
    "enquiry": (10, 60 * 60),
    "report": (10, 60 * 60),
    "contact": (5, 60 * 60),
    "upload": (60, 60 * 60),
    "payment": (20, 60 * 60),
}

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
LOG_DIR = Path(env("LOG_DIR") or BASE_DIR / "logs")
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {"format": "{asctime} {levelname} {name} {message}", "style": "{"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "verbose"},
    },
    "root": {"handlers": ["console"], "level": "INFO"},
    "loggers": {
        "django": {"handlers": ["console"], "level": "INFO", "propagate": False},
        "bph": {"handlers": ["console"], "level": "INFO", "propagate": False},
    },
}
