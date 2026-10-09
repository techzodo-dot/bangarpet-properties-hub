"""Owners, brokers and admins can mark a listing sold / rented out, and undo it."""
from datetime import timedelta

from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import Role
from core.tests.factories import make_property, make_user
from enquiries.models import Enquiry
from notifications.models import Notification
from properties.models import Property


def partner_action(prop, action):
    return reverse("dashboard:partner_property_action", args=[prop.pk, action])


def admin_action(prop, action):
    return reverse("adminpanel:property_action", args=[prop.pk, action])


class SoldOutTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_owner_marks_a_sale_listing_sold_out(self):
        owner = make_user(Role.OWNER)
        prop = make_property(owner=owner, purpose="sale", price=4500000)
        Enquiry.objects.create(property=prop, customer=make_user(), partner=owner, message="Is it available?",
                               contact_name="Asha", contact_phone="+919845012345")
        self.client.force_login(owner)
        page = self.client.get(reverse("dashboard:partner_properties"))
        self.assertContains(page, partner_action(prop, "close"))
        self.assertContains(page, "Sold out")
        self.client.post(partner_action(prop, "close"))
        prop.refresh_from_db()
        self.assertEqual(prop.status, Property.Status.SOLD)
        self.assertIsNotNone(prop.closed_at)
        self.assertFalse(prop.enquiries.filter(status__in=Enquiry.OPEN_STATUSES).exists())
        # Off the website: visitors see that it was sold, and search no longer lists it.
        self.client.logout()
        self.assertContains(self.client.get(prop.get_absolute_url()), "This property has been sold", status_code=410)
        self.assertFalse(Property.objects.public().filter(pk=prop.pk).exists())

    def test_broker_marks_a_rental_rented_out(self):
        broker = make_user(Role.BROKER)
        prop = make_property(owner=broker, purpose="rent")
        self.client.force_login(broker)
        self.assertContains(self.client.get(reverse("dashboard:partner_property_manage", args=[prop.pk])), "Mark as rented out")
        self.client.post(partner_action(prop, "close"), {"return_to": "manage"})
        prop.refresh_from_db()
        self.assertEqual(prop.status, Property.Status.RENTED)

    def test_undo_makes_the_listing_live_again(self):
        owner = make_user(Role.OWNER)
        prop = make_property(owner=owner, purpose="sale")
        self.client.force_login(owner)
        self.client.post(partner_action(prop, "close"))
        self.assertContains(self.client.get(reverse("dashboard:partner_property_manage", args=[prop.pk])), "Available again")
        self.client.post(partner_action(prop, "reopen"))
        prop.refresh_from_db()
        self.assertEqual((prop.status, prop.closed_at), (Property.Status.ACTIVE, None))
        self.assertEqual(self.client.get(prop.get_absolute_url()).status_code, 200)

    def test_undo_after_the_listing_period_ends_needs_a_renewal(self):
        owner = make_user(Role.OWNER)
        prop = make_property(owner=owner, purpose="sale")
        self.client.force_login(owner)
        self.client.post(partner_action(prop, "close"))
        Property.objects.filter(pk=prop.pk).update(expires_at=timezone.now() - timedelta(days=1))
        self.client.post(partner_action(prop, "reopen"))
        prop.refresh_from_db()
        self.assertEqual(prop.status, Property.Status.EXPIRED)

    def test_others_cannot_mark_someone_elses_listing(self):
        prop = make_property(owner=make_user(Role.OWNER))
        self.client.force_login(make_user(Role.OWNER))
        self.assertEqual(self.client.post(partner_action(prop, "close")).status_code, 404)
        self.client.force_login(make_user(Role.CUSTOMER))
        self.assertEqual(self.client.post(partner_action(prop, "close")).status_code, 403)
        prop.refresh_from_db()
        self.assertEqual(prop.status, Property.Status.ACTIVE)

    def test_admin_marks_any_listing_sold_and_the_owner_is_told(self):
        owner = make_user(Role.OWNER)
        prop = make_property(owner=owner, purpose="sale")
        self.client.force_login(make_user(Role.ADMIN))
        self.assertContains(self.client.get(reverse("adminpanel:property_review", args=[prop.pk])), "Mark as sold out")
        self.client.post(admin_action(prop, "close"))
        prop.refresh_from_db()
        self.assertEqual(prop.status, Property.Status.SOLD)
        self.assertTrue(Notification.objects.filter(recipient=owner, title__contains="marked as sold").exists())
        self.client.post(admin_action(prop, "reopen"))
        prop.refresh_from_db()
        self.assertEqual(prop.status, Property.Status.ACTIVE)

    def test_staff_need_the_listings_area(self):
        prop = make_property(purpose="sale")
        self.client.force_login(make_user(Role.STAFF, staff_permissions=["expenses"]))
        self.assertEqual(self.client.post(admin_action(prop, "close")).status_code, 403)
        self.client.force_login(make_user(Role.STAFF, staff_permissions=["listings"]))
        self.client.post(admin_action(prop, "close"))
        prop.refresh_from_db()
        self.assertEqual(prop.status, Property.Status.SOLD)

    def test_pending_listings_cannot_be_marked_sold(self):
        owner = make_user(Role.OWNER)
        prop = make_property(owner=owner, status="pending")
        self.client.force_login(owner)
        self.client.post(partner_action(prop, "close"))
        prop.refresh_from_db()
        self.assertEqual(prop.status, Property.Status.PENDING)
