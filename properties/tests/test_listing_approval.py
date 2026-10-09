"""Every listing is approved by an admin before it goes live."""
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from accounts.models import Role
from core.tests.factories import make_property, make_user
from notifications.models import Notification
from properties.models import Property
from properties.services import record_changes, snapshot


def step(prop, n):
    return reverse("dashboard:partner_property_step", args=[prop.pk, n])


class ListingApprovalTests(TestCase):
    def setUp(self):
        cache.clear()
        self.admin = make_user(Role.ADMIN)

    def test_owner_and_broker_listings_wait_for_an_admin(self):
        for role in (Role.OWNER, Role.BROKER):
            with self.subTest(role=role):
                partner = make_user(role)
                prop = make_property(owner=partner, status="draft", with_image=True)
                self.client.force_login(partner)
                self.client.post(step(prop, 7), {"accept_policy": "on"})
                prop.refresh_from_db()
                self.assertEqual(prop.status, Property.Status.PENDING)
                self.assertFalse(Property.objects.public().filter(pk=prop.pk).exists())
                self.assertTrue(Notification.objects.filter(recipient=self.admin, title__contains=prop.reference).exists())
                self.client.logout()
                self.assertEqual(self.client.get(prop.get_absolute_url()).status_code, 404)
                # Approved by an admin, it goes live.
                self.client.force_login(self.admin)
                self.client.post(reverse("adminpanel:property_action", args=[prop.pk, "approve"]))
                prop.refresh_from_db()
                self.assertEqual(prop.status, Property.Status.ACTIVE)
                self.assertTrue(Property.objects.public().filter(pk=prop.pk).exists())

    def test_admin_listings_go_live_directly(self):
        prop = make_property(owner=self.admin, status="draft", with_image=True)
        self.client.force_login(self.admin)
        self.client.post(step(prop, 7), {"accept_policy": "on"})
        prop.refresh_from_db()
        self.assertEqual(prop.status, Property.Status.ACTIVE)

    def test_changing_the_phone_number_on_a_live_listing_needs_approval(self):
        owner = make_user(Role.OWNER)
        prop = make_property(owner=owner, with_image=True)
        self.client.force_login(owner)
        self.client.post(step(prop, 6), {"contact_name": "Owner", "contact_phone": "9845099999", "whatsapp_number": "9845099999",
                                        "preferred_contact": "phone", "contact_visibility": "registered"})
        prop.refresh_from_db()
        self.assertEqual(prop.status, Property.Status.PENDING)
        self.assertTrue(Notification.objects.filter(recipient=self.admin, title=f"Listing edited: {prop.reference}").exists())

    def test_new_photos_on_a_live_listing_need_approval_and_admins_are_told(self):
        owner = make_user(Role.OWNER)
        prop = make_property(owner=owner, with_image=True)
        moved = record_changes(prop, snapshot(prop), owner, extra_changes={"images": [None, "1 photo(s) added"]})
        prop.refresh_from_db()
        self.assertTrue(moved)
        self.assertEqual(prop.status, Property.Status.PENDING)
        self.assertTrue(Notification.objects.filter(recipient=self.admin, title=f"Listing edited: {prop.reference}").exists())
