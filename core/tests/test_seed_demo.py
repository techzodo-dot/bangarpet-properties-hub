from io import StringIO

from django.core.management import CommandError, call_command
from django.test import TestCase

from accounts.models import User
from enquiries.models import Enquiry, PropertyVisit
from moderation.models import Report
from payments.models import Payment
from properties.models import Property, PropertyImage
from subscriptions.models import SubscriptionPlan
from subscriptions.services import listing_limit, used_listing_slots


class SeedDemoTests(TestCase):
    def run_cmd(self, *args):
        call_command("seed_demo", "--force", *args, stdout=StringIO())

    def test_refuses_without_debug(self):
        with self.assertRaises(CommandError):
            call_command("seed_demo", stdout=StringIO())

    def test_seed_creates_accounts_listings_and_activity(self):
        self.run_cmd()
        self.assertEqual(User.objects.filter(email__endswith="@demo.bph.local").count(), 4)
        self.assertTrue(User.objects.get(email="admin@demo.bph.local").is_platform_admin)
        self.assertEqual(Property.objects.public().filter(is_demo=True).count(), 10)
        self.assertEqual(Property.objects.filter(is_demo=True, status="pending").count(), 1)
        self.assertTrue(all(p.title.startswith("[DEMO]") for p in Property.objects.all()))
        self.assertFalse(PropertyImage.objects.filter(property__is_demo=True).count() < 20)
        self.assertEqual(Enquiry.objects.count(), 4)
        self.assertEqual(set(PropertyVisit.objects.values_list("status", flat=True)), {"requested", "scheduled"})
        self.assertEqual(Report.objects.count(), 1)
        for email in ("owner@demo.bph.local", "broker@demo.bph.local"):
            user = User.objects.get(email=email)
            self.assertLessEqual(used_listing_slots(user), listing_limit(user))

    def test_rerun_is_idempotent_and_reset_and_clear_work(self):
        self.run_cmd()
        self.run_cmd()
        self.assertEqual(Property.objects.count(), 11)
        owner = User.objects.get(email="owner@demo.bph.local")
        Payment.objects.create(user=owner, plan=SubscriptionPlan.objects.get(slug="basic"), amount=299)
        self.run_cmd("--reset")
        self.assertEqual(Property.objects.count(), 11)
        self.run_cmd("--clear")
        self.assertEqual(Property.objects.count(), 0)
        self.assertEqual(PropertyImage.objects.count(), 0)
        self.assertFalse(User.objects.filter(email__endswith="@demo.bph.local").exists())

    def test_clear_leaves_real_data_alone(self):
        from core.tests.factories import make_property

        real = make_property(title="A real owner's genuine listing")
        self.run_cmd()
        self.run_cmd("--clear")
        self.assertTrue(Property.objects.filter(pk=real.pk).exists())
