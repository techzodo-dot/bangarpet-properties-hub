from django.test import TestCase
from django.urls import reverse

from core.tests.factories import make_property


class InstallAppTests(TestCase):
    def test_pages_offer_install_button_banner_and_help(self):
        page = self.client.get(reverse("core:home"))
        self.assertContains(page, "data-install-app", count=3)  # header, footer, banner
        self.assertContains(page, 'id="installBanner"')
        self.assertContains(page, 'id="installModal"')
        self.assertContains(page, "Add to Home Screen")

    def test_property_page_keeps_contact_bar_clear(self):
        page = self.client.get(make_property().get_absolute_url())
        self.assertNotContains(page, 'id="installBanner"')
        self.assertContains(page, 'id="installModal"')

    def test_manifest_is_installable(self):
        data = self.client.get("/manifest.webmanifest").json()
        self.assertEqual(data["display"], "standalone")
        self.assertTrue(any(icon["sizes"] == "512x512" for icon in data["icons"]))
