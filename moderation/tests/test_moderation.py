from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import Role
from core.models import AuditLog
from core.tests.factories import make_property, make_user
from moderation.models import ListingReview, Report
from notifications.models import Notification
from properties.models import Property


class ModerationTests(TestCase):
    def setUp(self):
        self.admin = make_user(Role.ADMIN)
        self.client.force_login(self.admin)
        self.prop = make_property(status="pending", with_image=True)

    def act(self, action, **data):
        return self.client.post(reverse("adminpanel:property_action", args=[self.prop.pk, action]), data)

    def test_approve_publishes_with_expiry(self):
        self.act("approve")
        self.prop.refresh_from_db()
        self.assertEqual(self.prop.status, Property.Status.ACTIVE)
        self.assertGreater(self.prop.expires_at, timezone.now())
        self.assertTrue(self.prop.is_public)
        self.assertTrue(ListingReview.objects.filter(property=self.prop, decision="approved").exists())
        self.assertTrue(AuditLog.objects.filter(action="listing.approved", actor=self.admin).exists())
        self.assertTrue(Notification.objects.filter(recipient=self.prop.owner, event="listing_approved").exists())

    def test_reject_requires_reason(self):
        self.act("reject", reason="")
        self.prop.refresh_from_db()
        self.assertEqual(self.prop.status, "pending")
        self.act("reject", reason="Photos are of a different property")
        self.prop.refresh_from_db()
        self.assertEqual(self.prop.status, "rejected")
        self.assertEqual(self.prop.moderation_note, "Photos are of a different property")

    def test_request_changes_then_owner_resubmits(self):
        self.act("request_changes", reason="Add the correct rent")
        self.prop.refresh_from_db()
        self.assertEqual(self.prop.status, "changes_requested")
        self.client.force_login(self.prop.owner)
        self.client.post(reverse("dashboard:partner_property_step", args=[self.prop.pk, 7]), {"accept_policy": "on"})
        self.prop.refresh_from_db()
        self.assertEqual(self.prop.status, "pending")

    def test_remove_flag_and_feature(self):
        self.act("approve")
        self.act("feature", days=7)
        self.prop.refresh_from_db()
        self.assertTrue(self.prop.is_featured)
        self.act("flag", reason="Duplicate phone used in many listings")
        self.prop.refresh_from_db()
        self.assertTrue(self.prop.is_suspicious)
        self.act("remove", reason="Fraudulent listing")
        self.prop.refresh_from_db()
        self.assertEqual(self.prop.status, "removed")
        self.assertIsNone(self.prop.featured_until)
        self.assertEqual(self.client.get(self.prop.get_absolute_url()).status_code, 200)  # admin preview
        self.client.logout()
        self.assertEqual(self.client.get(self.prop.get_absolute_url()).status_code, 404)

    def test_reports_workflow(self):
        self.act("approve")
        reporter = make_user()
        report = Report.objects.create(property=self.prop, reporter=reporter, reason="fraud")
        self.client.post(reverse("adminpanel:report_action", args=[report.pk, "resolve"]), {"note": "Removed listing"})
        report.refresh_from_db()
        self.assertEqual(report.status, "resolved")
        self.assertEqual(report.handled_by, self.admin)


class UserManagementTests(TestCase):
    def setUp(self):
        self.admin = make_user(Role.ADMIN)
        self.client.force_login(self.admin)

    def test_suspend_pauses_listings_and_reactivate(self):
        prop = make_property()
        owner = prop.owner
        self.client.post(reverse("adminpanel:user_action", args=[owner.pk, "suspend"]), {"reason": "Fraud reports"})
        owner.refresh_from_db()
        prop.refresh_from_db()
        self.assertFalse(owner.is_active)
        self.assertEqual(prop.status, "paused")
        self.assertTrue(AuditLog.objects.filter(action="user.suspended").exists())
        self.client.post(reverse("adminpanel:user_action", args=[owner.pk, "reactivate"]))
        owner.refresh_from_db()
        self.assertTrue(owner.is_active)

    def test_admin_cannot_suspend_self(self):
        self.client.post(reverse("adminpanel:user_action", args=[self.admin.pk, "suspend"]), {"reason": "x"})
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.is_active)

    def test_user_detail_never_shows_password_hash(self):
        user = make_user()
        resp = self.client.get(reverse("adminpanel:user_detail", args=[user.pk]))
        self.assertNotContains(resp, user.password)

    def test_verify_partner(self):
        broker = make_user(Role.BROKER)
        self.client.post(reverse("adminpanel:verification_decide", args=[broker.pk]), {"decision": "approve", "valid_days": 365})
        broker.broker_profile.refresh_from_db()
        self.assertEqual(broker.broker_profile.verification_status, "verified")
        self.client.post(reverse("adminpanel:verification_decide", args=[broker.pk]), {"decision": "reject", "note": ""})
        broker.broker_profile.refresh_from_db()
        self.assertEqual(broker.broker_profile.verification_status, "verified")  # reason required

    def test_settings_and_crud(self):
        from core.models import PlatformSetting
        from properties.models import Location

        resp = self.client.get(reverse("adminpanel:settings"))
        self.assertEqual(resp.status_code, 200)
        self.client.post(reverse("adminpanel:crud_add", args=["locations"]),
                         {"name": "Doddapura", "kind": "area", "parent": Location.objects.get(slug="bangarpet").pk,
                          "district": "Kolar", "state": "Karnataka", "is_active": "on", "display_order": 9})
        self.assertTrue(Location.objects.filter(name="Doddapura").exists())
        self.assertTrue(PlatformSetting.load().pk == 1)
