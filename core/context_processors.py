from django.conf import settings

from core.models import PlatformSetting


def platform(request):
    site = PlatformSetting.load()
    ctx = {
        "site": site,
        "SITE_URL": settings.SITE_URL,
        "GOOGLE_MAPS_API_KEY": settings.GOOGLE_MAPS_API_KEY,
    }
    user = getattr(request, "user", None)
    if user is not None and user.is_authenticated:
        ctx["unread_notifications"] = user.notifications.filter(is_read=False).count()
    return ctx
