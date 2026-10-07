import importlib
import os
from unittest import mock

from django.test import SimpleTestCase


def load(**env):
    with mock.patch.dict(os.environ, env):
        import config.settings.base as base

        return importlib.reload(base)


class EmailSettingsTests(SimpleTestCase):
    def tearDown(self):
        load()

    def test_port_587_always_uses_starttls(self):
        s = load(EMAIL_HOST="smtp.gmail.com", EMAIL_PORT="587", EMAIL_USE_TLS="False")
        self.assertTrue(s.EMAIL_USE_TLS)
        self.assertFalse(s.EMAIL_USE_SSL)

    def test_port_465_uses_ssl(self):
        s = load(EMAIL_HOST="smtp.gmail.com", EMAIL_PORT="465", EMAIL_USE_TLS="True")
        self.assertTrue(s.EMAIL_USE_SSL)
        self.assertFalse(s.EMAIL_USE_TLS)

    def test_gmail_app_password_spaces_are_removed(self):
        s = load(EMAIL_HOST="smtp.gmail.com", EMAIL_HOST_PASSWORD="abcd efgh ijkl mnop")
        self.assertEqual(s.EMAIL_HOST_PASSWORD, "abcdefghijklmnop")
