from decimal import Decimal

from django.core.cache import cache
from django.forms.models import model_to_dict
from django.test import TestCase
from django.urls import reverse

from core.models import PlatformSetting
from core.tests.factories import make_user
from subscriptions.models import SubscriptionPlan


class PricingSettingsTests(TestCase):
    def setUp(self):
        cache.clear()
        self.admin = make_user("admin")
        self.client.force_login(self.admin)

    def tearDown(self):
        cache.clear()

    def test_edit_heading_and_description_in_admin(self):
        site = PlatformSetting.load()
        data = {key: value if value is not None else "" for key, value in model_to_dict(site).items() if key != "logo"}
        data.update(pricing_heading="Choose your listing plan", pricing_description="Flexible plans for local property owners.")
        response = self.client.post(reverse("adminpanel:settings"), data)
        self.assertEqual(response.status_code, 302)
        self.client.logout()
        page = self.client.get(reverse("subscriptions:pricing"))
        self.assertContains(page, "Choose your listing plan")
        self.assertContains(page, "Flexible plans for local property owners.")
        self.assertNotContains(page, "Start free. Upgrade when you need more listings.")

    def test_admin_plan_edit_updates_public_details(self):
        plan = SubscriptionPlan.objects.get(slug="basic")
        data = {key: value if value is not None else "" for key, value in model_to_dict(plan).items()}
        data.update(name="Local Plus", description="Our custom owner plan", price="450.00", listing_limit=9,
                    listing_duration_days=42, billing_period_days=60, is_recommended=True,
                    for_roles="owner", has_performance_stats=True,
                    features="Up to 5 active listings\nCustom support benefit")
        response = self.client.post(reverse("adminpanel:crud_edit", args=["plans", plan.pk]), data)
        self.assertEqual(response.status_code, 302)
        page = self.client.get(reverse("subscriptions:pricing"))
        for text in ("Local Plus", "Our custom owner plan", "450.00", "81.00", "531.00", "Up to 9 active listings",
                     "Listings stay live 42 days", "60 days", "Custom support benefit", "Recommended", "For Owners"):
            self.assertContains(page, text)
        self.assertNotContains(page, "Up to 5 active listings")

    def test_inactive_plans_hidden_and_order_from_admin(self):
        plan = SubscriptionPlan.objects.get(slug="basic")
        plan.is_active = False
        plan.save()
        first = SubscriptionPlan.objects.get(slug="broker")
        SubscriptionPlan.objects.all().update(display_order=10)
        first.display_order = 0
        first.save()
        page = self.client.get(reverse("subscriptions:pricing"))
        plans = page.context["plans"]
        self.assertEqual(plans[0].pk, first.pk)
        self.assertNotIn(plan.pk, [p.pk for p in plans])
        self.assertNotContains(page, "Most chosen")

    def test_admin_shows_same_gst_and_total(self):
        page = self.client.get(reverse("adminpanel:subscriptions"))
        for text in ("299.00", "GST (18%)", "53.82", "352.82", "Total payable"):
            self.assertContains(page, text)
        page = self.client.get(reverse("adminpanel:crud_list", args=["plans"]))
        self.assertContains(page, "GST (₹)")
        self.assertContains(page, "Total (₹)")

    def test_no_automatic_recommendation_or_free_signup_claim(self):
        SubscriptionPlan.objects.filter(price=0).update(is_active=False)
        page = self.client.get(reverse("subscriptions:pricing"))
        self.assertNotContains(page, "Get started free")
        self.assertNotContains(page, ">Recommended<")

    def test_enabled_capabilities_control_stock_bullets(self):
        plan = SubscriptionPlan.objects.get(slug="pro")
        plan.has_performance_stats = False
        plan.has_priority_visibility = False
        self.assertNotIn("Listing performance statistics", plan.card_features)
        self.assertNotIn("Priority visibility in default sorting", plan.card_features)
        self.assertIn("Standard visibility", plan.card_features)
