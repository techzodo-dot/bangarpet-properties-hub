from django.contrib.sitemaps.views import sitemap
from django.urls import path
from django.views.decorators.cache import cache_page

from core import views
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
    path("sitemap.xml", cache_page(60 * 30)(sitemap), {"sitemaps": SITEMAPS}, name="sitemap"),
]
