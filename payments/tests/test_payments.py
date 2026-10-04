import hashlib
import hmac
import json
from unittest import mock

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import Role
from core.tests.factories import make_user
from payments.models import Invoice, Payment, WebhookEvent
from subscriptions.models import Subscription, SubscriptionPlan
from subscriptions.services import current_plan, listing_limit


def sign(secret, message):
    return hmac.new(secret.encode(), message.encode() if isinstance(message, str) else message, hashlib.sha256).hexdigest()


class FakeResponse:
    def __init__(self, data, status=200):
        self._data, self.status_code = data, status

    def json(self):
        return self._data


class CheckoutTests(TestCase):
    def setUp(self):
        cache.clear()
        self.owner = make_user(Role.OWNER)
        self.client.force_login(self.owner)
        self.plan = SubscriptionPlan.objects.get(slug="basic")

    def _checkout(self):
        def fake_post(url, auth=None, json=None, timeout=None):
            self.assertEqual(auth, ("rzp_test_dummy", "test_secret"))
            return FakeResponse({"id": "order_TEST123", "amount": json["amount"], "currency": "INR"})

        with mock.patch("payments.razorpay.requests.post", side_effect=fake_post):
            resp = self.client.post(reverse("payments:checkout", args=["basic"]))
        payment = Payment.objects.get()
        self.assertRedirects(resp, reverse("payments:pay", args=[payment.uid]))
        return payment

    def test_order_created_server_side_with_correct_amount(self):
        payment = self._checkout()
        self.assertEqual(payment.gateway_order_id, "order_TEST123")
        self.assertEqual(payment.amount_paise, 29900)
        resp = self.client.get(reverse("payments:pay", args=[payment.uid]))
        self.assertContains(resp, "rzp_test_dummy")
        self.assertNotContains(resp, "test_secret")

    def test_valid_signature_activates_plan_once(self):
        payment = self._checkout()
        data = {"razorpay_order_id": "order_TEST123", "razorpay_payment_id": "pay_ABC",
                "razorpay_signature": sign("test_secret", "order_TEST123|pay_ABC")}
        self.client.post(reverse("payments:verify"), data)
        payment.refresh_from_db()
        self.assertEqual(payment.status, Payment.Status.PAID)
        self.assertEqual(current_plan(self.owner), self.plan)
        self.assertEqual(listing_limit(self.owner), 5)
        self.assertTrue(Invoice.objects.filter(payment=payment).exists())
        # Replaying the same verification does not double-activate
        self.client.post(reverse("payments:verify"), data)
        self.assertEqual(Subscription.objects.filter(user=self.owner).count(), 1)
        self.assertEqual(Invoice.objects.count(), 1)
        self.assertEqual(self.client.get(reverse("payments:receipt", args=[payment.uid])).status_code, 200)

    def test_invalid_signature_does_not_activate(self):
        payment = self._checkout()
        self.client.post(reverse("payments:verify"), {"razorpay_order_id": "order_TEST123", "razorpay_payment_id": "pay_ABC",
                                                      "razorpay_signature": "forged"})
        payment.refresh_from_db()
        self.assertEqual(payment.status, Payment.Status.CREATED)
        self.assertFalse(Subscription.objects.exists())

    def test_other_user_cannot_verify_or_view_payment(self):
        payment = self._checkout()
        self.client.force_login(make_user(Role.OWNER))
        data = {"razorpay_order_id": "order_TEST123", "razorpay_payment_id": "pay_ABC",
                "razorpay_signature": sign("test_secret", "order_TEST123|pay_ABC")}
        self.assertEqual(self.client.post(reverse("payments:verify"), data).status_code, 404)
        self.assertEqual(self.client.get(reverse("payments:receipt", args=[payment.uid])).status_code, 403)

    def test_failure_is_recorded(self):
        payment = self._checkout()
        self.client.post(reverse("payments:failed"), {"razorpay_order_id": "order_TEST123", "description": "Card declined"})
        payment.refresh_from_db()
        self.assertEqual(payment.status, Payment.Status.FAILED)
        self.assertEqual(payment.failure_reason, "Card declined")

    def test_customer_cannot_buy_and_broker_plan_restricted(self):
        self.client.force_login(make_user())
        self.assertEqual(self.client.post(reverse("payments:checkout", args=["basic"])).status_code, 403)
        self.client.force_login(self.owner)
        self.assertEqual(self.client.post(reverse("payments:checkout", args=["broker"])).status_code, 403)

    @override_settings(RAZORPAY_KEY_ID="", RAZORPAY_KEY_SECRET="")
    def test_not_configured(self):
        resp = self.client.post(reverse("payments:checkout", args=["basic"]))
        self.assertRedirects(resp, reverse("dashboard:partner_subscription"))
        self.assertFalse(Payment.objects.exists())


class WebhookTests(TestCase):
    def setUp(self):
        self.owner = make_user(Role.OWNER)
        self.plan = SubscriptionPlan.objects.get(slug="pro")
        self.payment = Payment.objects.create(user=self.owner, plan=self.plan, amount=self.plan.price, gateway_order_id="order_W1")

    def _post(self, payload, event_id="evt_1", secret="webhook_secret"):
        body = json.dumps(payload)
        return self.client.post(reverse("payments:razorpay_webhook"), body, content_type="application/json",
                                HTTP_X_RAZORPAY_SIGNATURE=sign(secret, body), HTTP_X_RAZORPAY_EVENT_ID=event_id)

    def _payload(self, amount=59900, event="payment.captured"):
        return {"event": event, "payload": {"payment": {"entity": {"id": "pay_W1", "order_id": "order_W1", "amount": amount,
                                                                   "currency": "INR", "status": "captured"}}}}

    def test_signed_webhook_marks_paid_idempotently(self):
        self.assertEqual(self._post(self._payload()).status_code, 200)
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, Payment.Status.PAID)
        self.assertEqual(self._post(self._payload()).content, b"duplicate")
        self._post(self._payload(event="order.paid"), event_id="evt_2")
        self.assertEqual(Subscription.objects.filter(user=self.owner).count(), 1)
        self.assertEqual(WebhookEvent.objects.count(), 2)

    def test_bad_signature_rejected(self):
        self.assertEqual(self._post(self._payload(), secret="wrong").status_code, 400)
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, Payment.Status.CREATED)

    def test_amount_mismatch_not_activated(self):
        self._post(self._payload(amount=100))
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, Payment.Status.CREATED)
        self.assertEqual(WebhookEvent.objects.get().result, "amount mismatch")


class ManualPaymentTests(TestCase):
    def test_manual_payment_requires_admin_verification(self):
        from core.models import PlatformSetting

        site = PlatformSetting.load()
        site.allow_manual_payments = True
        site.save()
        owner = make_user(Role.OWNER)
        self.client.force_login(owner)
        self.client.post(reverse("payments:manual", args=["basic"]), {"manual_reference": "UPI1234567890"})
        payment = Payment.objects.get()
        self.assertEqual(payment.status, Payment.Status.PENDING_VERIFICATION)
        self.assertEqual(listing_limit(owner), 1)
        self.client.force_login(make_user(Role.ADMIN))
        self.client.post(reverse("adminpanel:payment_action", args=[payment.pk, "approve"]))
        payment.refresh_from_db()
        self.assertEqual(payment.status, Payment.Status.PAID)
        self.assertEqual(listing_limit(owner), 5)
