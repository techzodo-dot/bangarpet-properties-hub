from io import StringIO
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from accounts.models import Role


class EnsureAdminTests(TestCase):
    def setUp(self):
        cache.clear()

    def run_cmd(self, *args, **env):
        out = StringIO()
        with mock.patch.dict("os.environ", env, clear=False):
            call_command("ensure_admin", *args, stdout=out, stderr=StringIO())
        return out.getvalue()

    def test_creates_super_admin_from_environment_and_can_sign_in(self):
        self.run_cmd(ADMIN_USERNAME="bph@admin", ADMIN_PASSWORD="Test#Admin2024")
        user = get_user_model().objects.get(email="bph@admin")
        self.assertTrue(user.is_superuser and user.is_staff and user.is_platform_admin)
        self.assertEqual(user.role, Role.ADMIN)

        resp = self.client.post("/login/", {"username": "bph@admin", "password": "Test#Admin2024"})
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(self.client.get("/management/").status_code, 200)

    def test_rerun_updates_instead_of_duplicating(self):
        self.run_cmd(ADMIN_USERNAME="bph@admin", ADMIN_PASSWORD="Test#Admin2024")
        self.run_cmd(ADMIN_USERNAME="BPH@admin", ADMIN_PASSWORD="Another#Pass99")
        users = get_user_model().objects.filter(email__iexact="bph@admin")
        self.assertEqual(users.count(), 1)
        self.assertTrue(users.get().check_password("Another#Pass99"))

    def test_keep_password_leaves_existing_password(self):
        self.run_cmd(ADMIN_USERNAME="bph@admin", ADMIN_PASSWORD="Test#Admin2024")
        self.run_cmd("--keep-password", ADMIN_USERNAME="bph@admin", ADMIN_PASSWORD="Changed#Pass99")
        self.assertTrue(get_user_model().objects.get(email="bph@admin").check_password("Test#Admin2024"))

    def test_missing_settings(self):
        with mock.patch.dict("os.environ", {"ADMIN_USERNAME": "", "ADMIN_PASSWORD": ""}):
            with self.assertRaises(CommandError):
                call_command("ensure_admin", stdout=StringIO())
            out = StringIO()
            call_command("ensure_admin", "--skip-if-missing", stdout=out)
        self.assertIn("skipped", out.getvalue())
        self.assertFalse(get_user_model().objects.exists())

    def test_wrong_password_is_rejected(self):
        self.run_cmd(ADMIN_USERNAME="bph@admin", ADMIN_PASSWORD="Test#Admin2024")
        resp = self.client.post("/login/", {"username": "bph@admin", "password": "wrong"})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Incorrect email/username or password.")

    def test_admin_login_page_signs_admins_into_management(self):
        self.run_cmd(ADMIN_USERNAME="bph@admin", ADMIN_PASSWORD="Test#Admin2024")
        page = self.client.get("/admin-login/")
        self.assertContains(page, "Admin &amp; staff sign in")
        resp = self.client.post("/admin-login/", {"username": "bph@admin", "password": "Test#Admin2024"})
        self.assertRedirects(resp, "/management/")

    def test_admin_login_page_rejects_other_accounts(self):
        get_user_model().objects.create_user("owner@example.com", "Owner#Pass2024", role=Role.OWNER, full_name="Owner")
        resp = self.client.post("/admin-login/", {"username": "owner@example.com", "password": "Owner#Pass2024"})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "administrators and staff only")
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_header_links_to_admin_login(self):
        self.assertContains(self.client.get("/"), 'href="/admin-login/"')
