import hashlib
import hmac
import json
from datetime import timedelta
from unittest import mock

from django.conf import settings
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import Role
from core.models import PlatformSetting
from core.tests.factories import make_property, make_user
from payments.models import Payment
from payments.services import record_subscription_charge, sync_auto_renewals
from properties.models import ContactUnlock
from subscriptions.models import Subscription
from subscriptions.services import activate_subscription, contact_pass_plan


def unlock_url(prop):
    return reverse("properties:unlock_contact", args=[prop.slug])


class FreeContactLimitTests(TestCase):
    def setUp(self):
        cache.clear()
        self.customer = make_user(Role.CUSTOMER)
        self.props = [make_property(contact_visibility="public") for _ in range(6)]

    def test_visitors_must_sign_in_to_see_contacts(self):
        resp = self.client.get(self.props[0].get_absolute_url())
        self.assertNotContains(resp, "tel:" + self.props[0].contact_phone)
        self.assertContains(resp, "Sign in to view phone number")

    def test_five_free_contacts_then_contact_pass(self):
        self.client.force_login(self.customer)
        first = self.props[0]
        page = self.client.get(first.get_absolute_url())
        self.assertNotContains(page, "tel:" + first.contact_phone)
        self.assertContains(page, "5 of 5 free owner contacts left this month")
        for prop in self.props[:5]:
            resp = self.client.post(unlock_url(prop))
            self.assertRedirects(resp, prop.get_absolute_url() + "#contact", fetch_redirect_response=False)
        self.assertContains(self.client.get(first.get_absolute_url()), "tel:" + first.contact_phone)
        # Re-opening an unlocked listing is free.
        self.client.post(unlock_url(first))
        self.assertEqual(ContactUnlock.objects.filter(user=self.customer).count(), 5)
        # The sixth listing asks for the Contact Pass.
        sixth = self.props[5]
        page = self.client.get(sixth.get_absolute_url())
        self.assertNotContains(page, "tel:" + sixth.contact_phone)
        self.assertContains(page, "Get Contact Pass")
        resp = self.client.post(unlock_url(sixth))
        self.assertTrue(resp["Location"].startswith(reverse("payments:contact_pass")))
        self.assertFalse(ContactUnlock.objects.filter(user=self.customer, property=sixth).exists())

    def test_free_contacts_reset_each_month(self):
        last_month = timezone.now() - timedelta(days=40)
        for prop in self.props[:5]:
            unlock = ContactUnlock.objects.create(user=self.customer, property=prop)
            ContactUnlock.objects.filter(pk=unlock.pk).update(created_at=last_month)
        self.client.force_login(self.customer)
        self.client.post(unlock_url(self.props[5]))
        self.assertTrue(ContactUnlock.objects.filter(user=self.customer, property=self.props[5]).exists())
        # Listings unlocked last month stay unlocked.
        self.assertContains(self.client.get(self.props[0].get_absolute_url()), "tel:" + self.props[0].contact_phone)

    def test_owners_brokers_and_admins_are_not_limited(self):
        prop = self.props[0]
        for user in (make_user(Role.OWNER), make_user(Role.BROKER), make_user(Role.ADMIN)):
            self.client.force_login(user)
            self.assertContains(self.client.get(prop.get_absolute_url()), "tel:" + prop.contact_phone)

    def test_limit_can_be_switched_off(self):
        site = PlatformSetting.load()
        site.contact_limit_enabled = False
        site.save()
        self.client.force_login(self.customer)
        self.assertContains(self.client.get(self.props[0].get_absolute_url()), "tel:" + self.props[0].contact_phone)
        self.client.logout()
        self.assertContains(self.client.get(self.props[0].get_absolute_url()), "tel:" + self.props[0].contact_phone)

    def test_free_contact_count_is_editable(self):
        site = PlatformSetting.load()
        site.free_contacts_per_month = 1
        site.save()
        self.client.force_login(self.customer)
        self.client.post(unlock_url(self.props[0]))
        self.assertContains(self.client.get(self.props[1].get_absolute_url()), "Get Contact Pass")

    def test_active_pass_shows_every_contact(self):
        activate_subscription(self.customer, contact_pass_plan(), amount_paid=99)
        self.client.force_login(self.customer)
        for prop in self.props:
            self.assertContains(self.client.get(prop.get_absolute_url()), "tel:" + prop.contact_phone)


def fake_rzp_subscription(plan_id, days, notes=None):
    return {"id": "sub_TEST123", "status": "created", "plan_id": plan_id}


def signature(payment_id, subscription_id):
    return hmac.new(settings.RAZORPAY_KEY_SECRET.encode(), f"{payment_id}|{subscription_id}".encode(), hashlib.sha256).hexdigest()


def webhook_body(event, sub_id, payment_id, amount=9900):
    return json.dumps({
        "event": event,
        "payload": {"subscription": {"entity": {"id": sub_id}},
                    "payment": {"entity": {"id": payment_id, "amount": amount, "currency": "INR", "status": "captured"}}},
    }).encode()


def webhook_headers(body, event_id):
    sig = hmac.new(settings.RAZORPAY_WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()
    return {"HTTP_X_RAZORPAY_SIGNATURE": sig, "HTTP_X_RAZORPAY_EVENT_ID": event_id}


@mock.patch("payments.razorpay.create_subscription", side_effect=fake_rzp_subscription)
@mock.patch("payments.razorpay.create_plan", return_value="plan_TEST99")
class RecurringContactPassTests(TestCase):
    def setUp(self):
        cache.clear()
        self.customer = make_user(Role.CUSTOMER)
        self.client.force_login(self.customer)
        self.plan = contact_pass_plan()

    def start(self):
        resp = self.client.post(reverse("payments:contact_pass_subscribe"))
        payment = Payment.objects.get(user=self.customer)
        self.assertRedirects(resp, reverse("payments:contact_pass_pay", args=[payment.uid]), fetch_redirect_response=False)
        return payment

    def test_plan_is_99_a_month_for_customers(self, *_):
        self.assertEqual(self.plan.price, 99)
        self.assertEqual(self.plan.billing_period_days, 30)
        page = self.client.get(reverse("payments:contact_pass"))
        self.assertContains(page, "Get Contact Pass")
        self.assertContains(page, "5 <small")

    def test_subscribe_pay_and_verify(self, create_plan, create_sub):
        payment = self.start()
        self.assertEqual(payment.gateway_subscription_id, "sub_TEST123")
        self.assertEqual(create_plan.call_args[0][1], 9900)
        page = self.client.get(reverse("payments:contact_pass_pay", args=[payment.uid]))
        self.assertContains(page, 'data-subscription="sub_TEST123"')
        self.plan.refresh_from_db()
        self.assertTrue(self.plan.razorpay_plan_id.endswith(":9900:plan_TEST99"))
        resp = self.client.post(reverse("payments:contact_pass_verify"), {
            "razorpay_subscription_id": "sub_TEST123", "razorpay_payment_id": "pay_FIRST",
            "razorpay_signature": signature("pay_FIRST", "sub_TEST123"),
        })
        self.assertRedirects(resp, reverse("payments:contact_pass"), fetch_redirect_response=False)
        sub = Subscription.objects.get(user=self.customer)
        self.assertTrue(sub.is_current and sub.auto_renew)
        self.assertEqual(sub.gateway_subscription_id, "sub_TEST123")
        payment.refresh_from_db()
        self.assertEqual(payment.status, Payment.Status.PAID)
        prop = make_property(contact_visibility="public")
        self.assertContains(self.client.get(prop.get_absolute_url()), "tel:" + prop.contact_phone)
        # A second subscribe reuses the cached Razorpay plan.
        self.client.post(reverse("payments:contact_pass_subscribe"))
        self.assertEqual(create_plan.call_count, 1)

    def test_bad_signature_does_not_activate(self, *_):
        self.start()
        self.client.post(reverse("payments:contact_pass_verify"), {
            "razorpay_subscription_id": "sub_TEST123", "razorpay_payment_id": "pay_FIRST", "razorpay_signature": "bad",
        })
        self.assertFalse(Subscription.objects.filter(user=self.customer).exists())

    def test_monthly_renewal_by_webhook_is_idempotent(self, *_):
        self.start()
        self.client.post(reverse("payments:contact_pass_verify"), {
            "razorpay_subscription_id": "sub_TEST123", "razorpay_payment_id": "pay_FIRST",
            "razorpay_signature": signature("pay_FIRST", "sub_TEST123"),
        })
        first_end = Subscription.objects.get(user=self.customer).ends_at
        body = webhook_body("subscription.charged", "sub_TEST123", "pay_SECOND")
        url = reverse("payments:razorpay_webhook")
        self.assertEqual(self.client.post(url, body, content_type="application/json", **webhook_headers(body, "evt_2")).status_code, 200)
        self.client.post(url, body, content_type="application/json", **webhook_headers(body, "evt_2b"))  # retried
        sub = Subscription.objects.get(user=self.customer, status="active")
        self.assertEqual(sub.ends_at, first_end + timedelta(days=30))
        self.assertEqual(Payment.objects.filter(user=self.customer, status="paid").count(), 2)

    def test_webhook_before_checkout_return_activates_once(self, *_):
        self.start()
        body = webhook_body("subscription.charged", "sub_TEST123", "pay_FIRST")
        self.client.post(reverse("payments:razorpay_webhook"), body, content_type="application/json", **webhook_headers(body, "evt_1"))
        self.client.post(reverse("payments:contact_pass_verify"), {
            "razorpay_subscription_id": "sub_TEST123", "razorpay_payment_id": "pay_FIRST",
            "razorpay_signature": signature("pay_FIRST", "sub_TEST123"),
        })
        self.assertEqual(Payment.objects.filter(user=self.customer, status="paid").count(), 1)
        self.assertTrue(Subscription.objects.get(user=self.customer).auto_renew)

    @mock.patch("payments.razorpay.cancel_subscription", return_value={"status": "cancelled"})
    def test_cancel_auto_renewal_keeps_paid_time(self, cancel, *_):
        self.start()
        self.client.post(reverse("payments:contact_pass_verify"), {
            "razorpay_subscription_id": "sub_TEST123", "razorpay_payment_id": "pay_FIRST",
            "razorpay_signature": signature("pay_FIRST", "sub_TEST123"),
        })
        self.client.post(reverse("payments:contact_pass_cancel"))
        cancel.assert_called_once_with("sub_TEST123")
        sub = Subscription.objects.get(user=self.customer)
        self.assertFalse(sub.auto_renew)
        self.assertTrue(sub.is_current)

    def test_daily_sync_records_missed_renewals(self, *_):
        self.start()
        self.client.post(reverse("payments:contact_pass_verify"), {
            "razorpay_subscription_id": "sub_TEST123", "razorpay_payment_id": "pay_FIRST",
            "razorpay_signature": signature("pay_FIRST", "sub_TEST123"),
        })
        sub = Subscription.objects.get(user=self.customer)
        Subscription.objects.filter(pk=sub.pk).update(ends_at=timezone.now() + timedelta(hours=2))
        invoices = [{"status": "paid", "payment_id": "pay_FIRST", "amount_paid": 9900},
                    {"status": "paid", "payment_id": "pay_SECOND", "amount_paid": 9900}]
        with mock.patch("payments.razorpay.subscription_invoices", return_value=invoices), \
                mock.patch("payments.razorpay.fetch_subscription", return_value={"status": "active"}):
            self.assertEqual(sync_auto_renewals(), "1 renewed, 0 stopped")
        sub.refresh_from_db()
        self.assertGreater(sub.ends_at, timezone.now() + timedelta(days=29))

    def test_unknown_subscription_charge_is_ignored(self, *_):
        self.assertEqual(record_subscription_charge("sub_UNKNOWN", {"id": "pay_X", "amount": 9900}), "no matching subscription")


class ContactPassAccessTests(TestCase):
    def test_partners_are_sent_back(self):
        self.client.force_login(make_user(Role.OWNER))
        self.assertEqual(self.client.get(reverse("payments:contact_pass")).status_code, 302)

    def test_pricing_page_lists_listing_plans_only(self):
        resp = self.client.get(reverse("subscriptions:pricing"))
        self.assertNotContains(resp, "Contact Pass")

    def test_anonymous_must_sign_in(self):
        resp = self.client.get(reverse("payments:contact_pass"))
        self.assertEqual(resp.status_code, 302)
        self.assertIn(reverse("accounts:login"), resp["Location"])
