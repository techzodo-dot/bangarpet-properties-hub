"""
Production settings.

Every value that differs per deployment comes from environment variables.
Startup fails loudly if critical security settings are missing.
"""
from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F401,F403
from .base import LOG_DIR, env, env_bool, env_int

DEBUG = False

if not SECRET_KEY or SECRET_KEY in {"change-me"} or len(SECRET_KEY) < 40:  # noqa: F405
    raise ImproperlyConfigured("Set a strong SECRET_KEY (40+ characters) in the environment.")
if not ALLOWED_HOSTS:  # noqa: F405
    raise ImproperlyConfigured("Set ALLOWED_HOSTS in the environment.")

# HTTPS
SECURE_SSL_REDIRECT = env_bool("SECURE_SSL_REDIRECT", True)
if env_bool("USE_X_FORWARDED_PROTO", True):
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_HSTS_SECONDS = env_int("SECURE_HSTS_SECONDS", 31536000)
SECURE_HSTS_INCLUDE_SUBDOMAINS = env_bool("SECURE_HSTS_INCLUDE_SUBDOMAINS", False)
SECURE_HSTS_PRELOAD = env_bool("SECURE_HSTS_PRELOAD", False)
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SESSION_COOKIE_NAME = "bph_sessionid"
CSRF_COOKIE_NAME = "bph_csrftoken"

# Static files are compressed and fingerprinted by WhiteNoise.
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}

# A database-backed cache is shared by all Gunicorn workers, which keeps rate
# limits and login throttling consistent. Run `python manage.py createcachetable`.
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.db.DatabaseCache",
        "LOCATION": "bph_cache",
    }
}

LOG_DIR.mkdir(parents=True, exist_ok=True)
LOGGING["handlers"]["file"] = {  # noqa: F405
    "class": "logging.handlers.RotatingFileHandler",
    "filename": str(LOG_DIR / "bph.log"),
    "maxBytes": 5 * 1024 * 1024,
    "backupCount": 5,
    "formatter": "verbose",
}
LOGGING["handlers"]["mail_admins"] = {  # noqa: F405
    "class": "django.utils.log.AdminEmailHandler",
    "level": "ERROR",
}
LOGGING["root"]["handlers"] = ["console", "file"]  # noqa: F405
LOGGING["loggers"]["django"]["handlers"] = ["console", "file"]  # noqa: F405
LOGGING["loggers"]["bph"]["handlers"] = ["console", "file"]  # noqa: F405
LOGGING["loggers"]["django.request"] = {  # noqa: F405
    "handlers": ["console", "file", "mail_admins"],
    "level": "ERROR",
    "propagate": False,
}

ADMINS = [("Site admin", email) for email in env("ADMIN_ERROR_EMAILS", "").split(",") if email.strip()]
