from django.contrib.sitemaps.views import sitemap
from django.urls import path
from django.views.decorators.cache import cache_page

from core import pwa, views
from core.sitemaps import SITEMAPS

app_name = "core"

urlpatterns = [
    path("", views.home, name="home"),
    path("about/", views.about, name="about"),
    path("contact/", views.contact, name="contact"),
    path("faq/", views.faq, name="faq"),
    path("privacy-policy/", views.privacy, name="privacy"),
    path("terms/", views.terms, name="terms"),
    path("listing-policy/", views.listing_policy, name="listing_policy"),
    path("robots.txt", views.robots_txt, name="robots"),
    path("manifest.webmanifest", pwa.manifest, name="manifest"),
    path("sw.js", pwa.service_worker, name="service_worker"),
    path("offline/", pwa.offline, name="offline"),
    path("healthz/", views.healthz, name="healthz"),
    path("cron/scheduled-tasks/", views.cron_scheduled_tasks, name="cron_scheduled_tasks"),
    path("sitemap.xml", cache_page(60 * 30)(sitemap), {"sitemaps": SITEMAPS}, name="sitemap"),
]
