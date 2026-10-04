from unittest import mock

from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse

from core.models import PlatformSetting
from core.tests.factories import make_user
from notifications.models import Notification, NotificationDelivery
from notifications.services import notify


class NotificationDeliveryTests(TestCase):
    def test_in_app_and_email_delivery_recorded(self):
        user = make_user()
        with self.captureOnCommitCallbacks(execute=True):
            n = notify(user, Notification.Event.ADMIN_NOTICE, "Hello", "Body text", "/dashboard/")
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(n.deliveries.get(channel="email").status, "sent")
        self.assertEqual(n.deliveries.get(channel="whatsapp").status, "skipped")

    def test_whatsapp_not_configured_is_reported_honestly(self):
        site = PlatformSetting.load()
        site.whatsapp_notifications_enabled = True
        site.save()
        user = make_user()
        user.profile.notify_whatsapp = True
        user.profile.save()
        with self.captureOnCommitCallbacks(execute=True):
            n = notify(user, Notification.Event.NEW_ENQUIRY, "Enquiry", "Body")
        self.assertEqual(n.deliveries.get(channel="whatsapp").status, NotificationDelivery.Status.NOT_CONFIGURED)

    @override_settings(WHATSAPP_ACCESS_TOKEN="token", WHATSAPP_PHONE_NUMBER_ID="123")
    def test_whatsapp_sent_via_cloud_api(self):
        site = PlatformSetting.load()
        site.whatsapp_notifications_enabled = True
        site.save()
        user = make_user()
        user.profile.notify_whatsapp = True
        user.profile.save()
        resp = mock.Mock()
        resp.json.return_value = {"messages": [{"id": "wamid.1"}]}
        resp.raise_for_status.return_value = None
        with mock.patch("notifications.services.requests.post", return_value=resp) as post:
            with self.captureOnCommitCallbacks(execute=True):
                n = notify(user, Notification.Event.NEW_ENQUIRY, "Enquiry", "Body")
        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["template"]["name"], "bph_new_enquiry")
        self.assertEqual(n.deliveries.get(channel="whatsapp").provider_message_id, "wamid.1")

    def test_email_preference_respected(self):
        user = make_user()
        user.profile.notify_email = False
        user.profile.save()
        with self.captureOnCommitCallbacks(execute=True):
            notify(user, Notification.Event.ADMIN_NOTICE, "Hi", "Body")
        self.assertEqual(len(mail.outbox), 0)

    def test_open_marks_read_and_only_owner(self):
        user = make_user()
        n = Notification.objects.create(recipient=user, event="admin_notice", title="T", body="B", link="/dashboard/")
        other = make_user()
        self.client.force_login(other)
        self.assertEqual(self.client.post(reverse("notifications:open", args=[n.pk])).status_code, 404)
        self.client.force_login(user)
        resp = self.client.post(reverse("notifications:open", args=[n.pk]))
        self.assertRedirects(resp, "/dashboard/", fetch_redirect_response=False)
        n.refresh_from_db()
        self.assertTrue(n.is_read)

    def test_external_link_not_followed(self):
        user = make_user()
        n = Notification.objects.create(recipient=user, event="admin_notice", title="T", body="B", link="https://evil.example.com")
        self.client.force_login(user)
        resp = self.client.post(reverse("notifications:open", args=[n.pk]))
        self.assertEqual(resp["Location"], reverse("notifications:list"))
