from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from accounts.models import Role
from core.tests.factories import make_property, make_user
from properties.models import Category, Property


class AdminListingTests(TestCase):
    """Platform admins can add and manage their own listings from Management."""

    def setUp(self):
        cache.clear()
        self.admin = make_user(Role.ADMIN)
        self.client.force_login(self.admin)

    def test_management_links_to_add_property(self):
        for url in ("/management/", reverse("adminpanel:properties")):
            self.assertContains(self.client.get(url), reverse("dashboard:partner_property_add"))

    def test_admin_can_create_draft_inside_management_layout(self):
        page = self.client.get(reverse("dashboard:partner_property_add"))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "admin-sidebar")
        self.assertNotContains(page, reverse("dashboard:partner_subscription"))
        resp = self.client.post(reverse("dashboard:partner_property_add"), {
            "title": "Commercial shop on main road", "category": Category.objects.get(slug="houses").pk,
            "purpose": "rent", "description": "Ground floor space with good frontage and parking nearby.",
            "availability": "immediate",
        })
        prop = Property.objects.get(owner=self.admin)
        self.assertRedirects(resp, reverse("dashboard:partner_property_step", args=[prop.pk, 2]))

    def test_admin_listing_is_published_without_approval_or_plan_limits(self):
        for _ in range(3):  # more than the free plan allows
            make_property(owner=self.admin)
        prop = make_property(owner=self.admin, status="draft", with_image=True)
        self.client.post(reverse("dashboard:partner_property_step", args=[prop.pk, 7]), {"accept_policy": "on"})
        prop.refresh_from_db()
        self.assertEqual(prop.status, Property.Status.ACTIVE)
        self.assertIsNotNone(prop.expires_at)
        self.assertContains(self.client.get(reverse("dashboard:partner_properties")), prop.reference)

    def test_admin_edits_stay_live(self):
        prop = make_property(owner=self.admin, with_image=True)
        self.client.post(reverse("dashboard:partner_property_step", args=[prop.pk, 1]), {
            "title": "A completely new title for this listing", "category": prop.category_id, "purpose": "rent",
            "description": prop.description, "availability": "immediate",
        })
        prop.refresh_from_db()
        self.assertEqual(prop.title, "A completely new title for this listing")
        self.assertEqual(prop.status, Property.Status.ACTIVE)

    def test_admin_only_manages_own_listings_here_and_customers_stay_blocked(self):
        other = make_property()
        self.assertEqual(self.client.get(reverse("dashboard:partner_property_step", args=[other.pk, 1])).status_code, 404)
        self.client.force_login(make_user())
        self.assertEqual(self.client.get(reverse("dashboard:partner_property_add")).status_code, 403)
