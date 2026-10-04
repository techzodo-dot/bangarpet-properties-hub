from datetime import timedelta

from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import Role
from core.tests.factories import make_property, make_user
from enquiries.models import Enquiry, PropertyVisit
from notifications.models import Notification


class EnquiryFlowTests(TestCase):
    def setUp(self):
        cache.clear()
        self.prop = make_property()
        self.partner = self.prop.owner
        self.customer = make_user()
        self.client.force_login(self.customer)
        self.url = reverse("enquiries:create", args=[self.prop.pk])

    def _data(self, **kw):
        data = {"contact_name": "Customer", "contact_phone": "9876512345", "contact_email": "c@example.com",
                "preferred_contact": "phone", "message": "Is this still available?"}
        data.update(kw)
        return data

    def test_send_enquiry_notifies_partner(self):
        with self.captureOnCommitCallbacks(execute=True):
            resp = self.client.post(self.url, self._data())
        self.assertRedirects(resp, reverse("dashboard:customer_enquiries"))
        enquiry = Enquiry.objects.get()
        self.assertEqual(enquiry.status, Enquiry.Status.NEW)
        self.assertEqual(enquiry.partner, self.partner)
        self.assertTrue(Notification.objects.filter(recipient=self.partner, event="new_enquiry").exists())
        self.prop.refresh_from_db()
        self.assertEqual(self.prop.enquiry_count, 1)

    def test_duplicate_enquiry_prevented(self):
        self.client.post(self.url, self._data())
        self.client.post(self.url, self._data())
        self.assertEqual(Enquiry.objects.count(), 1)

    def test_visit_request_and_partner_scheduling(self):
        date = (timezone.localdate() + timedelta(days=2)).isoformat()
        self.client.post(self.url, self._data(request_visit="on", preferred_date=date, preferred_slot="evening"))
        enquiry = Enquiry.objects.get()
        visit = PropertyVisit.objects.get()
        self.assertEqual(enquiry.status, Enquiry.Status.VISIT_REQUESTED)
        self.client.force_login(self.partner)
        self.client.post(reverse("dashboard:partner_visit_action", args=[visit.pk, "schedule"]),
                         {"visit-scheduled_date": date, "visit-scheduled_time": "17:30", "visit-partner_note": "Meet at gate"})
        visit.refresh_from_db()
        enquiry.refresh_from_db()
        self.assertEqual(visit.status, PropertyVisit.Status.SCHEDULED)
        self.assertEqual(enquiry.status, Enquiry.Status.VISIT_SCHEDULED)
        self.assertTrue(Notification.objects.filter(recipient=self.customer, event="visit_confirmed").exists())
        self.client.post(reverse("dashboard:partner_visit_action", args=[visit.pk, "complete"]))
        enquiry.refresh_from_db()
        self.assertEqual(enquiry.status, Enquiry.Status.COMPLETED)

    def test_visit_date_validation(self):
        self.client.post(self.url, self._data(request_visit="on", preferred_date=timezone.localdate().isoformat(), preferred_slot="morning"))
        self.assertEqual(Enquiry.objects.count(), 0)

    def test_customer_can_cancel_pending_visit_only(self):
        date = (timezone.localdate() + timedelta(days=3)).isoformat()
        self.client.post(self.url, self._data(request_visit="on", preferred_date=date, preferred_slot="morning"))
        visit = PropertyVisit.objects.get()
        self.client.post(reverse("dashboard:customer_visit_cancel", args=[visit.pk]))
        visit.refresh_from_db()
        self.assertEqual(visit.status, PropertyVisit.Status.CANCELLED)

    def test_partner_status_update_and_customer_cancel(self):
        self.client.post(self.url, self._data())
        enquiry = Enquiry.objects.get()
        self.client.force_login(self.partner)
        self.client.post(reverse("dashboard:partner_enquiry_detail", args=[enquiry.pk]), {"status-status": "contacted", "status-note": "Called"})
        enquiry.refresh_from_db()
        self.assertEqual(enquiry.status, "contacted")
        self.assertEqual(enquiry.status_changes.count(), 2)
        self.client.force_login(self.customer)
        self.client.post(reverse("dashboard:customer_enquiry_cancel", args=[enquiry.pk]))
        enquiry.refresh_from_db()
        self.assertEqual(enquiry.status, "cancelled")
        # a new enquiry is allowed once the previous one is closed
        self.client.post(self.url, self._data())
        self.assertEqual(Enquiry.objects.count(), 2)

    def test_partner_cannot_set_arbitrary_status(self):
        self.client.post(self.url, self._data())
        enquiry = Enquiry.objects.get()
        self.client.force_login(self.partner)
        self.client.post(reverse("dashboard:partner_enquiry_detail", args=[enquiry.pk]), {"status-status": "visit_scheduled"})
        enquiry.refresh_from_db()
        self.assertEqual(enquiry.status, "new")

    def test_owner_cannot_enquire_on_own_listing_and_admin_blocked(self):
        self.client.force_login(self.partner)
        self.client.post(self.url, self._data())
        self.assertEqual(Enquiry.objects.count(), 0)
        self.client.force_login(make_user(Role.ADMIN))
        self.assertEqual(self.client.post(self.url, self._data()).status_code, 403)

    def test_anonymous_must_login(self):
        self.client.logout()
        resp = self.client.post(self.url, self._data())
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(Enquiry.objects.count(), 0)


class EnquiryIsolationTests(TestCase):
    def test_users_cannot_access_others_enquiries(self):
        prop = make_property()
        customer = make_user()
        enquiry = Enquiry.objects.create(property=prop, customer=customer, partner=prop.owner, message="Hello there",
                                         contact_name="C", contact_phone="+919876512345")
        other_partner = make_user(Role.OWNER)
        self.client.force_login(other_partner)
        self.assertEqual(self.client.get(reverse("dashboard:partner_enquiry_detail", args=[enquiry.pk])).status_code, 404)
        self.assertNotContains(self.client.get(reverse("dashboard:partner_enquiries")), "Hello there")
        other_customer = make_user()
        self.client.force_login(other_customer)
        self.assertNotContains(self.client.get(reverse("dashboard:customer_enquiries")), "Hello there")
        self.client.post(reverse("dashboard:customer_enquiry_cancel", args=[enquiry.pk]))
        enquiry.refresh_from_db()
        self.assertEqual(enquiry.status, "new")
