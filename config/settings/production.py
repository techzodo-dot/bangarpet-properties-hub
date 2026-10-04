"""
Production settings.

Every value that differs per deployment comes from environment variables.
Startup fails loudly if critical security settings are missing.
"""
from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F401,F403
from .base import LOG_DIR, env, env_bool, env_int

DEBUG = False

# Vercel runs the app as a serverless function: read-only file system, no
# persistent disk, a fresh process per instance. See docs/DEPLOYMENT.md.
ON_VERCEL = bool(env("VERCEL", ""))

if ON_VERCEL:
    if not env("DATABASE_URL"):
        raise ImproperlyConfigured(
            "DATABASE_URL is not set. Add a Neon Postgres database to the Vercel project "
            "(Storage -> Create Database -> Neon) and redeploy."
        )
    # Serverless instances come and go: don't hold connections open between requests.
    DATABASES["default"]["CONN_MAX_AGE"] = 0  # noqa: F405
    if DATABASES["default"]["ENGINE"] == "django.db.backends.postgresql":  # noqa: F405
        # Works behind transaction-mode poolers (Supabase Supavisor :6543,
        # Neon/PgBouncer), which support neither prepared statements nor
        # server-side cursors.
        DATABASES["default"]["DISABLE_SERVER_SIDE_CURSORS"] = True  # noqa: F405
        DATABASES["default"].setdefault("OPTIONS", {})["prepare_threshold"] = None  # noqa: F405
    _prod_host = env("VERCEL_PROJECT_PRODUCTION_URL", "")
    if _prod_host and not env("SITE_URL"):
        SITE_URL = f"https://{_prod_host}"

# Hosting platforms that publish the app's public hostname in the environment.
for _var in ("RENDER_EXTERNAL_HOSTNAME", "RAILWAY_PUBLIC_DOMAIN",
             "VERCEL_PROJECT_PRODUCTION_URL", "VERCEL_BRANCH_URL", "VERCEL_URL"):
    _host = env(_var, "")
    if _host and _host not in ALLOWED_HOSTS:  # noqa: F405
        ALLOWED_HOSTS.append(_host)  # noqa: F405
        CSRF_TRUSTED_ORIGINS.append(f"https://{_host}")  # noqa: F405

if not SECRET_KEY or SECRET_KEY in {"change-me"} or len(SECRET_KEY) < 40:  # noqa: F405
    raise ImproperlyConfigured("Set a strong SECRET_KEY (40+ characters) in the environment.")
if not ALLOWED_HOSTS:  # noqa: F405
    raise ImproperlyConfigured("Set ALLOWED_HOSTS in the environment.")

# HTTPS
SECURE_SSL_REDIRECT = env_bool("SECURE_SSL_REDIRECT", True)
SECURE_REDIRECT_EXEMPT = [r"^healthz/$"]  # platform health checks call plain HTTP internally
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

if ON_VERCEL:
    # Photos go to Vercel Blob (public CDN); verification documents stay in the database.
    STORAGES = {
        "default": {"BACKEND": "core.storage.VercelBlobStorage"},
        "staticfiles": {"BACKEND": "core.storage.VersionedStaticFilesStorage"},
    }
    PRIVATE_FILE_STORAGE = "core.storage.DatabaseStorage"
    # Static files are served straight from the source tree by WhiteNoise; a
    # per-deployment ?v= query string busts browser caches after each deploy.
    STATIC_ROOT = None
    WHITENOISE_USE_FINDERS = True
    WHITENOISE_AUTOREFRESH = False
    WHITENOISE_MAX_AGE = 60 * 60 * 24
    STATIC_VERSION = (env("VERCEL_DEPLOYMENT_ID") or env("VERCEL_GIT_COMMIT_SHA") or "1").replace("dpl_", "")[:10]

    def WHITENOISE_ADD_HEADERS_FUNCTION(headers, path, url):  # noqa: N802
        headers["CDN-Cache-Control"] = "public, max-age=86400"

    # Vercel rejects request bodies over 4.5 MB. Photos are resized in the
    # browser and sent in small batches; documents must stay under 4 MB.
    MAX_DOCUMENT_UPLOAD_MB = 4

# A database-backed cache is shared by all Gunicorn workers, which keeps rate
# limits and login throttling consistent. Run `python manage.py createcachetable`.
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.db.DatabaseCache",
        "LOCATION": "bph_cache",
    }
}

# Log to stdout and a rotating file; on Vercel (read-only disk) stdout only,
# which appears under the project's Logs tab.
_log_handlers = ["console"]
if not ON_VERCEL:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    LOGGING["handlers"]["file"] = {  # noqa: F405
        "class": "logging.handlers.RotatingFileHandler",
        "filename": str(LOG_DIR / "bph.log"),
        "maxBytes": 5 * 1024 * 1024,
        "backupCount": 5,
        "formatter": "verbose",
    }
    _log_handlers.append("file")
LOGGING["handlers"]["mail_admins"] = {  # noqa: F405
    "class": "django.utils.log.AdminEmailHandler",
    "level": "ERROR",
}
LOGGING["root"]["handlers"] = list(_log_handlers)  # noqa: F405
LOGGING["loggers"]["django"]["handlers"] = list(_log_handlers)  # noqa: F405
LOGGING["loggers"]["bph"]["handlers"] = list(_log_handlers)  # noqa: F405
LOGGING["loggers"]["django.request"] = {  # noqa: F405
    "handlers": [*_log_handlers, "mail_admins"],
    "level": "ERROR",
    "propagate": False,
}

ADMINS = [("Site admin", email) for email in env("ADMIN_ERROR_EMAILS", "").split(",") if email.strip()]
