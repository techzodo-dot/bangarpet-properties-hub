"""Small cache-backed fixed-window rate limiter.

Use a shared cache (database or Redis) in production so limits apply across
all application server workers.
"""
import hashlib

from django.conf import settings
from django.core.cache import cache


def get_client_ip(request):
    # REMOTE_ADDR is trusted by default. When running behind a reverse proxy that
    # overwrites X-Forwarded-For, the first entry is the client address.
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
    if forwarded and getattr(settings, "SECURE_PROXY_SSL_HEADER", None):
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR") or "0.0.0.0"


def _key(scope, ident):
    digest = hashlib.sha256(f"{scope}:{ident}".encode()).hexdigest()[:32]
    return f"bph:rl:{scope}:{digest}"


def is_limited(scope, ident):
    limit, _window = settings.RATE_LIMITS.get(scope, (10, 3600))
    return (cache.get(_key(scope, ident)) or 0) >= limit


def hit(scope, ident):
    _limit, window = settings.RATE_LIMITS.get(scope, (10, 3600))
    key = _key(scope, ident)
    if cache.add(key, 1, window):
        return 1
    try:
        return cache.incr(key)
    except ValueError:
        cache.set(key, 1, window)
        return 1


def reset(scope, ident):
    cache.delete(_key(scope, ident))


def check_and_hit(scope, ident):
    """Return True when the request is allowed (and count it)."""
    if is_limited(scope, ident):
        return False
    hit(scope, ident)
    return True


def request_ident(request):
    user_part = request.user.pk if getattr(request, "user", None) and request.user.is_authenticated else "anon"
    return f"{user_part}:{get_client_ip(request)}"
