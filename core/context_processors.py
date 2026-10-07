from django.conf import settings

from accounts.otp import otp_enabled
from core.models import PlatformSetting


def platform(request):
    site = PlatformSetting.load()
    ctx = {
        "site": site,
        "SITE_URL": settings.SITE_URL,
        "GOOGLE_MAPS_API_KEY": settings.GOOGLE_MAPS_API_KEY,
        "ONLINE_PAYMENTS": bool(settings.PAYU_MERCHANT_KEY and settings.PAYU_MERCHANT_SALT),
        "EMAIL_OTP": otp_enabled(),
    }
    user = getattr(request, "user", None)
    if user is not None and user.is_authenticated:
        ctx["unread_notifications"] = user.notifications.filter(is_read=False).count()
    return ctx
