import re
from datetime import timedelta

from django.core import mail
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import EmailOTP, Role, User
from accounts.otp import MAX_ATTEMPTS, OTPError, Purpose, check_code, mask_email, otp_enabled, send_code
from core.tests.factories import PASSWORD, make_user


def last_code(to=None):
    message = mail.outbox[-1]
    if to:
        assert message.to == [to], message.to
    return re.search(r"\b(\d{6})\b", message.subject).group(1)


def age_codes(user, seconds=120):
    EmailOTP.objects.filter(user=user).update(created_at=timezone.now() - timedelta(seconds=seconds))


class OTPServiceTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = make_user(email="asha@example.com")

    def test_code_is_emailed_and_only_a_hash_is_stored(self):
        masked = send_code(self.user, Purpose.LOGIN)
        self.assertEqual(masked, "as***@example.com")
        code = last_code(to="asha@example.com")
        self.assertIn(code, mail.outbox[-1].body)
        self.assertIn("ಕೋಡ್", mail.outbox[-1].body)  # Kannada line for customers
        self.assertIn(code, mail.outbox[-1].alternatives[0][0])
        otp = EmailOTP.objects.get()
        self.assertNotIn(code, otp.code_hash)
        self.assertEqual(len(otp.code_hash), 64)

    def test_code_works_once(self):
        send_code(self.user, Purpose.LOGIN)
        code = last_code()
        self.assertTrue(check_code(self.user, Purpose.LOGIN, code))
        with self.assertRaises(OTPError):
            check_code(self.user, Purpose.LOGIN, code)

    def test_code_is_tied_to_its_purpose(self):
        send_code(self.user, Purpose.LOGIN)
        with self.assertRaises(OTPError):
            check_code(self.user, Purpose.PASSWORD_RESET, last_code())

    def test_wrong_tries_are_limited(self):
        send_code(self.user, Purpose.LOGIN)
        code = last_code()
        wrong = "000000" if code != "000000" else "111111"
        for _ in range(MAX_ATTEMPTS):
            with self.assertRaises(OTPError):
                check_code(self.user, Purpose.LOGIN, wrong)
        with self.assertRaisesMessage(OTPError, "Too many wrong tries"):
            check_code(self.user, Purpose.LOGIN, code)

    def test_expired_code_is_rejected(self):
        send_code(self.user, Purpose.LOGIN)
        EmailOTP.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        with self.assertRaisesMessage(OTPError, "expired"):
            check_code(self.user, Purpose.LOGIN, last_code())

    def test_new_code_replaces_the_old_one_after_a_short_wait(self):
        send_code(self.user, Purpose.LOGIN)
        first = last_code()
        with self.assertRaisesMessage(OTPError, "Please wait"):
            send_code(self.user, Purpose.LOGIN)
        age_codes(self.user)
        send_code(self.user, Purpose.LOGIN)
        second = last_code()
        if first != second:
            with self.assertRaises(OTPError):
                check_code(self.user, Purpose.LOGIN, first)
        self.assertTrue(check_code(self.user, Purpose.LOGIN, second))

    def test_codes_per_hour_are_limited(self):
        for _ in range(5):
            send_code(self.user, Purpose.LOGIN)
            age_codes(self.user)
        with self.assertRaisesMessage(OTPError, "Too many codes"):
            send_code(self.user, Purpose.LOGIN)

    def test_mask_email(self):
        self.assertEqual(mask_email("techzodo@gmail.com"), "te******@gmail.com")
        self.assertEqual(mask_email("ab@x.in"), "a***@x.in")

    @override_settings(EMAIL_DELIVERY_CONFIGURED=False, DEBUG=False)
    def test_codes_are_off_until_email_is_set_up(self):
        self.assertFalse(otp_enabled())

    @override_settings(EMAIL_OTP_ENABLED=False)
    def test_codes_can_be_switched_off(self):
        self.assertFalse(otp_enabled())


class SignupVerificationTests(TestCase):
    def setUp(self):
        cache.clear()

    def register(self):
        return self.client.post(reverse("accounts:register"), {
            "account_type": "customer", "full_name": "Asha Kumar", "email": "asha@example.com", "phone": "98450 12345",
            "password1": "Bangarpet#Home2024", "password2": "Bangarpet#Home2024", "accept_terms": "on",
        })

    def test_signup_then_verify_with_code(self):
        resp = self.register()
        self.assertRedirects(resp, reverse("accounts:verify_email_code"), fetch_redirect_response=False)
        page = self.client.get(resp["Location"])
        self.assertContains(page, "Verify your email")
        self.assertContains(page, 'autocomplete="one-time-code"')
        resp = self.client.post(reverse("accounts:verify_email_code"), {"code": last_code(to="asha@example.com")})
        self.assertRedirects(resp, reverse("accounts:post_login"), fetch_redirect_response=False)
        self.assertTrue(User.objects.get(email="asha@example.com").email_verified)

    def test_wrong_code_and_resend(self):
        self.register()
        user = User.objects.get(email="asha@example.com")
        code = last_code()
        wrong = "000000" if code != "000000" else "111111"
        resp = self.client.post(reverse("accounts:verify_email_code"), {"code": wrong})
        self.assertContains(resp, "not correct")
        age_codes(user)
        self.client.post(reverse("accounts:verify_email_code"), {"action": "resend"})
        self.assertEqual(len(mail.outbox), 2)
        self.client.post(reverse("accounts:verify_email_code"), {"code": last_code()})
        user.refresh_from_db()
        self.assertTrue(user.email_verified)

    def test_dashboard_banner_links_to_code_page(self):
        user = make_user(email_verified=False)
        self.client.force_login(user)
        resp = self.client.get(reverse("dashboard:customer_overview"))
        self.assertContains(resp, reverse("accounts:verify_email_code"))

    @override_settings(EMAIL_OTP_ENABLED=False)
    def test_signup_skips_code_step_when_codes_are_off(self):
        resp = self.register()
        self.assertRedirects(resp, reverse("accounts:post_login"), fetch_redirect_response=False)


class LoginWithCodeTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = make_user(email="ravi@example.com", email_verified=False)

    def test_sign_in_with_emailed_code(self):
        self.assertContains(self.client.get(reverse("accounts:login")), reverse("accounts:login_code"))
        resp = self.client.post(reverse("accounts:login_code"), {"email": "RAVI@example.com"})
        self.assertRedirects(resp, reverse("accounts:login_code_verify"), fetch_redirect_response=False)
        self.assertNotIn("_auth_user_id", self.client.session)
        resp = self.client.post(reverse("accounts:login_code_verify"), {"code": last_code(to="ravi@example.com")})
        self.assertRedirects(resp, reverse("accounts:post_login"), fetch_redirect_response=False)
        self.assertEqual(int(self.client.session["_auth_user_id"]), self.user.pk)
        self.user.refresh_from_db()
        self.assertTrue(self.user.email_verified)

    def test_unknown_email_gets_the_same_answer_and_no_email(self):
        resp = self.client.post(reverse("accounts:login_code"), {"email": "nobody@example.com"}, follow=True)
        self.assertContains(resp, "If an account exists for nobody@example.com")
        self.assertEqual(len(mail.outbox), 0)
        resp = self.client.post(reverse("accounts:login_code_verify"), {"code": "123456"})
        self.assertContains(resp, "not correct")
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_admins_cannot_skip_their_password(self):
        make_user(Role.ADMIN, email="boss@example.com")
        self.client.post(reverse("accounts:login_code"), {"email": "boss@example.com"})
        self.assertEqual(len(mail.outbox), 0)

    def test_verify_page_needs_a_request_first(self):
        resp = self.client.get(reverse("accounts:login_code_verify"))
        self.assertRedirects(resp, reverse("accounts:login_code"))

    def test_code_guesses_are_throttled_per_network(self):
        self.client.post(reverse("accounts:login_code"), {"email": "ravi@example.com"})
        for _ in range(20):
            self.client.post(reverse("accounts:login_code_verify"), {"code": "000000"})
        resp = self.client.post(reverse("accounts:login_code_verify"), {"code": last_code()})
        self.assertContains(resp, "Too many tries from your network")
        self.assertNotIn("_auth_user_id", self.client.session)

    @override_settings(EMAIL_OTP_ENABLED=False)
    def test_hidden_when_codes_are_off(self):
        self.assertNotContains(self.client.get(reverse("accounts:login")), reverse("accounts:login_code"))
        self.assertRedirects(self.client.get(reverse("accounts:login_code")), reverse("accounts:login"))


class PasswordResetByCodeTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = make_user(email="reset@example.com")

    def test_reset_password_with_code(self):
        resp = self.client.post(reverse("accounts:password_reset"), {"email": "reset@example.com"})
        self.assertRedirects(resp, reverse("accounts:password_reset_code"), fetch_redirect_response=False)
        page = self.client.get(resp["Location"])
        self.assertContains(page, "Choose a new password")
        resp = self.client.post(reverse("accounts:password_reset_code"), {
            "code": last_code(to="reset@example.com"), "new_password1": "N3w-Secure#Pass", "new_password2": "N3w-Secure#Pass",
        })
        self.assertRedirects(resp, reverse("accounts:password_reset_complete"))
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("N3w-Secure#Pass"))

    def test_wrong_code_keeps_the_old_password(self):
        self.client.post(reverse("accounts:password_reset"), {"email": "reset@example.com"})
        code = last_code()
        wrong = "000000" if code != "000000" else "111111"
        resp = self.client.post(reverse("accounts:password_reset_code"), {
            "code": wrong, "new_password1": "N3w-Secure#Pass", "new_password2": "N3w-Secure#Pass",
        })
        self.assertContains(resp, "not correct")
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(PASSWORD))

    def test_unknown_email_gives_same_response(self):
        resp = self.client.post(reverse("accounts:password_reset"), {"email": "nobody@example.com"})
        self.assertRedirects(resp, reverse("accounts:password_reset_code"), fetch_redirect_response=False)
        self.assertEqual(len(mail.outbox), 0)

    def test_weak_new_password_rejected(self):
        self.client.post(reverse("accounts:password_reset"), {"email": "reset@example.com"})
        resp = self.client.post(reverse("accounts:password_reset_code"), {
            "code": last_code(), "new_password1": "12345678", "new_password2": "12345678",
        })
        self.assertEqual(resp.status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(PASSWORD))


class AdminTwoStepTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_admin_needs_the_emailed_code(self):
        admin = make_user(Role.ADMIN, email="boss@example.com")
        resp = self.client.post(reverse("accounts:admin_login"), {"username": "boss@example.com", "password": PASSWORD})
        self.assertRedirects(resp, reverse("accounts:admin_login_verify"), fetch_redirect_response=False)
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertEqual(self.client.get("/management/").status_code, 302)
        resp = self.client.post(reverse("accounts:admin_login_verify"), {"code": last_code(to="boss@example.com")})
        self.assertRedirects(resp, "/management/", fetch_redirect_response=False)
        self.assertEqual(int(self.client.session["_auth_user_id"]), admin.pk)

    def test_regular_sign_in_page_also_asks_admins_for_the_code(self):
        make_user(Role.ADMIN, email="boss@example.com")
        resp = self.client.post(reverse("accounts:login"), {"username": "boss@example.com", "password": PASSWORD})
        self.assertRedirects(resp, reverse("accounts:admin_login_verify"), fetch_redirect_response=False)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_wrong_code_does_not_sign_in(self):
        make_user(Role.ADMIN, email="boss@example.com")
        self.client.post(reverse("accounts:admin_login"), {"username": "boss@example.com", "password": PASSWORD})
        code = last_code()
        wrong = "000000" if code != "000000" else "111111"
        resp = self.client.post(reverse("accounts:admin_login_verify"), {"code": wrong})
        self.assertContains(resp, "not correct")
        self.assertNotIn("_auth_user_id", self.client.session)

    @override_settings(ADMIN_OTP_EMAIL="owner.inbox@example.com")
    def test_username_login_sends_code_to_admin_otp_email(self):
        User.objects.create_superuser("bph@admin", "Test#Admin2024", full_name="Site Admin")
        resp = self.client.post(reverse("accounts:admin_login"), {"username": "bph@admin", "password": "Test#Admin2024"})
        self.assertRedirects(resp, reverse("accounts:admin_login_verify"), fetch_redirect_response=False)
        page = self.client.get(resp["Location"])
        self.assertContains(page, "ow*********@example.com")
        self.client.post(reverse("accounts:admin_login_verify"), {"code": last_code(to="owner.inbox@example.com")})
        self.assertEqual(self.client.get("/management/").status_code, 200)

    def test_username_login_without_an_inbox_is_not_locked_out(self):
        User.objects.create_superuser("bph@admin", "Test#Admin2024", full_name="Site Admin")
        resp = self.client.post(reverse("accounts:admin_login"), {"username": "bph@admin", "password": "Test#Admin2024"})
        self.assertRedirects(resp, "/management/", fetch_redirect_response=False)
        self.assertEqual(len(mail.outbox), 0)

    @override_settings(EMAIL_OTP_ENABLED=False)
    def test_no_second_step_when_codes_are_off(self):
        make_user(Role.ADMIN, email="boss@example.com")
        resp = self.client.post(reverse("accounts:admin_login"), {"username": "boss@example.com", "password": PASSWORD})
        self.assertRedirects(resp, "/management/", fetch_redirect_response=False)

    def test_verify_page_without_password_step_goes_back(self):
        self.assertRedirects(self.client.get(reverse("accounts:admin_login_verify")), reverse("accounts:admin_login"))
