from datetime import timedelta

from django.conf import settings
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import Role
from core.models import PlatformSetting
from core.tests.factories import make_property, make_user
from payments.models import Payment
from payments.tests.payu_helpers import payu_reply
from properties.models import ContactUnlock
from subscriptions.models import Subscription
from subscriptions.services import activate_subscription, contact_pass_plan


def unlock_url(prop):
    return reverse("properties:unlock_contact", args=[prop.slug])


class FreeContactLimitTests(TestCase):
    def setUp(self):
        cache.clear()
        self.customer = make_user(Role.CUSTOMER)
        self.props = [make_property(contact_visibility="public") for _ in range(6)]

    def test_visitors_must_sign_in_to_see_contacts(self):
        resp = self.client.get(self.props[0].get_absolute_url())
        self.assertNotContains(resp, "tel:" + self.props[0].contact_phone)
        self.assertContains(resp, "Sign in to view phone number")

    def test_five_free_contacts_then_contact_pass(self):
        self.client.force_login(self.customer)
        first = self.props[0]
        page = self.client.get(first.get_absolute_url())
        self.assertNotContains(page, "tel:" + first.contact_phone)
        self.assertContains(page, "5 of 5 free owner contacts left this month")
        for prop in self.props[:5]:
            resp = self.client.post(unlock_url(prop))
            self.assertRedirects(resp, prop.get_absolute_url() + "#contact", fetch_redirect_response=False)
        self.assertContains(self.client.get(first.get_absolute_url()), "tel:" + first.contact_phone)
        # Re-opening an unlocked listing is free.
        self.client.post(unlock_url(first))
        self.assertEqual(ContactUnlock.objects.filter(user=self.customer).count(), 5)
        # The sixth listing asks for the Contact Pass.
        sixth = self.props[5]
        page = self.client.get(sixth.get_absolute_url())
        self.assertNotContains(page, "tel:" + sixth.contact_phone)
        self.assertContains(page, "Get Contact Pass")
        resp = self.client.post(unlock_url(sixth))
        self.assertTrue(resp["Location"].startswith(reverse("payments:contact_pass")))
        self.assertFalse(ContactUnlock.objects.filter(user=self.customer, property=sixth).exists())

    def test_free_contacts_reset_each_month(self):
        last_month = timezone.now() - timedelta(days=40)
        for prop in self.props[:5]:
            unlock = ContactUnlock.objects.create(user=self.customer, property=prop)
            ContactUnlock.objects.filter(pk=unlock.pk).update(created_at=last_month)
        self.client.force_login(self.customer)
        self.client.post(unlock_url(self.props[5]))
        self.assertTrue(ContactUnlock.objects.filter(user=self.customer, property=self.props[5]).exists())
        # Listings unlocked last month stay unlocked.
        self.assertContains(self.client.get(self.props[0].get_absolute_url()), "tel:" + self.props[0].contact_phone)

    def test_owners_brokers_and_admins_are_not_limited(self):
        prop = self.props[0]
        for user in (make_user(Role.OWNER), make_user(Role.BROKER), make_user(Role.ADMIN)):
            self.client.force_login(user)
            self.assertContains(self.client.get(prop.get_absolute_url()), "tel:" + prop.contact_phone)

    def test_limit_can_be_switched_off(self):
        site = PlatformSetting.load()
        site.contact_limit_enabled = False
        site.save()
        self.client.force_login(self.customer)
        self.assertContains(self.client.get(self.props[0].get_absolute_url()), "tel:" + self.props[0].contact_phone)
        self.client.logout()
        self.assertContains(self.client.get(self.props[0].get_absolute_url()), "tel:" + self.props[0].contact_phone)

    def test_free_contact_count_is_editable(self):
        site = PlatformSetting.load()
        site.free_contacts_per_month = 1
        site.save()
        self.client.force_login(self.customer)
        self.client.post(unlock_url(self.props[0]))
        self.assertContains(self.client.get(self.props[1].get_absolute_url()), "Get Contact Pass")

    def test_active_pass_shows_every_contact(self):
        activate_subscription(self.customer, contact_pass_plan(), amount_paid=99)
        self.client.force_login(self.customer)
        for prop in self.props:
            self.assertContains(self.client.get(prop.get_absolute_url()), "tel:" + prop.contact_phone)


class ContactPassPaymentTests(TestCase):
    """The Contact Pass is bought through PayU for one billing period at a time."""

    def setUp(self):
        cache.clear()
        self.customer = make_user(Role.CUSTOMER)
        self.client.force_login(self.customer)
        self.plan = contact_pass_plan()

    def start(self, next_url=""):
        data = {"next": next_url} if next_url else {}
        resp = self.client.post(reverse("payments:contact_pass_subscribe"), data)
        payment = Payment.objects.get(user=self.customer, status="created")
        self.assertRedirects(resp, reverse("payments:pay", args=[payment.uid]), fetch_redirect_response=False)
        return payment

    def test_plan_is_99_for_30_days_for_customers(self):
        self.assertEqual(self.plan.price, 99)
        self.assertEqual(self.plan.billing_period_days, 30)
        page = self.client.get(reverse("payments:contact_pass"))
        self.assertContains(page, "Get Contact Pass")
        self.assertContains(page, "PayU")

    def test_pay_page_posts_signed_form_to_payu(self):
        payment = self.start()
        self.assertEqual(payment.gateway, Payment.Gateway.PAYU)
        self.assertTrue(payment.gateway_order_id.startswith("BPH") and len(payment.gateway_order_id) <= 25)
        page = self.client.get(reverse("payments:pay", args=[payment.uid]))
        self.assertContains(page, 'action="https://test.payu.in/_payment"')
        self.assertContains(page, 'name="amount" value="99.00"')
        self.assertContains(page, f'name="txnid" value="{payment.gateway_order_id}"')
        self.assertContains(page, 'name="surl" value="http://localhost:8000/payments/payu/return/"')
        self.assertContains(page, 'name="hash"')
        self.assertNotContains(page, settings.PAYU_MERCHANT_SALT)

    def test_successful_payment_activates_pass_and_returns_to_the_property(self):
        prop = make_property(contact_visibility="public")
        payment = self.start(next_url=prop.get_absolute_url())
        self.client.logout()  # PayU posts back cross-site, usually without our session cookie
        resp = self.client.post(reverse("payments:payu_return"), payu_reply(payment))
        self.assertRedirects(resp, prop.get_absolute_url(), fetch_redirect_response=False)
        payment.refresh_from_db()
        self.assertEqual(payment.status, Payment.Status.PAID)
        self.assertEqual(payment.gateway_payment_id, "403993715500000001")
        sub = Subscription.objects.get(user=self.customer)
        self.assertTrue(sub.is_current)
        self.client.force_login(self.customer)
        self.assertContains(self.client.get(prop.get_absolute_url()), "tel:" + prop.contact_phone)

    def test_buying_again_extends_the_pass(self):
        payment = self.start()
        self.client.post(reverse("payments:payu_return"), payu_reply(payment))
        first_end = Subscription.objects.get(user=self.customer).ends_at
        second = self.start()
        self.client.post(reverse("payments:payu_return"), payu_reply(second, mihpayid="403993715500000002"))
        self.assertEqual(Subscription.objects.get(user=self.customer).ends_at, first_end + timedelta(days=30))

    def test_forged_or_wrong_amount_reply_does_not_activate(self):
        payment = self.start()
        self.client.post(reverse("payments:payu_return"), payu_reply(payment, salt="not-the-salt"))
        self.client.post(reverse("payments:payu_return"), payu_reply(payment, amount="1.00"))
        payment.refresh_from_db()
        self.assertEqual(payment.status, Payment.Status.CREATED)
        self.assertFalse(Subscription.objects.exists())

    def test_failed_payment_is_recorded(self):
        payment = self.start()
        resp = self.client.post(reverse("payments:payu_return"), payu_reply(payment, status="failure", error_Message="Bank declined"))
        self.assertRedirects(resp, reverse("payments:contact_pass"), fetch_redirect_response=False)
        payment.refresh_from_db()
        self.assertEqual(payment.status, Payment.Status.FAILED)
        self.assertEqual(payment.failure_reason, "Bank declined")

    @override_settings(PAYU_MERCHANT_KEY="", PAYU_MERCHANT_SALT="")
    def test_without_payu_customers_are_sent_to_whatsapp(self):
        self.client.post(reverse("payments:contact_pass_subscribe"))
        self.assertFalse(Payment.objects.exists())
        self.assertContains(self.client.get(reverse("payments:contact_pass")), "Online payment is not available yet")


class ContactPassAccessTests(TestCase):
    def test_partners_are_sent_back(self):
        self.client.force_login(make_user(Role.OWNER))
        self.assertEqual(self.client.get(reverse("payments:contact_pass")).status_code, 302)

    def test_pricing_page_lists_listing_plans_only(self):
        resp = self.client.get(reverse("subscriptions:pricing"))
        self.assertNotContains(resp, "Contact Pass")

    def test_anonymous_must_sign_in(self):
        resp = self.client.get(reverse("payments:contact_pass"))
        self.assertEqual(resp.status_code, 302)
        self.assertIn(reverse("accounts:login"), resp["Location"])


class OwnerWhatsAppButtonTests(TestCase):
    def setUp(self):
        cache.clear()
        self.customer = make_user(Role.CUSTOMER)
        self.client.force_login(self.customer)

    def test_locked_listing_offers_call_and_whatsapp(self):
        prop = make_property(contact_visibility="public")
        page = self.client.get(prop.get_absolute_url())
        self.assertContains(page, 'name="open" value="phone"')
        self.assertContains(page, 'name="open" value="whatsapp"')

    def test_whatsapp_button_opens_chat_after_unlocking(self):
        prop = make_property(contact_visibility="public")
        resp = self.client.post(unlock_url(prop), {"open": "whatsapp"})
        self.assertTrue(resp["Location"].startswith("https://wa.me/919876500000?text="))
        self.assertTrue(ContactUnlock.objects.filter(user=self.customer, property=prop).exists())

    def test_phone_is_used_for_whatsapp_when_no_whatsapp_number(self):
        prop = make_property(contact_visibility="public", whatsapp_number="", contact_phone="+919845012345")
        self.client.post(unlock_url(prop))
        page = self.client.get(prop.get_absolute_url())
        self.assertContains(page, "https://wa.me/919845012345?text=")
        self.assertContains(page, "tel:+919845012345")
