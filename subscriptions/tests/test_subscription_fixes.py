"""Regression tests for the subscription review: plan kinds, switching credit, invoices, admin forms."""
from datetime import timedelta
from decimal import Decimal

from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import Role
from adminpanel.forms import GrantSubscriptionForm, PlanForm
from core.tests.factories import make_user
from payments.models import Invoice, Payment
from payments.services import mark_paid
from properties import contacts
from subscriptions.models import Subscription, SubscriptionPlan
from subscriptions.services import activate_subscription, contact_pass_plan, current_plan, current_subscription


def plan(slug):
    return SubscriptionPlan.objects.get(slug=slug)


def pay(user, p):
    payment = Payment.objects.create(user=user, plan=p, amount=p.effective_price, gateway=Payment.Gateway.MANUAL,
                                     status=Payment.Status.PENDING_VERIFICATION)
    return mark_paid(payment.pk, source="test")[0]


class PlanKindsAreSeparateTests(TestCase):
    def test_contact_pass_and_listing_plan_do_not_replace_each_other(self):
        user = make_user(Role.CUSTOMER)
        activate_subscription(user, contact_pass_plan(), amount_paid=99)
        user.role = Role.OWNER  # the customer later lists a property
        user.save()
        activate_subscription(user, plan("basic"), amount_paid=299)
        self.assertTrue(contacts.active_pass(user))
        self.assertEqual(current_plan(user), plan("basic"))
        self.assertEqual(current_subscription(user).plan, plan("basic"))
        self.assertEqual(Subscription.objects.filter(user=user, status="active").count(), 2)

    def test_partner_page_ignores_the_contact_pass(self):
        user = make_user(Role.CUSTOMER)
        activate_subscription(user, contact_pass_plan(), amount_paid=99)
        self.assertIsNone(current_subscription(user))
        self.assertEqual(current_plan(user).slug, "free")


class SwitchingPlansTests(TestCase):
    def test_unused_paid_days_are_credited_on_a_new_plan(self):
        owner = make_user(Role.OWNER)
        basic = activate_subscription(owner, plan("basic"), amount_paid=Decimal("299"))
        # Ten days into a 30-day Basic plan, the owner upgrades to Pro.
        Subscription.objects.filter(pk=basic.pk).update(starts_at=basic.starts_at - timedelta(days=10),
                                                        ends_at=basic.ends_at - timedelta(days=10))
        pro = activate_subscription(owner, plan("pro"), amount_paid=Decimal("599"))
        # 20 unused days of Basic are worth about ₹199, i.e. 9 extra days of Pro (₹599 / 30 days).
        self.assertEqual(pro.carried_over_days, 9)
        self.assertAlmostEqual(pro.ends_at - pro.starts_at, timedelta(days=39), delta=timedelta(seconds=5))
        self.assertIn("credited from the unused Basic plan", pro.notes)
        basic.refresh_from_db()
        self.assertEqual(basic.status, Subscription.Status.SUPERSEDED)

    def test_granted_free_plans_carry_no_credit(self):
        owner = make_user(Role.OWNER)
        activate_subscription(owner, plan("pro"), amount_paid=0, notes="promo")
        basic = activate_subscription(owner, plan("basic"), amount_paid=Decimal("299"))
        self.assertEqual(basic.carried_over_days, 0)
        self.assertAlmostEqual(basic.ends_at - basic.starts_at, timedelta(days=30), delta=timedelta(seconds=5))


class InvoicePeriodTests(TestCase):
    def test_renewal_invoice_covers_only_the_new_period(self):
        owner = make_user(Role.OWNER)
        first = pay(owner, plan("basic"))
        second = pay(owner, plan("basic"))
        inv1, inv2 = Invoice.objects.get(payment=first), Invoice.objects.get(payment=second)
        self.assertEqual(inv2.period_start, inv1.period_end)
        self.assertAlmostEqual(inv2.period_end - inv2.period_start, timedelta(days=30), delta=timedelta(seconds=5))
        self.assertEqual(Invoice.objects.get(payment=pay(make_user(), contact_pass_plan())).description, "Contact Pass - 30 days")


class PlanFormTests(TestCase):
    def data(self, p, **changes):
        fields = PlanForm.Meta.fields
        data = {f: getattr(p, f) for f in fields if getattr(p, f) is not None and getattr(p, f) is not False}
        data.update(changes)
        return {k: v for k, v in data.items() if v is not False}

    def test_contact_pass_can_be_edited(self):
        p = contact_pass_plan()
        form = PlanForm(self.data(p, price="149"), instance=p)
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        self.assertEqual(contact_pass_plan().price, 149)

    def test_roles_must_match_the_plan_kind(self):
        p = plan("basic")
        self.assertIn("for_roles", PlanForm(self.data(p, for_roles="customer"), instance=p).errors)
        cp = contact_pass_plan()
        self.assertIn("for_roles", PlanForm(self.data(cp, for_roles="owner"), instance=cp).errors)

    def test_making_a_plan_default_only_happens_on_save(self):
        free = plan("free")
        self.assertTrue(free.is_default)
        form = PlanForm(self.data(plan("basic"), is_default="on"), instance=plan("basic"))
        self.assertFalse(form.is_valid())  # a paid plan cannot be the default
        free.refresh_from_db()
        self.assertTrue(free.is_default)


class GrantFormTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_contact_pass_goes_to_customers_and_listing_plans_to_partners(self):
        customer, owner = make_user(Role.CUSTOMER), make_user(Role.OWNER)
        cp = contact_pass_plan()
        self.assertTrue(GrantSubscriptionForm({"email": customer.email, "plan": cp.pk, "notes": "support"}).is_valid())
        self.assertFalse(GrantSubscriptionForm({"email": owner.email, "plan": cp.pk, "notes": "x"}).is_valid())
        self.assertFalse(GrantSubscriptionForm({"email": customer.email, "plan": plan("basic").pk, "notes": "x"}).is_valid())
        self.assertFalse(GrantSubscriptionForm({"email": owner.email, "plan": plan("free").pk, "notes": "x"}).is_valid())

    def test_only_admins_can_grant(self):
        owner = make_user(Role.OWNER)
        payload = {"email": owner.email, "plan": plan("basic").pk, "notes": "promo"}
        self.client.force_login(make_user(Role.STAFF, staff_permissions=["payments"]))
        self.assertNotContains(self.client.get(reverse("adminpanel:subscriptions")), "Grant a plan manually")
        self.assertEqual(self.client.post(reverse("adminpanel:subscriptions"), payload).status_code, 403)
        self.client.force_login(make_user(Role.ADMIN))
        self.client.post(reverse("adminpanel:subscriptions"), payload)
        self.assertEqual(current_plan(owner), plan("basic"))
