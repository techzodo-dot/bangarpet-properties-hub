from django.test import TestCase

from core.models import PlatformSetting
from core.templatetags.bph import phone


class SupportContactTests(TestCase):
    def test_phone_is_shown_readably(self):
        self.assertEqual(phone("+919445330547"), "+91 94453 30547")
        self.assertEqual(phone("9445330547"), "+91 94453 30547")
        self.assertEqual(phone("080-1234"), "080-1234")

    def test_support_email_call_and_whatsapp_on_public_pages(self):
        site = PlatformSetting.load()
        site.contact_email = "bangarpetpropertyhub@gmail.com"
        site.support_phone = site.whatsapp_number = "+919445330547"
        site.save()
        for url in ("/", "/contact/"):
            resp = self.client.get(url)
            self.assertContains(resp, 'href="tel:+919445330547"')
            self.assertContains(resp, "+91 94453 30547")
            self.assertContains(resp, 'href="https://wa.me/919445330547"')
            self.assertContains(resp, 'href="mailto:bangarpetpropertyhub@gmail.com"')
