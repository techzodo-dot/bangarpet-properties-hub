"""Settings used by the automated test suite."""
import tempfile
from pathlib import Path

from .base import *  # noqa: F401,F403

DEBUG = False
SECRET_KEY = "test-secret-key-not-for-production"
ALLOWED_HOSTS = ["testserver", "localhost"]
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
EMAIL_DELIVERY_CONFIGURED = True

_tmp = Path(tempfile.mkdtemp(prefix="bph-test-"))
MEDIA_ROOT = _tmp / "media"
PRIVATE_MEDIA_ROOT = _tmp / "private"

DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}

GOOGLE_MAPS_API_KEY = ""
PAYU_MERCHANT_KEY = "testKey1"
PAYU_MERCHANT_SALT = "testSalt1"
PAYU_MODE = "test"
WHATSAPP_ACCESS_TOKEN = ""
WHATSAPP_PHONE_NUMBER_ID = ""
