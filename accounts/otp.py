"""One-time codes sent by email.

Used for verifying an email address at sign-up, signing in with a code,
resetting a forgotten password and the admin 2-step sign-in. Codes are six
digits, valid for 10 minutes, single use, and only a keyed hash is stored.
Requesting a new code cancels the previous one.
"""
import hashlib
import hmac
import logging
import secrets
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.mail import EmailMultiAlternatives
from django.core.validators import validate_email
from django.db.models import F
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.translation import gettext as _

from accounts.models import EmailOTP
from core import ratelimit

logger = logging.getLogger("bph")

Purpose = EmailOTP.Purpose
CODE_LENGTH = 6
CODE_TTL = timedelta(minutes=10)
MAX_ATTEMPTS = 5
RESEND_AFTER_SECONDS = 60


class OTPError(Exception):
    """A code could not be sent or accepted; the message is safe to show."""


def otp_enabled():
    """Codes are used only when email can actually be delivered (or in DEBUG, via the console)."""
    return bool(settings.EMAIL_OTP_ENABLED and (settings.EMAIL_DELIVERY_CONFIGURED or settings.DEBUG))


def _deliverable(address):
    try:
        validate_email(address)
    except ValidationError:
        return False
    return True


def delivery_address(user):
    """The inbox a user's codes go to, or None when there is none."""
    if _deliverable(user.email):
        return user.email
    if user.is_platform_admin and settings.ADMIN_OTP_EMAIL and _deliverable(settings.ADMIN_OTP_EMAIL):
        return settings.ADMIN_OTP_EMAIL
    return None


def admin_two_step_required(user):
    """Admins and staff confirm each sign-in with an emailed code once email is set up."""
    if not (user.is_management and otp_enabled()):
        return False
    if delivery_address(user) is None:
        logger.warning("Admin 2-step sign-in skipped for user %s: no email address to send codes to "
                       "(set ADMIN_OTP_EMAIL).", user.pk)
        return False
    return True


def mask_email(address):
    name, _sep, domain = (address or "").partition("@")
    if not domain:
        return address
    shown = name[:2] if len(name) > 2 else name[:1]
    return f"{shown}{'*' * max(len(name) - len(shown), 3)}@{domain}"


def _hash(user, purpose, code):
    message = f"{user.pk}:{purpose}:{code}".encode()
    return hmac.new(settings.SECRET_KEY.encode(), message, hashlib.sha256).hexdigest()


def _clean_code(code):
    return "".join(ch for ch in str(code or "") if ch.isdigit())


def seconds_until_resend(user, purpose):
    last = EmailOTP.objects.filter(user=user, purpose=purpose).order_by("-created_at").first()
    if not last:
        return 0
    waited = (timezone.now() - last.created_at).total_seconds()
    return max(0, int(RESEND_AFTER_SECONDS - waited))


def send_code(user, purpose):
    """Create a new code and email it. Returns the masked address; raises OTPError."""
    address = delivery_address(user)
    if not address:
        raise OTPError(_("There is no email address on this account to send a code to."))
    wait = seconds_until_resend(user, purpose)
    if wait:
        raise OTPError(_("Please wait %(seconds)s seconds before asking for another code.") % {"seconds": wait})
    if not ratelimit.check_and_hit("otp_send", f"{user.pk}"):
        raise OTPError(_("Too many codes requested. Please try again in an hour."))

    now = timezone.now()
    code = f"{secrets.randbelow(10 ** CODE_LENGTH):0{CODE_LENGTH}d}"
    EmailOTP.objects.filter(user=user, purpose=purpose, used_at__isnull=True).update(used_at=now)
    otp = EmailOTP.objects.create(
        user=user, purpose=purpose, sent_to=address, code_hash=_hash(user, purpose, code), expires_at=now + CODE_TTL,
    )
    context = {"user": user, "code": code, "purpose": purpose, "minutes": int(CODE_TTL.total_seconds() // 60),
               "site_url": settings.SITE_URL}
    message = EmailMultiAlternatives(
        subject=f"{code} is your Bangarpet Property Hub code",
        body=render_to_string("emails/otp_code.txt", context),
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[address],
    )
    message.attach_alternative(render_to_string("emails/otp_code.html", context), "text/html")
    try:
        message.send(fail_silently=False)
    except Exception:
        logger.exception("Could not email a %s code to user %s", purpose, user.pk)
        otp.delete()
        raise OTPError(_("We couldn't send the email right now. Please try again in a few minutes.")) from None
    return mask_email(address)


def check_code(user, purpose, code):
    """Accept the latest unused code for this user and purpose, once. Raises OTPError."""
    otp = EmailOTP.objects.filter(user=user, purpose=purpose, used_at__isnull=True).order_by("-created_at").first()
    if otp is None or not otp.is_usable:
        raise OTPError(_("This code has expired. Ask for a new code."))
    if otp.attempts >= MAX_ATTEMPTS:
        raise OTPError(_("Too many wrong tries. Ask for a new code."))
    code = _clean_code(code)
    if len(code) != CODE_LENGTH or not hmac.compare_digest(otp.code_hash, _hash(user, purpose, code)):
        EmailOTP.objects.filter(pk=otp.pk).update(attempts=F("attempts") + 1)
        left = MAX_ATTEMPTS - otp.attempts - 1
        if left <= 0:
            raise OTPError(_("Too many wrong tries. Ask for a new code."))
        raise OTPError(_("That code is not correct. Tries left: %(left)s.") % {"left": left})
    # Mark it used atomically so the same code can't be accepted twice.
    if not EmailOTP.objects.filter(pk=otp.pk, used_at__isnull=True).update(used_at=timezone.now()):
        raise OTPError(_("This code has already been used. Ask for a new code."))
    return True
