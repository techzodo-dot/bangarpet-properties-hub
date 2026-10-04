from datetime import timedelta
from io import StringIO

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from accounts.models import Role
from core.tests.factories import make_property, make_user
from notifications.models import Notification
from properties.models import Property
from subscriptions.models import Subscription, SubscriptionPlan
from subscriptions.services import activate_subscription, can_occupy_slot, current_plan, listing_terms


class SubscriptionServiceTests(TestCase):
    def setUp(self):
        self.owner = make_user(Role.OWNER)
        self.basic = SubscriptionPlan.objects.get(slug="basic")
        self.pro = SubscriptionPlan.objects.get(slug="pro")

    def test_default_free_plan(self):
        self.assertEqual(current_plan(self.owner).slug, "free")
        make_property(owner=self.owner)
        self.assertFalse(can_occupy_slot(self.owner))

    def test_same_plan_extends_and_upgrade_supersedes(self):
        sub = activate_subscription(self.owner, self.basic)
        end = sub.ends_at
        again = activate_subscription(self.owner, self.basic)
        self.assertEqual(again.pk, sub.pk)
        self.assertEqual(again.ends_at, end + timedelta(days=30))
        pro = activate_subscription(self.owner, self.pro)
        sub.refresh_from_db()
        self.assertEqual(sub.status, Subscription.Status.SUPERSEDED)
        self.assertEqual(current_plan(self.owner), self.pro)
        self.assertEqual(listing_terms(self.owner), (45, 1))
        self.assertTrue(pro.is_current)

    def test_discount_price(self):
        self.basic.discount_percent = 20
        self.basic.save()
        self.assertEqual(str(self.basic.effective_price), "239.20")


class ScheduledTaskTests(TestCase):
    def test_subscription_and_listing_expiry(self):
        owner = make_user(Role.OWNER)
        sub = activate_subscription(owner, SubscriptionPlan.objects.get(slug="basic"))
        Subscription.objects.filter(pk=sub.pk).update(ends_at=timezone.now() - timedelta(minutes=1))
        expired_prop = make_property(owner=owner, expires_at=timezone.now() - timedelta(minutes=5))
        expiring = make_property(owner=owner, expires_at=timezone.now() + timedelta(days=2))
        call_command("run_scheduled_tasks", stdout=StringIO())
        sub.refresh_from_db()
        expired_prop.refresh_from_db()
        expiring.refresh_from_db()
        self.assertEqual(sub.status, Subscription.Status.EXPIRED)
        self.assertEqual(current_plan(owner).slug, "free")
        self.assertEqual(expired_prop.status, Property.Status.EXPIRED)
        self.assertIsNotNone(expiring.expiry_reminder_sent_at)
        self.assertTrue(Notification.objects.filter(recipient=owner, event="listing_expiring").exists())
        # Running again does not send duplicate reminders
        count = Notification.objects.count()
        call_command("run_scheduled_tasks", stdout=StringIO())
        self.assertEqual(Notification.objects.count(), count)

    def test_purge_documents_after_retention(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        from accounts.models import VerificationDocument

        owner = make_user(Role.OWNER)
        doc = VerificationDocument.objects.create(user=owner, doc_type="pan", status="accepted",
                                                  file=SimpleUploadedFile("p.pdf", b"%PDF-1.4"),
                                                  retain_until=timezone.now() - timedelta(days=1))
        path = doc.file.path
        call_command("run_scheduled_tasks", "--only", "documents", stdout=StringIO())
        doc.refresh_from_db()
        self.assertFalse(doc.file)
        self.assertIsNotNone(doc.file_purged_at)
        import os

        self.assertFalse(os.path.exists(path))
