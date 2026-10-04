import re

from django.test import TestCase
from django.urls import reverse
from django.utils import translation

from core.i18n_catalog import all_messages
from core.tests.factories import make_property

KN_LETTER = re.compile(r"[ಀ-೿]")


class KannadaTranslationTests(TestCase):
    """Customers can read the public website in Kannada."""

    def _kn(self):
        self.client.cookies["bph_language"] = "kn"

    def test_every_public_string_has_a_kannada_translation(self):
        singular, plurals = all_messages()
        with translation.override("kn"):
            catalog = translation.trans_real.translation("kn")._catalog
            missing = [m for m in singular if m != "---------" and m not in catalog]
            missing += [one for one, _ in plurals if (one, 0) not in catalog]
        self.assertEqual(missing, [], f"Add these to locale/kn_translations.py and run scripts/build_translations.py: {missing}")

    def test_language_switch_sets_cookie_and_returns_to_page(self):
        resp = self.client.post(reverse("set_language"), {"language": "kn", "next": "/properties/"})
        self.assertRedirects(resp, "/properties/", fetch_redirect_response=False)
        self.assertEqual(resp.cookies["bph_language"].value, "kn")
        page = self.client.get("/")
        self.assertContains(page, '<html lang="kn"')
        self.assertContains(page, "ಮುಖಪುಟ")  # "Home" in the tab bar
        self.assertContains(page, "ಬಂಗಾರಪೇಟೆಯಲ್ಲಿ ನಂಬಿಕೆಯ")  # hero heading
        self.assertContains(page, 'value="en"')  # switch back to English

    def test_kannada_phone_browsers_get_kannada_automatically(self):
        page = self.client.get("/", HTTP_ACCEPT_LANGUAGE="kn-IN,kn;q=0.9,en;q=0.8")
        self.assertContains(page, "ಬಾಡಿಗೆಗೆ")

    def test_english_stays_default(self):
        page = self.client.get("/")
        self.assertContains(page, '<html lang="en-IN"')
        self.assertContains(page, "Find a <span")
        self.assertContains(page, "ಕನ್ನಡ")  # the switch offers Kannada

    def test_public_pages_render_in_kannada(self):
        prop = make_property(with_image=True)
        self._kn()
        pages = {
            "/": "ನಿಮ್ಮ ಆಸ್ತಿಯನ್ನು ಪಟ್ಟಿ ಮಾಡಿ",
            "/properties/": "ಬಂಗಾರಪೇಟೆಯಲ್ಲಿ ಆಸ್ತಿಗಳನ್ನು ಹುಡುಕಿ",
            "/rent/": "ಬಾಡಿಗೆಗೆ ಆಸ್ತಿಗಳು",
            prop.get_absolute_url(): "ಆಸ್ತಿಯ ವಿವರಗಳು",
            "/login/": "ಮತ್ತೆ ಸ್ವಾಗತ",
            "/register/": "ನಿಮ್ಮ ಖಾತೆ ತೆರೆಯಿರಿ",
            "/contact/": "ನಮ್ಮನ್ನು ಸಂಪರ್ಕಿಸಿ",
            "/faq/": "ಆಸ್ತಿಗಳನ್ನು ಹುಡುಕುವುದು ಉಚಿತವೇ?",
        }
        for url, text in pages.items():
            with self.subTest(url=url):
                resp = self.client.get(url)
                self.assertContains(resp, text)

    def test_search_form_and_property_facts_are_translated(self):
        prop = make_property(with_image=True)
        self._kn()
        search = self.client.get("/properties/").content.decode()
        self.assertIn("ಎಲ್ಲಾ ಆಸ್ತಿ ಪ್ರಕಾರಗಳು", search)  # category select placeholder
        self.assertIn("ಸ್ವತಂತ್ರ ಮನೆ", search)  # category name from the database
        self.assertIn("ಬೆಲೆ: ಕಡಿಮೆಯಿಂದ ಹೆಚ್ಚು", search)  # sort option
        detail = self.client.get(prop.get_absolute_url()).content.decode()
        self.assertIn("ಭಾಗಶಃ ಪೀಠೋಪಕರಣ", detail)  # furnishing choice
        self.assertIn(prop.title, detail)  # owners' own text is shown as written
