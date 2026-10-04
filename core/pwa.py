"""Installable web app (PWA): manifest, service worker and offline page.

The service worker is deliberately conservative: it caches only static assets
(CSS, JS, fonts, icons) and a generic offline page. HTML pages are always
fetched from the network, so private pages (dashboards, enquiries, admin) are
never stored on the device.
"""
import hashlib
import json

from django.http import HttpResponse, JsonResponse
from django.shortcuts import render
from django.template.loader import render_to_string
from django.templatetags.static import static
from django.views.decorators.cache import cache_control
from django.views.decorators.http import require_GET

from core.models import PlatformSetting

PRECACHE_ASSETS = [
    "css/main.css",
    "js/main.js",
    "vendor/bootstrap/bootstrap.min.css",
    "vendor/bootstrap/bootstrap.bundle.min.js",
    "vendor/bootstrap-icons/bootstrap-icons.min.css",
    "vendor/bootstrap-icons/fonts/bootstrap-icons.woff2",
    "vendor/fonts/plus-jakarta-sans-latin-400-normal.woff2",
    "vendor/fonts/plus-jakarta-sans-latin-700-normal.woff2",
    "vendor/fonts/plus-jakarta-sans-latin-800-normal.woff2",
    "img/logo-mark.svg",
    "img/icon-192.png",
]


def _site_name():
    try:
        return PlatformSetting.load().site_name or "Bangarpet Property Hub"
    except Exception:  # database not ready (e.g. during first deploy)
        return "Bangarpet Property Hub"


@require_GET
@cache_control(public=True, max_age=60 * 60 * 24)
def manifest(request):
    name = _site_name()
    data = {
        "id": "/",
        "name": name,
        "short_name": "Bangarpet",
        "description": "Find houses, flats, PG rooms, shops and plots for rent or sale in Bangarpet. "
                       "Talk directly to owners and verified brokers.",
        "lang": "en-IN",
        "start_url": "/?source=pwa",
        "scope": "/",
        "display": "standalone",
        "background_color": "#ffffff",
        "theme_color": "#ffffff",
        "categories": ["lifestyle", "business"],
        "icons": [
            {"src": static("img/icon-192.png"), "sizes": "192x192", "type": "image/png", "purpose": "any"},
            {"src": static("img/icon-512.png"), "sizes": "512x512", "type": "image/png", "purpose": "any"},
            {"src": static("img/icon-maskable-512.png"), "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
        ],
        "shortcuts": [
            {"name": "Search properties", "url": "/properties/", "icons": [{"src": static("img/icon-192.png"), "sizes": "192x192"}]},
            {"name": "Properties for rent", "url": "/rent/", "icons": [{"src": static("img/icon-192.png"), "sizes": "192x192"}]},
            {"name": "My account", "url": "/login/", "icons": [{"src": static("img/icon-192.png"), "sizes": "192x192"}]},
        ],
    }
    return JsonResponse(data, content_type="application/manifest+json", json_dumps_params={"indent": 2})


@require_GET
def service_worker(request):
    assets = [static(path) for path in PRECACHE_ASSETS]
    # The version changes whenever a fingerprinted asset changes, which makes the
    # browser install the new worker and drop old caches on the next visit.
    version = hashlib.sha1("|".join(assets).encode()).hexdigest()[:12]
    offline_url = "/offline/"
    precache = json.dumps([offline_url, *assets]).replace("</", "<\\/")
    body = render_to_string("pwa/sw.js", {"precache": precache, "version": version, "offline_url": offline_url})
    response = HttpResponse(body, content_type="application/javascript; charset=utf-8")
    response["Cache-Control"] = "no-cache"
    response["Service-Worker-Allowed"] = "/"
    return response


@require_GET
def offline(request):
    return render(request, "pwa/offline.html")
