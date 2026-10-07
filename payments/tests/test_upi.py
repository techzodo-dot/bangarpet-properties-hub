"""Direct UPI payments: pay to the site's UPI ID, submit the reference, admin approves."""
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import Role
from core.models import PlatformSetting
from core.tests.factories import make_property, make_user
from payments import upi
from payments.models import Payment
from properties import contacts
from subscriptions.models import SubscriptionPlan
from subscriptions.services import contact_pass_plan, current_plan


def set_site(**fields):
    site = PlatformSetting.load()
    for name, value in fields.items():
        setattr(site, name, value)
    site.save()
    return site


class UpiLinkTests(TestCase):
    def test_link_has_payee_amount_and_note(self):
        site = set_site(upi_id="bph@okhdfcbank", upi_payee_name="Bangarpet Property Hub")
        link = upi.pay_link(site, 99, "BPH contact-pass U7")
        self.assertEqual(link, "upi://pay?pa=bph@okhdfcbank&pn=Bangarpet%20Property%20Hub&am=99.00&cu=INR"
                               "&tn=BPH%20contact-pass%20U7")
        self.assertIn("<svg", upi.qr_svg(link))

    def test_no_upi_id_means_no_link(self):
        self.assertEqual(upi.pay_link(PlatformSetting.load(), 99, "x"), "")

    def test_upi_id_is_validated(self):
        site = PlatformSetting.load()
        site.upi_id = "not a upi id"
        with self.assertRaises(ValidationError):
            site.full_clean()


class ManualUpiTests(TestCase):
    def setUp(self):
        cache.clear()
        self.admin = make_user(Role.ADMIN)

    def approve(self, payment):
        self.client.force_login(self.admin)
        self.client.post(reverse("adminpanel:payment_action", args=[payment.pk, "approve"]))
        payment.refresh_from_db()
        self.assertEqual(payment.status, Payment.Status.PAID)

    def test_upi_is_on_by_default(self):
        self.assertTrue(PlatformSetting.load().allow_manual_payments)

    def test_page_shows_qr_upi_id_and_amount(self):
        set_site(upi_id="bph@okhdfcbank", upi_payee_name="BPH")
        owner = make_user(Role.OWNER)
        self.client.force_login(owner)
        page = self.client.get(reverse("payments:manual", args=["basic"]))
        self.assertContains(page, "bph@okhdfcbank")
        self.assertContains(page, 'aria-label="UPI QR code"')
        self.assertContains(page, f"upi://pay?pa=bph@okhdfcbank&amp;pn=BPH&amp;am=299.00&amp;cu=INR&amp;tn=BPH%20basic%20U{owner.pk}")

    def test_without_upi_id_page_points_to_whatsapp(self):
        set_site(upi_id="", whatsapp_number="+919445330547")
        self.client.force_login(make_user(Role.OWNER))
        page = self.client.get(reverse("payments:manual", args=["basic"]))
        self.assertContains(page, "https://wa.me/919445330547")
        self.assertNotContains(page, "upi://pay")

    def test_owner_pays_by_upi_and_admin_approval_activates_plan(self):
        owner = make_user(Role.OWNER)
        self.client.force_login(owner)
        plans = self.client.get(reverse("dashboard:partner_subscription"))
        self.assertContains(plans, reverse("payments:manual", args=["basic"]))
        resp = self.client.post(reverse("payments:manual", args=["basic"]), {"manual_reference": "412345678901"})
        self.assertRedirects(resp, reverse("dashboard:partner_payments"), fetch_redirect_response=False)
        # Submitting the same reference twice does not create a second payment.
        self.client.post(reverse("payments:manual", args=["basic"]), {"manual_reference": "412345678901"})
        payment = Payment.objects.get(user=owner)
        self.assertEqual((payment.status, payment.gateway, payment.amount), ("pending_verification", "manual", 299))
        self.approve(payment)
        self.assertEqual(current_plan(owner), SubscriptionPlan.objects.get(slug="basic"))

    def test_customer_buys_contact_pass_by_upi(self):
        customer = make_user(Role.CUSTOMER)
        self.client.force_login(customer)
        self.assertContains(self.client.get(reverse("payments:contact_pass")),
                            reverse("payments:manual", args=["contact-pass"]))
        page = self.client.get(reverse("payments:manual", args=["contact-pass"]))
        self.assertContains(page, "Contact Pass")
        resp = self.client.post(reverse("payments:manual", args=["contact-pass"]), {"manual_reference": "412345678902"})
        self.assertRedirects(resp, reverse("payments:contact_pass"), fetch_redirect_response=False)
        payment = Payment.objects.get(user=customer)
        self.assertEqual(payment.amount, contact_pass_plan().effective_price)
        self.assertFalse(contacts.active_pass(customer))
        self.approve(payment)
        self.assertTrue(contacts.active_pass(customer))
        prop = make_property(contact_visibility="public")
        self.client.force_login(customer)
        self.assertContains(self.client.get(prop.get_absolute_url()), "tel:" + prop.contact_phone)

    def test_roles_cannot_buy_the_wrong_plan(self):
        self.client.force_login(make_user(Role.CUSTOMER))
        self.assertEqual(self.client.get(reverse("payments:manual", args=["basic"])).status_code, 403)
        self.client.force_login(make_user(Role.OWNER))
        self.assertEqual(self.client.get(reverse("payments:manual", args=["contact-pass"])).status_code, 403)

    def test_switched_off_upi_is_refused(self):
        set_site(allow_manual_payments=False)
        self.client.force_login(make_user(Role.CUSTOMER))
        self.assertEqual(self.client.get(reverse("payments:manual", args=["contact-pass"])).status_code, 403)

    @override_settings(PAYU_MERCHANT_KEY="", PAYU_MERCHANT_SALT="")
    def test_without_payu_upi_is_the_main_option(self):
        self.client.force_login(make_user(Role.CUSTOMER))
        resp = self.client.post(reverse("payments:contact_pass_subscribe"))
        self.assertRedirects(resp, reverse("payments:manual", args=["contact-pass"]), fetch_redirect_response=False)
        self.assertFalse(Payment.objects.exists())

    def test_pay_page_offers_upi_when_payu_has_trouble(self):
        customer = make_user(Role.CUSTOMER)
        self.client.force_login(customer)
        self.client.post(reverse("payments:contact_pass_subscribe"))
        payment = Payment.objects.get(user=customer)
        page = self.client.get(reverse("payments:pay", args=[payment.uid]))
        self.assertContains(page, reverse("payments:manual", args=["contact-pass"]))
