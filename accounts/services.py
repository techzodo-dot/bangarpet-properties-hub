import logging

from django.conf import settings
from django.core import signing
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.urls import reverse

logger = logging.getLogger("bph")

EMAIL_VERIFY_SALT = "bph.email-verification"
EMAIL_VERIFY_MAX_AGE = 60 * 60 * 24 * 3


def make_email_token(user):
    return signing.dumps({"uid": user.pk, "email": user.email}, salt=EMAIL_VERIFY_SALT)


def read_email_token(token):
    """Return (uid, email) or None for invalid/expired tokens."""
    try:
        data = signing.loads(token, salt=EMAIL_VERIFY_SALT, max_age=EMAIL_VERIFY_MAX_AGE)
    except signing.BadSignature:
        return None
    return data.get("uid"), data.get("email")


def send_verification_email(user):
    link = settings.SITE_URL + reverse("accounts:verify_email", args=[make_email_token(user)])
    context = {"user": user, "link": link}
    try:
        send_mail(
            subject="Verify your email - Bangarpet Property Hub",
            message=render_to_string("emails/verify_email.txt", context),
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[user.email],
            fail_silently=False,
        )
        return True
    except Exception:  # pragma: no cover - depends on SMTP availability
        logger.exception("Could not send verification email to user %s", user.pk)
        return False
