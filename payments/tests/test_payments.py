import hashlib
import json
from unittest import mock

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import Role
from core.tests.factories import make_user
from payments.models import Invoice, Payment, WebhookEvent
from payments.tests.payu_helpers import payu_reply
from subscriptions.models import Subscription, SubscriptionPlan
from subscriptions.services import current_plan, listing_limit


class CheckoutTests(TestCase):
    def setUp(self):
        cache.clear()
        self.owner = make_user(Role.OWNER)
        self.client.force_login(self.owner)
        self.plan = SubscriptionPlan.objects.get(slug="basic")

    def _checkout(self):
        resp = self.client.post(reverse("payments:checkout", args=["basic"]))
        payment = Payment.objects.get()
        self.assertRedirects(resp, reverse("payments:pay", args=[payment.uid]))
        return payment

    def test_payment_created_server_side_with_correct_amount(self):
        payment = self._checkout()
        self.assertEqual(payment.gateway, Payment.Gateway.PAYU)
        self.assertEqual(payment.amount, self.plan.price)
        resp = self.client.get(reverse("payments:pay", args=[payment.uid]))
        self.assertContains(resp, 'name="key" value="testKey1"')
        self.assertContains(resp, 'name="amount" value="299.00"')
        self.assertContains(resp, "test mode")
        self.assertNotContains(resp, "testSalt1")

    def test_request_hash_matches_payu_formula(self):
        from payments.services import payu_checkout_fields

        payment = self._checkout()
        f = payu_checkout_fields(payment)
        expected = hashlib.sha512("|".join([f["key"], f["txnid"], f["amount"], f["productinfo"], f["firstname"], f["email"],
                                            f["udf1"], "", "", "", "", "", "", "", "", "", "testSalt1"]).encode()).hexdigest()
        self.assertEqual(f["hash"], expected)

    def test_valid_reply_activates_plan_once(self):
        payment = self._checkout()
        reply = payu_reply(payment)
        resp = self.client.post(reverse("payments:payu_return"), reply)
        self.assertRedirects(resp, reverse("payments:receipt", args=[payment.uid]), fetch_redirect_response=False)
        payment.refresh_from_db()
        self.assertEqual(payment.status, Payment.Status.PAID)
        self.assertEqual(current_plan(self.owner), self.plan)
        self.assertEqual(listing_limit(self.owner), 5)
        self.assertTrue(Invoice.objects.filter(payment=payment).exists())
        # Replaying the same reply does not double-activate
        self.client.post(reverse("payments:payu_return"), reply)
        self.assertEqual(Subscription.objects.filter(user=self.owner).count(), 1)
        self.assertEqual(Invoice.objects.count(), 1)
        self.assertEqual(self.client.get(reverse("payments:receipt", args=[payment.uid])).status_code, 200)

    def test_forged_reply_does_not_activate(self):
        payment = self._checkout()
        reply = payu_reply(payment)
        reply["hash"] = "0" * 128
        self.client.post(reverse("payments:payu_return"), reply)
        payment.refresh_from_db()
        self.assertEqual(payment.status, Payment.Status.CREATED)
        self.assertFalse(Subscription.objects.exists())

    def test_reply_with_changed_amount_does_not_activate(self):
        payment = self._checkout()
        self.client.post(reverse("payments:payu_return"), payu_reply(payment, amount="1.00"))
        payment.refresh_from_db()
        self.assertEqual(payment.status, Payment.Status.CREATED)

    def test_other_user_cannot_open_pay_page_or_receipt(self):
        payment = self._checkout()
        self.client.force_login(make_user(Role.OWNER))
        self.assertEqual(self.client.get(reverse("payments:pay", args=[payment.uid])).status_code, 404)
        self.assertEqual(self.client.get(reverse("payments:receipt", args=[payment.uid])).status_code, 403)

    def test_failure_is_recorded(self):
        payment = self._checkout()
        self.client.post(reverse("payments:payu_return"), payu_reply(payment, status="failure", error_Message="Card declined"))
        payment.refresh_from_db()
        self.assertEqual(payment.status, Payment.Status.FAILED)
        self.assertEqual(payment.failure_reason, "Card declined")

    def test_customer_cannot_buy_and_broker_plan_restricted(self):
        self.client.force_login(make_user())
        self.assertEqual(self.client.post(reverse("payments:checkout", args=["basic"])).status_code, 403)
        self.client.force_login(self.owner)
        self.assertEqual(self.client.post(reverse("payments:checkout", args=["broker"])).status_code, 403)

    def test_owners_can_always_choose_to_pay_offline(self):
        page = self.client.get(reverse("dashboard:partner_subscription"))
        self.assertContains(page, reverse("payments:checkout", args=["basic"]))
        self.assertContains(page, reverse("payments:request", args=["basic"]))
        payment = self._checkout()
        pay_page = self.client.get(reverse("payments:pay", args=[payment.uid]))
        self.assertContains(pay_page, "Trouble paying online?")
        self.assertContains(pay_page, reverse("payments:request", args=["basic"]))
        # The offline request works while an online attempt is still open.
        resp = self.client.post(reverse("payments:request", args=["basic"]), {"phone": "9845012345", "note": "PayU failed"})
        self.assertRedirects(resp, reverse("dashboard:partner_payments"), fetch_redirect_response=False)
        self.assertTrue(Payment.objects.filter(user=self.owner, status=Payment.Status.PENDING_VERIFICATION).exists())

    @override_settings(PAYU_MERCHANT_KEY="", PAYU_MERCHANT_SALT="")
    def test_not_configured_falls_back_to_plan_request(self):
        resp = self.client.post(reverse("payments:checkout", args=["basic"]))
        self.assertRedirects(resp, reverse("payments:request", args=["basic"]))
        self.assertFalse(Payment.objects.exists())

    @override_settings(PAYU_MODE="live")
    def test_live_mode_uses_live_payu(self):
        payment = self._checkout()
        resp = self.client.get(reverse("payments:pay", args=[payment.uid]))
        self.assertContains(resp, 'action="https://secure.payu.in/_payment"')
        self.assertNotContains(resp, "test mode")


class WebhookTests(TestCase):
    def setUp(self):
        self.owner = make_user(Role.OWNER)
        self.plan = SubscriptionPlan.objects.get(slug="pro")
        self.payment = Payment.objects.create(user=self.owner, plan=self.plan, amount=self.plan.price,
                                              gateway=Payment.Gateway.PAYU, gateway_order_id="BPHW1")

    def _post(self, data):
        return self.client.post(reverse("payments:payu_webhook"), data)

    def test_signed_webhook_marks_paid_idempotently(self):
        self.assertEqual(self._post(payu_reply(self.payment)).status_code, 200)
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, Payment.Status.PAID)
        self.assertEqual(self._post(payu_reply(self.payment)).content, b"duplicate")
        self.assertEqual(Subscription.objects.filter(user=self.owner).count(), 1)
        self.assertEqual(WebhookEvent.objects.count(), 1)

    def test_json_webhook_is_accepted(self):
        resp = self.client.post(reverse("payments:payu_webhook"), json.dumps(payu_reply(self.payment)), content_type="application/json")
        self.assertEqual(resp.status_code, 200)
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, Payment.Status.PAID)

    def test_bad_hash_rejected(self):
        self.assertEqual(self._post(payu_reply(self.payment, salt="wrong")).status_code, 400)
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, Payment.Status.CREATED)

    def test_amount_mismatch_not_activated(self):
        self._post(payu_reply(self.payment, amount="1.00"))
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, Payment.Status.CREATED)
        self.assertEqual(WebhookEvent.objects.get().result, "amount mismatch")


class ReconcileTests(TestCase):
    def test_daily_check_confirms_payments_paid_at_payu(self):
        from datetime import timedelta

        from django.utils import timezone

        from payments.services import reconcile_payu_payments

        owner = make_user(Role.OWNER)
        plan = SubscriptionPlan.objects.get(slug="basic")
        payment = Payment.objects.create(user=owner, plan=plan, amount=plan.price, gateway=Payment.Gateway.PAYU,
                                         gateway_order_id="BPHR1")
        Payment.objects.filter(pk=payment.pk).update(created_at=timezone.now() - timedelta(hours=2))
        details = {"status": "success", "amt": "299.00", "mihpayid": "4039937155R1", "txnid": "BPHR1"}
        with mock.patch("payments.payu.verify_payment", return_value=details):
            self.assertEqual(reconcile_payu_payments(), "1 paid, 0 failed")
        payment.refresh_from_db()
        self.assertEqual(payment.status, Payment.Status.PAID)
        self.assertEqual(current_plan(owner), plan)


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


@override_settings(PAYU_MERCHANT_KEY="", PAYU_MERCHANT_SALT="")
class PlanRequestTests(TestCase):
    """Without an online gateway, partners can still choose a paid plan."""

    def setUp(self):
        cache.clear()
        self.owner = make_user(Role.OWNER)
        self.client.force_login(self.owner)

    def test_subscription_page_offers_working_choose_buttons(self):
        resp = self.client.get(reverse("dashboard:partner_subscription"))
        self.assertContains(resp, reverse("payments:request", args=["basic"]))
        self.assertNotContains(resp, reverse("payments:checkout", args=["basic"]))

    def test_request_creates_pending_payment_and_admin_approval_activates_plan(self):
        page = self.client.get(reverse("payments:request", args=["basic"]))
        self.assertContains(page, "Choose the Basic plan")
        resp = self.client.post(reverse("payments:request", args=["basic"]), {"phone": "9876543210", "note": "Call after 5pm"})
        self.assertRedirects(resp, reverse("dashboard:partner_payments"))
        payment = Payment.objects.get()
        self.assertEqual(payment.status, Payment.Status.PENDING_VERIFICATION)
        self.assertEqual(payment.gateway, Payment.Gateway.MANUAL)
        self.assertIn("9876543210", payment.manual_reference)
        self.assertEqual(listing_limit(self.owner), 1)
        # A second request for the same plan does not create a duplicate.
        self.client.post(reverse("payments:request", args=["basic"]), {"phone": "9876543210"})
        self.assertEqual(Payment.objects.count(), 1)

        self.client.force_login(make_user(Role.ADMIN))
        self.client.post(reverse("adminpanel:payment_action", args=[payment.pk, "approve"]))
        payment.refresh_from_db()
        self.assertEqual(payment.status, Payment.Status.PAID)
        self.assertEqual(listing_limit(self.owner), 5)

    def test_request_requires_phone_and_partner_account(self):
        resp = self.client.post(reverse("payments:request", args=["basic"]), {"phone": ""})
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(Payment.objects.exists())
        self.client.force_login(make_user())
        self.assertEqual(self.client.get(reverse("payments:request", args=["basic"])).status_code, 403)


class PricingPageTests(TestCase):
    def test_visitors_get_choose_buttons_that_return_to_the_plan_page(self):
        resp = self.client.get(reverse("subscriptions:pricing"))
        self.assertContains(resp, "Choose Basic")
        self.assertContains(resp, "?type=owner&amp;next=/partner/subscription/")

    def test_partners_can_choose_directly(self):
        self.client.force_login(make_user(Role.OWNER))
        resp = self.client.get(reverse("subscriptions:pricing"))
        self.assertContains(resp, reverse("payments:checkout", args=["basic"]))

    def test_signup_returns_to_next(self):
        resp = self.client.post(reverse("accounts:register") + "?type=owner", {
            "account_type": "owner", "full_name": "New Owner", "email": "new.owner@example.com",
            "phone": "9876543211", "password1": "Strong#Pass2024", "password2": "Strong#Pass2024",
            "accept_terms": "on", "next": "/partner/subscription/",
        })
        # Sign-up first asks for the emailed code, then carries on to the plan page.
        self.assertRedirects(resp, "/accounts/verify/?next=%2Fpartner%2Fsubscription%2F", fetch_redirect_response=False)
        page = self.client.get(resp["Location"])
        self.assertContains(page, 'href="/partner/subscription/"')
