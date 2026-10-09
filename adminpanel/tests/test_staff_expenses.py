"""Staff accounts (limited Management access) and the expense tracker."""
from datetime import date, timedelta
from decimal import Decimal

from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import Role, User, VerificationDocument
from accounts.tests.test_otp import last_code
from adminpanel.models import Expense
from core.tests.factories import PASSWORD, make_property, make_user
from payments.models import Payment
from subscriptions.models import SubscriptionPlan


def make_staff(*areas, **kwargs):
    return make_user(Role.STAFF, staff_permissions=list(areas), **kwargs)


class StaffAccessTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_staff_only_reach_the_areas_they_were_given(self):
        staff = make_staff("listings", "expenses")
        self.client.force_login(staff)
        for name in ("adminpanel:approvals", "adminpanel:properties", "adminpanel:reports", "adminpanel:expenses",
                     "adminpanel:staff_home"):
            self.assertEqual(self.client.get(reverse(name)).status_code, 200, name)
        for name in ("adminpanel:payments", "adminpanel:verifications", "adminpanel:users", "adminpanel:enquiries",
                     "adminpanel:banners", "adminpanel:dashboard", "adminpanel:settings", "adminpanel:audit_logs",
                     "adminpanel:staff", "adminpanel:staff_add", "adminpanel:broadcast"):
            self.assertEqual(self.client.get(reverse(name)).status_code, 403, name)
        self.assertEqual(self.client.get(reverse("adminpanel:crud_list", args=["plans"])).status_code, 403)
        self.assertEqual(self.client.get(reverse("adminpanel:crud_list", args=["locations"])).status_code, 403)

    def test_content_staff_manage_banners_and_videos_only(self):
        self.client.force_login(make_staff("content"))
        self.assertEqual(self.client.get(reverse("adminpanel:crud_list", args=["videos"])).status_code, 200)
        self.assertEqual(self.client.get(reverse("adminpanel:crud_add", args=["banners"])).status_code, 200)
        self.assertEqual(self.client.get(reverse("adminpanel:crud_add", args=["plans"])).status_code, 403)

    def test_sidebar_shows_only_allowed_sections(self):
        self.client.force_login(make_staff("payments"))
        page = self.client.get(reverse("adminpanel:staff_home"))
        self.assertContains(page, reverse("adminpanel:payments"))
        self.assertNotContains(page, reverse("adminpanel:approvals"))
        self.assertNotContains(page, reverse("adminpanel:settings"))
        self.assertNotContains(page, reverse("adminpanel:expenses"))

    def test_staff_can_approve_upi_payments_with_payments_area(self):
        owner = make_user(Role.OWNER)
        plan = SubscriptionPlan.objects.get(slug="basic")
        payment = Payment.objects.create(user=owner, plan=plan, amount=plan.price, gateway=Payment.Gateway.MANUAL,
                                         status=Payment.Status.PENDING_VERIFICATION, manual_reference="412345678901")
        self.client.force_login(make_staff("expenses"))
        self.assertEqual(self.client.post(reverse("adminpanel:payment_action", args=[payment.pk, "approve"])).status_code, 403)
        self.client.force_login(make_staff("payments"))
        self.client.post(reverse("adminpanel:payment_action", args=[payment.pk, "approve"]))
        payment.refresh_from_db()
        self.assertEqual(payment.status, Payment.Status.PAID)

    def test_staff_cannot_change_users_or_open_documents_without_access(self):
        owner = make_user(Role.OWNER)
        doc = VerificationDocument.objects.create(
            user=owner, doc_type="pan", file=SimpleUploadedFile("pan.pdf", b"%PDF-1.4 test", content_type="application/pdf"))
        self.client.force_login(make_staff("users"))
        page = self.client.get(reverse("adminpanel:user_detail", args=[owner.pk]))
        self.assertContains(page, "Only admins can change accounts")
        self.assertNotContains(page, reverse("accounts:verification_document", args=[doc.pk]))
        self.assertEqual(self.client.post(reverse("adminpanel:user_action", args=[owner.pk, "suspend"]), {"reason": "x"}).status_code, 403)
        self.assertEqual(self.client.get(reverse("accounts:verification_document", args=[doc.pk])).status_code, 403)
        self.client.force_login(make_staff("verifications"))
        self.assertEqual(self.client.get(reverse("accounts:verification_document", args=[doc.pk])).status_code, 200)

    def test_staff_can_review_pending_listings(self):
        prop = make_property(status="pending")
        self.client.force_login(make_staff("expenses"))
        self.assertEqual(self.client.get(prop.get_absolute_url()).status_code, 404)
        self.client.force_login(make_staff("listings"))
        self.assertEqual(self.client.get(prop.get_absolute_url()).status_code, 200)

    def test_disabled_or_suspended_staff_lose_access(self):
        staff = make_staff("expenses")
        staff.suspended_at = timezone.now()
        staff.save()
        self.client.force_login(staff)
        self.assertEqual(self.client.get(reverse("adminpanel:expenses")).status_code, 403)

    def test_other_accounts_cannot_use_management(self):
        self.client.force_login(make_user(Role.OWNER, staff_permissions=["expenses"]))
        self.assertEqual(self.client.get(reverse("adminpanel:expenses")).status_code, 403)
        self.assertEqual(self.client.get(reverse("adminpanel:staff_home")).status_code, 403)


class StaffSignInTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_staff_sign_in_with_a_code_sent_to_their_own_email(self):
        staff = make_staff("expenses", email="ravi@example.com")
        resp = self.client.post(reverse("accounts:admin_login"), {"username": "ravi@example.com", "password": PASSWORD})
        self.assertRedirects(resp, reverse("accounts:admin_login_verify"), fetch_redirect_response=False)
        resp = self.client.post(reverse("accounts:admin_login_verify"), {"code": last_code(to="ravi@example.com")})
        self.assertRedirects(resp, reverse("adminpanel:staff_home"), fetch_redirect_response=False)
        self.assertEqual(int(self.client.session["_auth_user_id"]), staff.pk)

    def test_staff_login_link_and_regular_users_are_refused(self):
        self.assertRedirects(self.client.get("/staff-login/"), reverse("accounts:admin_login"), fetch_redirect_response=False)
        make_user(Role.CUSTOMER, email="cust@example.com")
        resp = self.client.post(reverse("accounts:admin_login"), {"username": "cust@example.com", "password": PASSWORD})
        self.assertContains(resp, "administrators and staff only")

    def test_staff_cannot_skip_two_step_with_a_sign_in_code(self):
        make_staff("expenses", email="ravi@example.com")
        self.client.post(reverse("accounts:login_code"), {"email": "ravi@example.com"})
        session = self.client.session
        self.assertNotIn("_auth_user_id", session)


class StaffManagementTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client.force_login(make_user(Role.ADMIN))

    def test_admin_creates_edits_and_disables_staff(self):
        resp = self.client.post(reverse("adminpanel:staff_add"), {
            "full_name": "Ravi Kumar", "email": "Ravi@Example.com", "phone": "9845012345",
            "permissions": ["listings", "payments"], "password": "Bangarpet#Staff2026",
        })
        self.assertRedirects(resp, reverse("adminpanel:staff"), fetch_redirect_response=False)
        staff = User.objects.get(email="ravi@example.com")
        self.assertEqual((staff.role, staff.staff_permissions), (Role.STAFF, ["listings", "payments"]))
        self.assertTrue(staff.check_password("Bangarpet#Staff2026"))
        self.assertContains(self.client.get(reverse("adminpanel:staff")), "Ravi Kumar")
        # Edit: change permissions and disable, keeping the password.
        self.client.post(reverse("adminpanel:staff_edit", args=[staff.pk]), {
            "full_name": "Ravi Kumar", "email": "ravi@example.com", "phone": "", "permissions": ["expenses"],
        })
        staff.refresh_from_db()
        self.assertEqual(staff.staff_permissions, ["expenses"])
        self.assertFalse(staff.is_active)
        self.assertTrue(staff.check_password("Bangarpet#Staff2026"))

    def test_weak_password_and_duplicate_email_are_refused(self):
        make_user(Role.CUSTOMER, email="taken@example.com")
        resp = self.client.post(reverse("adminpanel:staff_add"), {
            "full_name": "X", "email": "taken@example.com", "permissions": [], "password": "123",
        })
        self.assertContains(resp, "Another account already uses this email")
        self.assertFalse(User.objects.filter(role=Role.STAFF).exists())

    def test_staff_role_cannot_be_changed_from_the_users_page(self):
        staff = make_staff("expenses")
        self.client.post(reverse("adminpanel:user_action", args=[staff.pk, "role"]), {"role": "owner"})
        staff.refresh_from_db()
        self.assertEqual(staff.role, Role.STAFF)


class ExpenseTests(TestCase):
    def setUp(self):
        cache.clear()
        self.staff = make_staff("expenses", full_name="Ravi Kumar")
        self.client.force_login(self.staff)

    def add(self, **data):
        payload = {"spent_on": timezone.localdate().isoformat(), "category": "marketing", "description": "Facebook ads",
                   "amount": "1500", "payment_method": "upi", "paid_to": "Meta", "reference": "", "notes": ""}
        payload.update(data)
        return self.client.post(reverse("adminpanel:expense_add"), payload)

    def test_staff_record_an_expense_with_a_private_receipt(self):
        receipt = SimpleUploadedFile("bill.pdf", b"%PDF-1.4 bill", content_type="application/pdf")
        resp = self.add(receipt=receipt)
        self.assertRedirects(resp, reverse("adminpanel:expenses"), fetch_redirect_response=False)
        expense = Expense.objects.get()
        self.assertEqual((expense.amount, expense.created_by, expense.receipt_name), (Decimal("1500"), self.staff, "bill.pdf"))
        self.assertEqual(self.client.get(reverse("adminpanel:expense_receipt", args=[expense.pk])).status_code, 200)
        self.client.logout()
        self.assertEqual(self.client.get(reverse("adminpanel:expense_receipt", args=[expense.pk])).status_code, 302)
        self.client.force_login(make_staff("payments"))
        self.assertEqual(self.client.get(reverse("adminpanel:expense_receipt", args=[expense.pk])).status_code, 403)

    def test_bad_amounts_are_refused(self):
        self.assertEqual(self.add(amount="0").status_code, 200)
        self.assertEqual(self.add(amount="-5").status_code, 200)
        self.assertFalse(Expense.objects.exists())

    def test_totals_profit_filters_and_csv(self):
        today = timezone.localdate()
        last_month = today.replace(day=1) - timedelta(days=1)
        Expense.objects.create(spent_on=today, category="marketing", description="Ads", amount=1500)
        Expense.objects.create(spent_on=today, category="office", description="Rent", amount=5000)
        Expense.objects.create(spent_on=last_month, category="travel", description="Fuel", amount=700)
        owner = make_user(Role.OWNER)
        plan = SubscriptionPlan.objects.get(slug="basic")
        Payment.objects.create(user=owner, plan=plan, amount=Decimal("8000"), status=Payment.Status.PAID, paid_at=timezone.now())
        page = self.client.get(reverse("adminpanel:expenses"))
        self.assertEqual(page.context["summary"]["this_month"], Decimal("6500"))
        self.assertEqual(page.context["summary"]["last_month"], Decimal("700"))
        self.assertEqual(page.context["summary"]["income_month"], Decimal("8000"))
        self.assertContains(page, "Profit this month")
        filtered = self.client.get(reverse("adminpanel:expenses"), {"month": f"{last_month:%Y-%m}"})
        self.assertEqual(filtered.context["filtered_total"], Decimal("700"))
        self.assertEqual(self.client.get(reverse("adminpanel:expenses"), {"category": "office"}).context["filtered_total"], Decimal("5000"))
        csv = self.client.get(reverse("adminpanel:expenses"), {"export": "csv"})
        self.assertEqual(csv["Content-Type"], "text/csv; charset=utf-8")
        body = csv.content.decode("utf-8-sig")
        self.assertIn("Date,Category,What for,Amount (INR)", body)
        self.assertIn("Office rent & maintenance,Rent,5000.00", body)

    def test_only_admins_delete_expenses(self):
        expense = Expense.objects.create(spent_on=date.today(), description="Tea", amount=100)
        self.assertNotContains(self.client.get(reverse("adminpanel:expenses")), reverse("adminpanel:expense_delete", args=[expense.pk]))
        self.assertEqual(self.client.post(reverse("adminpanel:expense_delete", args=[expense.pk])).status_code, 403)
        self.client.force_login(make_user(Role.ADMIN))
        self.client.post(reverse("adminpanel:expense_delete", args=[expense.pk]))
        self.assertFalse(Expense.objects.exists())

    def test_staff_without_expenses_area_are_refused(self):
        self.client.force_login(make_staff("listings"))
        self.assertEqual(self.client.get(reverse("adminpanel:expenses")).status_code, 403)
        self.assertEqual(self.client.get(reverse("adminpanel:expense_add")).status_code, 403)
