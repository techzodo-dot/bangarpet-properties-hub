from django.core import mail
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import BrokerProfile, OwnerProfile, Role, User, UserProfile, VerificationDocument
from accounts.services import make_email_token
from core.tests.factories import PASSWORD, make_user


class RegistrationTests(TestCase):
    def setUp(self):
        cache.clear()

    def _data(self, **kw):
        data = {"account_type": "customer", "full_name": "Asha Kumar", "email": "Asha@Example.com", "phone": "98450 12345",
                "password1": "Bangarpet#Home2024", "password2": "Bangarpet#Home2024", "accept_terms": "on"}
        data.update(kw)
        return data

    def test_customer_registration_creates_profile_and_logs_in(self):
        resp = self.client.post(reverse("accounts:register"), self._data())
        self.assertRedirects(resp, reverse("accounts:verify_email_code"), fetch_redirect_response=False)
        user = User.objects.get(email="asha@example.com")
        self.assertEqual(user.role, Role.CUSTOMER)
        self.assertEqual(user.phone, "+919845012345")
        self.assertTrue(UserProfile.objects.filter(user=user).exists())
        self.assertNotEqual(user.password, "Bangarpet#Home2024")  # hashed
        self.assertTrue(user.check_password("Bangarpet#Home2024"))
        self.assertEqual(len(mail.outbox), 1)  # verification code
        self.assertIn("is your Bangarpet Property Hub code", mail.outbox[0].subject)
        self.assertEqual(int(self.client.session["_auth_user_id"]), user.pk)

    def test_broker_requires_agency_name(self):
        resp = self.client.post(reverse("accounts:register"), self._data(account_type="broker"))
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(User.objects.exists())
        self.client.post(reverse("accounts:register"), self._data(account_type="broker", agency_name="Kolar Homes"))
        user = User.objects.get()
        self.assertEqual(BrokerProfile.objects.get(user=user).agency_name, "Kolar Homes")

    def test_owner_registration_creates_owner_profile(self):
        self.client.post(reverse("accounts:register"), self._data(account_type="owner"))
        self.assertTrue(OwnerProfile.objects.filter(user__email="asha@example.com").exists())

    def test_cannot_register_as_admin(self):
        resp = self.client.post(reverse("accounts:register"), self._data(account_type="admin"))
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(User.objects.exists())

    def test_duplicate_email_and_weak_password_rejected(self):
        make_user(email="asha@example.com")
        resp = self.client.post(reverse("accounts:register"), self._data())
        self.assertContains(resp, "already exists")
        resp = self.client.post(reverse("accounts:register"), self._data(email="new@example.com", password1="12345678", password2="12345678"))
        self.assertEqual(User.objects.filter(email="new@example.com").count(), 0)

    def test_invalid_phone_rejected(self):
        resp = self.client.post(reverse("accounts:register"), self._data(phone="12345"))
        self.assertContains(resp, "valid 10-digit")


class LoginTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = make_user(email="ravi@example.com")

    def test_login_with_case_insensitive_email(self):
        resp = self.client.post(reverse("accounts:login"), {"username": "RAVI@example.com", "password": PASSWORD})
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(int(self.client.session["_auth_user_id"]), self.user.pk)

    def test_role_based_redirect_after_login(self):
        owner = make_user(Role.OWNER)
        self.client.force_login(owner)
        self.assertRedirects(self.client.get(reverse("accounts:post_login")), reverse("dashboard:partner_overview"))
        admin = make_user(Role.ADMIN)
        self.client.force_login(admin)
        self.assertRedirects(self.client.get(reverse("accounts:post_login")), reverse("adminpanel:dashboard"))

    def test_login_throttling(self):
        for _ in range(5):
            self.client.post(reverse("accounts:login"), {"username": "ravi@example.com", "password": "wrong"})
        resp = self.client.post(reverse("accounts:login"), {"username": "ravi@example.com", "password": PASSWORD})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Too many failed sign-in attempts")
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_suspended_user_cannot_login(self):
        self.user.is_active = False
        self.user.save()
        resp = self.client.post(reverse("accounts:login"), {"username": "ravi@example.com", "password": PASSWORD})
        self.assertContains(resp, "not active")

    def test_logout_requires_post(self):
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(reverse("accounts:logout")).status_code, 405)
        self.client.post(reverse("accounts:logout"))
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_open_redirect_blocked(self):
        resp = self.client.post(reverse("accounts:login") + "?next=https://evil.example.com/",
                                {"username": "ravi@example.com", "password": PASSWORD, "next": "https://evil.example.com/"})
        self.assertNotIn("evil", resp["Location"])


@override_settings(EMAIL_OTP_ENABLED=False)
class PasswordResetLinkTests(TestCase):
    """The link-based reset, used while email codes are switched off (and for links already sent)."""

    def setUp(self):
        cache.clear()

    def test_password_reset_flow(self):
        user = make_user(email="reset@example.com")
        resp = self.client.post(reverse("accounts:password_reset"), {"email": "reset@example.com"})
        self.assertRedirects(resp, reverse("accounts:password_reset_done"))
        self.assertEqual(len(mail.outbox), 1)
        link = [line for line in mail.outbox[0].body.splitlines() if "/accounts/reset/" in line][0].strip()
        path = link.split("testserver", 1)[-1]
        resp = self.client.get(path, follow=True)
        self.assertContains(resp, "Choose a new password")
        resp = self.client.post(resp.redirect_chain[-1][0], {"new_password1": "N3w-Secure#Pass", "new_password2": "N3w-Secure#Pass"})
        self.assertRedirects(resp, reverse("accounts:password_reset_complete"))
        user.refresh_from_db()
        self.assertTrue(user.check_password("N3w-Secure#Pass"))

    def test_unknown_email_gives_same_response(self):
        resp = self.client.post(reverse("accounts:password_reset"), {"email": "nobody@example.com"})
        self.assertRedirects(resp, reverse("accounts:password_reset_done"))
        self.assertEqual(len(mail.outbox), 0)


class EmailVerificationTests(TestCase):
    def test_verify_email_token(self):
        user = make_user(email_verified=False)
        self.client.get(reverse("accounts:verify_email", args=[make_email_token(user)]))
        user.refresh_from_db()
        self.assertTrue(user.email_verified)

    def test_tampered_token_rejected(self):
        user = make_user(email_verified=False)
        self.client.get(reverse("accounts:verify_email", args=[make_email_token(user) + "x"]))
        user.refresh_from_db()
        self.assertFalse(user.email_verified)


class VerificationDocumentAccessTests(TestCase):
    def setUp(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        self.owner = make_user(Role.OWNER)
        self.doc = VerificationDocument.objects.create(
            user=self.owner, doc_type="pan", file=SimpleUploadedFile("pan.pdf", b"%PDF-1.4 test", content_type="application/pdf"))

    def test_document_is_not_under_public_media(self):
        from django.conf import settings

        self.assertFalse(str(self.doc.file.path).startswith(str(settings.MEDIA_ROOT)))

    def test_only_owner_and_admin_can_view(self):
        url = reverse("accounts:verification_document", args=[self.doc.pk])
        self.assertEqual(self.client.get(url).status_code, 302)  # anonymous -> login
        self.client.force_login(make_user(Role.OWNER))
        self.assertEqual(self.client.get(url).status_code, 403)
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(url).status_code, 200)
        self.client.force_login(make_user(Role.ADMIN))
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Cache-Control"], "private, no-store")
        from core.models import AuditLog

        self.assertTrue(AuditLog.objects.filter(action="verification.document_viewed").exists())

    def test_upload_validates_document_type(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        self.client.force_login(self.owner)
        fake = SimpleUploadedFile("id.pdf", b"not really a pdf", content_type="application/pdf")
        self.client.post(reverse("dashboard:partner_profile"), {"section": "verification", "doc-doc_type": "pan", "doc-file": fake})
        self.assertEqual(self.owner.verification_documents.count(), 1)  # only the setUp document
        good = SimpleUploadedFile("id.pdf", b"%PDF-1.4 real", content_type="application/pdf")
        self.client.post(reverse("dashboard:partner_profile"), {"section": "verification", "doc-doc_type": "pan", "doc-file": good})
        self.assertEqual(self.owner.verification_documents.count(), 2)
        self.owner.owner_profile.refresh_from_db()
        self.assertEqual(self.owner.owner_profile.verification_status, "pending")
