from django.test import TestCase
from django.urls import reverse

from accounts.models import Role
from core.tests.factories import make_property, make_user

CUSTOMER_URLS = ["dashboard:customer_overview", "dashboard:customer_enquiries", "dashboard:customer_favourites",
                 "dashboard:customer_visits", "dashboard:customer_profile"]
PARTNER_URLS = ["dashboard:partner_overview", "dashboard:partner_properties", "dashboard:partner_property_add",
                "dashboard:partner_enquiries", "dashboard:partner_visits", "dashboard:partner_subscription",
                "dashboard:partner_payments", "dashboard:partner_profile"]
ADMIN_URLS = ["adminpanel:dashboard", "adminpanel:users", "adminpanel:properties", "adminpanel:approvals",
              "adminpanel:subscriptions", "adminpanel:payments", "adminpanel:reports", "adminpanel:banners",
              "adminpanel:settings", "adminpanel:audit_logs", "adminpanel:verifications", "adminpanel:enquiries"]


class RolePermissionTests(TestCase):
    def test_anonymous_redirected_to_login(self):
        for name in CUSTOMER_URLS + PARTNER_URLS + ADMIN_URLS:
            resp = self.client.get(reverse(name))
            self.assertEqual(resp.status_code, 302, name)
            self.assertIn(reverse("accounts:login"), resp["Location"])

    def test_customer_cannot_access_partner_or_admin(self):
        self.client.force_login(make_user(Role.CUSTOMER))
        for name in CUSTOMER_URLS:
            self.assertEqual(self.client.get(reverse(name)).status_code, 200, name)
        for name in PARTNER_URLS + ADMIN_URLS:
            self.assertEqual(self.client.get(reverse(name)).status_code, 403, name)

    def test_partner_cannot_access_admin(self):
        for role in (Role.OWNER, Role.BROKER):
            self.client.force_login(make_user(role))
            for name in PARTNER_URLS:
                self.assertEqual(self.client.get(reverse(name)).status_code, 200, name)
            for name in ADMIN_URLS:
                self.assertEqual(self.client.get(reverse(name)).status_code, 403, name)

    def test_admin_can_access_management(self):
        self.client.force_login(make_user(Role.ADMIN))
        for name in ADMIN_URLS:
            self.assertEqual(self.client.get(reverse(name)).status_code, 200, name)

    def test_admin_post_actions_are_protected(self):
        prop = make_property(status="pending")
        self.client.force_login(make_user(Role.OWNER))
        resp = self.client.post(reverse("adminpanel:property_action", args=[prop.pk, "approve"]))
        self.assertEqual(resp.status_code, 403)
        prop.refresh_from_db()
        self.assertEqual(prop.status, "pending")

    def test_suspended_staff_loses_access(self):
        admin = make_user(Role.ADMIN)
        self.client.force_login(admin)
        admin.is_active = False
        admin.save()
        self.assertNotEqual(self.client.get(reverse("adminpanel:dashboard")).status_code, 200)


class SecurityHeaderTests(TestCase):
    def test_private_pages_not_cached(self):
        self.client.force_login(make_user())
        resp = self.client.get(reverse("dashboard:customer_overview"))
        self.assertEqual(resp["Cache-Control"], "private, no-store")
        self.assertIn("noindex", resp["X-Robots-Tag"])
        self.assertEqual(resp["X-Frame-Options"], "DENY")
