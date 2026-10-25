from decimal import Decimal

from django.core.cache import cache
from django.forms.models import model_to_dict
from django.test import TestCase

from adminpanel.forms import PlatformSettingForm
from core.models import PlatformSetting


class GstSettingsTests(TestCase):
    def setUp(self):
        cache.clear()
        self.site = PlatformSetting.load()
        self.site.gst_percent = 0
        self.site.save()

    def tearDown(self):
        cache.clear()

    def form(self, rate):
        data = model_to_dict(self.site)
        data["gst_percent"] = rate
        return PlatformSettingForm(data=data, instance=self.site)

    def test_default_and_decimal_rate_round_trip(self):
        self.assertEqual(self.site.gst_percent, 0)
        form = self.form("18.25")
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        self.assertEqual(PlatformSetting.load().gst_percent, Decimal("18.25"))

    def test_invalid_rates_do_not_save(self):
        for rate in ("-0.01", "100.01", "18.123", "NaN", ""):
            with self.subTest(rate=rate):
                form = self.form(rate)
                self.assertFalse(form.is_valid())
                self.assertIn("gst_percent", form.errors)
        self.site.refresh_from_db()
        self.assertEqual(self.site.gst_percent, 0)

    def test_boundaries_are_accepted(self):
        for rate in ("0", "100"):
            with self.subTest(rate=rate):
                form = self.form(rate)
                self.assertTrue(form.is_valid(), form.errors)

    def test_field_renders_percentage_constraints(self):
        field = str(PlatformSettingForm(instance=self.site)["gst_percent"])
        for value in ('min="0"', 'max="100"', 'step="0.01"'):
            self.assertIn(value, field)
