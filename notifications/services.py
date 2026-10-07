"""Notification dispatch: in-app record + optional email and WhatsApp delivery.

Every delivery attempt is stored in NotificationDelivery with an honest
status. When an integration is not configured the attempt is recorded as
``not_configured`` - nothing is reported as delivered unless the provider
accepted it.
"""
import logging

import requests
from django.conf import settings
from django.core.mail import send_mail
from django.db import transaction
from django.template.loader import render_to_string
from django.urls import reverse

from core.models import PlatformSetting
from notifications.models import Notification, NotificationDelivery

logger = logging.getLogger("bph")

# WhatsApp template names (must be created and approved in WhatsApp Manager).
# Each template receives a single body parameter {{1}} containing the message text.
WHATSAPP_TEMPLATES = {
    Notification.Event.NEW_ENQUIRY: "bph_new_enquiry",
    Notification.Event.VISIT_REQUEST: "bph_visit_request",
    Notification.Event.VISIT_CONFIRMED: "bph_visit_confirmed",
    Notification.Event.LISTING_APPROVED: "bph_listing_approved",
    Notification.Event.LISTING_REJECTED: "bph_listing_rejected",
    Notification.Event.LISTING_EXPIRING: "bph_listing_expiring",
    Notification.Event.SUBSCRIPTION_PURCHASED: "bph_subscription_purchased",
    Notification.Event.PAYMENT_CONFIRMED: "bph_payment_confirmed",
    Notification.Event.SUBSCRIPTION_EXPIRING: "bph_subscription_expiring",
    Notification.Event.ADMIN_NOTICE: "bph_admin_notice",
}


def email_configured():
    return bool(getattr(settings, "EMAIL_DELIVERY_CONFIGURED", False))


def whatsapp_configured():
    return bool(settings.WHATSAPP_ACCESS_TOKEN and settings.WHATSAPP_PHONE_NUMBER_ID)


def integration_status():
    site = PlatformSetting.load()
    return [
        {"name": "Email (SMTP)", "configured": email_configured(), "enabled": site.email_notifications_enabled,
         "hint": "Set EMAIL_HOST, EMAIL_HOST_USER and EMAIL_HOST_PASSWORD."},
        {"name": "WhatsApp Business API", "configured": whatsapp_configured(), "enabled": site.whatsapp_notifications_enabled,
         "hint": "Set WHATSAPP_ACCESS_TOKEN and WHATSAPP_PHONE_NUMBER_ID, and get the bph_* templates approved."},
        {"name": f"PayU payments ({'live' if settings.PAYU_MODE == 'live' else 'test'} mode)",
         "configured": bool(settings.PAYU_MERCHANT_KEY and settings.PAYU_MERCHANT_SALT), "enabled": True,
         "hint": "Set PAYU_MERCHANT_KEY, PAYU_MERCHANT_SALT and PAYU_MODE (test or live)."},
        {"name": "PayU webhook", "configured": bool(settings.PAYU_MERCHANT_KEY and settings.PAYU_MERCHANT_SALT), "enabled": True,
         "hint": "In the PayU dashboard add a webhook to /payments/payu/webhook/ for successful and failed payments."},
        {"name": "Google Maps", "configured": bool(settings.GOOGLE_MAPS_API_KEY), "enabled": True,
         "hint": "Set GOOGLE_MAPS_API_KEY (restrict it to your domain)."},
    ]


def _send_email(notification, link):
    user = notification.recipient
    context = {
        "recipient": user, "notification": notification, "link": link,
        "settings_link": settings.SITE_URL + reverse("dashboard:customer_settings"),
    }
    send_mail(
        subject=f"{notification.title} - Bangarpet Property Hub",
        message=render_to_string("emails/notification.txt", context),
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[user.email],
        fail_silently=False,
    )


def _send_whatsapp(to_number, template, text):
    url = f"https://graph.facebook.com/{settings.WHATSAPP_API_VERSION}/{settings.WHATSAPP_PHONE_NUMBER_ID}/messages"
    payload = {
        "messaging_product": "whatsapp",
        "to": "".join(ch for ch in to_number if ch.isdigit()),
        "type": "template",
        "template": {
            "name": template,
            "language": {"code": settings.WHATSAPP_TEMPLATE_LANGUAGE},
            "components": [{"type": "body", "parameters": [{"type": "text", "text": text[:1000]}]}],
        },
    }
    resp = requests.post(
        url, json=payload, timeout=8,
        headers={"Authorization": f"Bearer {settings.WHATSAPP_ACCESS_TOKEN}"},
    )
    resp.raise_for_status()
    data = resp.json()
    return (data.get("messages") or [{}])[0].get("id", "")


def deliver(notification, link=""):
    """Attempt email and WhatsApp delivery for a notification, recording each outcome."""
    user = notification.recipient
    profile = getattr(user, "profile", None)
    site = PlatformSetting.load()
    absolute_link = settings.SITE_URL + link if link.startswith("/") else link

    # Email
    if not (site.email_notifications_enabled and (profile is None or profile.notify_email)):
        NotificationDelivery.objects.create(notification=notification, channel="email", status="skipped", destination=user.email)
    elif not email_configured():
        NotificationDelivery.objects.create(notification=notification, channel="email", status="not_configured", destination=user.email)
    else:
        try:
            _send_email(notification, absolute_link)
            NotificationDelivery.objects.create(notification=notification, channel="email", status="sent", destination=user.email)
        except Exception as exc:  # network/SMTP failures must not break the request
            logger.warning("Email delivery failed for notification %s: %s", notification.pk, exc)
            NotificationDelivery.objects.create(notification=notification, channel="email", status="failed",
                                                destination=user.email, error=str(exc)[:500])

    # WhatsApp
    number = (profile.whatsapp_number if profile and profile.whatsapp_number else user.phone) or ""
    template = WHATSAPP_TEMPLATES.get(notification.event)
    if not (site.whatsapp_notifications_enabled and profile and profile.notify_whatsapp and number and template):
        NotificationDelivery.objects.create(notification=notification, channel="whatsapp", status="skipped", destination=number)
    elif not whatsapp_configured():
        NotificationDelivery.objects.create(notification=notification, channel="whatsapp", status="not_configured", destination=number)
    else:
        try:
            msg_id = _send_whatsapp(number, template, notification.body)
            NotificationDelivery.objects.create(notification=notification, channel="whatsapp", status="sent",
                                                destination=number, provider_message_id=msg_id)
        except Exception as exc:
            logger.warning("WhatsApp delivery failed for notification %s: %s", notification.pk, exc)
            NotificationDelivery.objects.create(notification=notification, channel="whatsapp", status="failed",
                                                destination=number, error=str(exc)[:500])


def notify(recipient, event, title, body, link=""):
    """Create an in-app notification and schedule external delivery after commit."""
    notification = Notification.objects.create(recipient=recipient, event=event, title=title[:160], body=body[:2000], link=link[:255])
    transaction.on_commit(lambda: deliver(notification, link))
    return notification


def notify_admins(event, title, body, link=""):
    from accounts.models import Role, User

    for admin in User.objects.filter(role=Role.ADMIN, is_active=True):
        Notification.objects.create(recipient=admin, event=event, title=title[:160], body=body[:2000], link=link[:255])
