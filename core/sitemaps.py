from django.contrib.sitemaps import Sitemap
from django.urls import reverse

from properties.models import Category, Property


class SiteSitemap(Sitemap):
    """Builds absolute URLs from SITE_URL instead of the contrib.sites framework."""

    protocol = None

    def get_urls(self, page=1, site=None, protocol=None):
        from django.conf import settings
        from urllib.parse import urlparse

        parsed = urlparse(settings.SITE_URL)

        class _Site:
            domain = parsed.netloc

        return super().get_urls(page=page, site=_Site(), protocol=parsed.scheme or "https")


class StaticSitemap(SiteSitemap):
    changefreq = "weekly"
    priority = 0.6

    def items(self):
        return ["core:home", "properties:search", "properties:rent", "properties:buy", "properties:pg_rooms",
                "properties:commercial", "core:about", "core:contact", "core:faq", "subscriptions:pricing"]

    def location(self, item):
        return reverse(item)


class CategorySitemap(SiteSitemap):
    changefreq = "daily"
    priority = 0.7

    def items(self):
        return Category.objects.filter(is_active=True)


class PropertySitemap(SiteSitemap):
    changefreq = "daily"
    priority = 0.8
    limit = 5000

    def items(self):
        return Property.objects.public().only("slug", "updated_at").order_by("-published_at")

    def lastmod(self, obj):
        return obj.updated_at


SITEMAPS = {"static": StaticSitemap, "categories": CategorySitemap, "properties": PropertySitemap}
