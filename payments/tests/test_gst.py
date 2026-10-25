from decimal import Decimal

from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from core.models import PlatformSetting
from core.tests.factories import make_user
from payments.models import Invoice, Payment
from payments.services import mark_paid, start_payu_payment, process_payu_result, payu_checkout_fields
from payments.tests.payu_helpers import payu_reply
from subscriptions.models import SubscriptionPlan


class GstPaymentTests(TestCase):
    def setUp(self):
        cache.clear()
        self.site = PlatformSetting.load()
        self.site.gst_percent = Decimal("18")
        self.site.upi_id = "bph@okhdfcbank"
        self.site.upi_payee_name = "BPH"
        self.site.save()

    def tearDown(self):
        cache.clear()

    def test_all_roles_checkout_total_and_tax_snapshots(self):
        for role, slug in (("owner", "basic"), ("broker", "broker"), ("customer", "contact-pass")):
            plan = SubscriptionPlan.objects.filter(unlimited_contacts=True).first() if role == "customer" else SubscriptionPlan.objects.get(slug=slug)
            user = make_user(role)
            payment = start_payu_payment(user, plan)
            tax = (plan.effective_price * Decimal("0.18")).quantize(Decimal("0.01"))
            self.assertEqual(payment.amount, plan.effective_price + tax)
            self.assertEqual(payment.subtotal, plan.effective_price)
            self.assertEqual(payment.gst_amount, tax)
            self.assertEqual(payu_checkout_fields(payment)["amount"], f"{payment.amount:.2f}")
            self.client.force_login(user)
            page = self.client.get(reverse("payments:pay", args=[payment.uid]))
            self.assertContains(page, "GST (18%)")
            # Old pre-tax amount cannot activate the new payment.
            _, result = process_payu_result(payu_reply(payment, amount=str(payment.subtotal)))
            self.assertEqual(result, "amount mismatch")
            _, result = process_payu_result(payu_reply(payment, mihpayid=str(payment.pk)))
            self.assertEqual(result, "paid")
            self.assertEqual(Invoice.objects.get(payment=payment).amount, payment.amount)

    def test_discount_rounding_and_free_plan(self):
        plan = SubscriptionPlan.objects.get(slug="basic")
        plan.price = Decimal("299")
        plan.discount_percent = 20
        self.assertEqual(plan.payment_amounts, {"subtotal": Decimal("239.20"), "gst_percent": Decimal("18"), "gst_amount": Decimal("43.06"), "amount": Decimal("282.26")})
        self.assertEqual(SubscriptionPlan.objects.get(slug="free").total_price, 0)

    def test_rate_change_does_not_rewrite_paid_or_pending_payments(self):
        plan = SubscriptionPlan.objects.get(slug="basic")
        user = make_user("owner")
        payment = start_payu_payment(user, plan)
        self.site.gst_percent = 5
        self.site.save()
        payment, _ = mark_paid(payment.pk)
        self.assertEqual(payment.gst_percent, 18)
        self.assertEqual(payment.amount, Decimal("352.82"))
        self.assertEqual(payment.subscription.amount_paid, Decimal("299"))
        self.client.force_login(user)
        page = self.client.get(reverse("payments:receipt", args=[payment.uid]))
        self.assertContains(page, "GST (18%)")
        self.assertContains(page, "53.82")
        self.assertEqual(start_payu_payment(user, plan).gst_percent, 5)

    def test_manual_and_request_payments_include_gst(self):
        user = make_user("owner")
        self.client.force_login(user)
        plan = SubscriptionPlan.objects.get(slug="basic")
        page = self.client.get(reverse("payments:manual", args=[plan.slug]))
        self.assertContains(page, "am=352.82")
        self.client.post(reverse("payments:manual", args=[plan.slug]), {"manual_reference": "GST-TEST-123"})
        payment = Payment.objects.get(user=user)
        self.assertEqual(payment.amount, Decimal("352.82"))
        self.assertEqual(payment.gst_amount, Decimal("53.82"))

        self.client.force_login(make_user("broker"))
        self.client.post(reverse("payments:request", args=[plan.slug]), {"phone": "9876543210", "note": "GST test"})
        requested = Payment.objects.filter(manual_reference__startswith="Plan request").get()
        self.assertEqual(requested.amount, Decimal("352.82"))
        self.assertEqual(requested.gst_amount, Decimal("53.82"))

    def test_historical_payment_has_no_retroactive_gst(self):
        user = make_user("owner")
        payment = Payment.objects.create(user=user, plan=SubscriptionPlan.objects.get(slug="basic"), amount=299)
        payment, _ = mark_paid(payment.pk)
        self.assertEqual(payment.base_amount, 299)
        self.assertEqual(payment.gst_amount, 0)
        self.assertEqual(Invoice.objects.get(payment=payment).amount, 299)
