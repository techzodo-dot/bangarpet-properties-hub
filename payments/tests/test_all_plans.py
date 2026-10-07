"""Every plan, end to end: who may buy it, what PayU is asked to charge, what a payment unlocks."""
from datetime import timedelta

from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import Role
from core.tests.factories import make_user
from payments.models import Invoice, Payment
from payments.tests.payu_helpers import payu_reply
from properties import contacts
from subscriptions.models import Subscription, SubscriptionPlan
from subscriptions.services import current_plan, listing_limit, listing_terms

PARTNER_ROLES = (Role.OWNER, Role.BROKER)


def roles_of(plan):
    return {r.strip() for r in plan.for_roles.split(",") if r.strip()}


class AllPlansTests(TestCase):
    def setUp(self):
        cache.clear()
        self.paid_partner_plans = list(SubscriptionPlan.objects.filter(is_active=True, unlimited_contacts=False, price__gt=0))
        self.assertEqual({p.slug for p in self.paid_partner_plans}, {"basic", "pro", "broker"})

    def test_free_plan_is_the_default_and_cannot_be_bought(self):
        free = SubscriptionPlan.objects.get(slug="free")
        for role in PARTNER_ROLES:
            user = make_user(role)
            self.assertEqual(current_plan(user), free)
            self.assertEqual(listing_limit(user), free.listing_limit)
            self.client.force_login(user)
            self.assertEqual(self.client.post(reverse("payments:checkout", args=["free"])).status_code, 403)

    def test_every_paid_plan_charges_its_price_and_unlocks_its_limits(self):
        for plan in self.paid_partner_plans:
            for role in PARTNER_ROLES:
                with self.subTest(plan=plan.slug, role=role):
                    user = make_user(role)
                    self.client.force_login(user)
                    resp = self.client.post(reverse("payments:checkout", args=[plan.slug]))
                    if role not in roles_of(plan):
                        self.assertEqual(resp.status_code, 403)
                        continue
                    payment = Payment.objects.get(user=user)
                    self.assertEqual(payment.amount, plan.effective_price)
                    page = self.client.get(reverse("payments:pay", args=[payment.uid]))
                    self.assertContains(page, f'name="amount" value="{plan.effective_price:.2f}"')
                    self.assertContains(page, f"{plan.name} ({plan.billing_period_days} days)")
                    self.client.post(reverse("payments:payu_return"), payu_reply(payment, mihpayid=f"MIH{payment.pk}"))
                    payment.refresh_from_db()
                    self.assertEqual(payment.status, Payment.Status.PAID)
                    sub = Subscription.objects.get(user=user, status="active")
                    self.assertEqual(sub.plan, plan)
                    self.assertAlmostEqual(sub.ends_at - sub.starts_at, timedelta(days=plan.billing_period_days),
                                           delta=timedelta(seconds=5))
                    self.assertEqual(current_plan(user), plan)
                    self.assertEqual(listing_limit(user), plan.listing_limit)
                    days, priority = listing_terms(user)
                    self.assertEqual(days, plan.listing_duration_days)
                    self.assertEqual(priority, plan.visibility_priority if plan.has_priority_visibility else 0)
                    self.assertEqual(Invoice.objects.get(payment=payment).amount, plan.effective_price)
                    self.assertEqual(self.client.get(reverse("payments:receipt", args=[payment.uid])).status_code, 200)

    def test_every_paid_plan_can_be_bought_offline_and_approved_by_admin(self):
        admin = make_user(Role.ADMIN)
        for plan in self.paid_partner_plans:
            role = Role.OWNER if Role.OWNER in roles_of(plan) else Role.BROKER
            with self.subTest(plan=plan.slug):
                user = make_user(role)
                self.client.force_login(user)
                self.client.post(reverse("payments:request", args=[plan.slug]), {"phone": "9845012345"})
                payment = Payment.objects.get(user=user, status=Payment.Status.PENDING_VERIFICATION)
                self.assertEqual(payment.amount, plan.effective_price)
                self.client.force_login(admin)
                self.client.post(reverse("adminpanel:payment_action", args=[payment.pk, "approve"]))
                self.assertEqual(current_plan(user), plan)
                self.assertEqual(listing_limit(user), plan.listing_limit)

    def test_discount_is_charged_when_active(self):
        plan = SubscriptionPlan.objects.get(slug="pro")
        plan.discount_percent = 20
        plan.discount_ends_at = timezone.now() + timedelta(days=3)
        plan.save()
        user = make_user(Role.OWNER)
        self.client.force_login(user)
        self.client.post(reverse("payments:checkout", args=["pro"]))
        self.assertEqual(Payment.objects.get(user=user).amount, plan.effective_price)
        self.assertEqual(plan.effective_price, plan.price * 80 / 100)

    def test_contact_pass_is_for_customers_only_and_unlocks_contacts(self):
        plan = SubscriptionPlan.objects.get(slug="contact-pass")
        self.assertEqual((plan.price, plan.billing_period_days, roles_of(plan)), (99, 30, {"customer"}))
        owner = make_user(Role.OWNER)
        self.client.force_login(owner)
        self.assertEqual(self.client.post(reverse("payments:checkout", args=["contact-pass"])).status_code, 403)
        customer = make_user(Role.CUSTOMER)
        self.client.force_login(customer)
        self.assertEqual(self.client.post(reverse("payments:checkout", args=["basic"])).status_code, 403)
        self.client.post(reverse("payments:contact_pass_subscribe"))
        payment = Payment.objects.get(user=customer)
        self.assertEqual(payment.amount, plan.effective_price)
        self.client.post(reverse("payments:payu_return"), payu_reply(payment))
        self.assertTrue(contacts.active_pass(customer))
        # The pass is not a listing plan.
        self.assertEqual(current_plan(customer).slug, "free")

    def test_pricing_page_shows_every_listing_plan_with_its_price(self):
        page = self.client.get(reverse("subscriptions:pricing"))
        for plan in SubscriptionPlan.objects.filter(is_active=True, unlimited_contacts=False):
            self.assertContains(page, plan.name)
            if plan.price:
                self.assertContains(page, f"{plan.price:,.0f}")
        self.assertNotContains(page, "Contact Pass")
