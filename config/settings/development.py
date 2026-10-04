"""Development settings: DEBUG on, console email, local media."""
from .base import *  # noqa: F401,F403
from .base import env_bool

DEBUG = env_bool("DEBUG", True)
SECRET_KEY = SECRET_KEY or "dev-only-insecure-secret-key-change-me"  # noqa: F405
ALLOWED_HOSTS = ALLOWED_HOSTS or ["localhost", "127.0.0.1"]  # noqa: F405
INTERNAL_IPS = ["127.0.0.1"]
