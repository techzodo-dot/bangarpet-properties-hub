from django.conf import settings
from django.conf.urls.static import static
from django.urls import include, path, re_path
from django.views.static import serve

urlpatterns = [
    path("", include("core.urls")),
    path("", include("accounts.urls")),
    path("", include("properties.urls")),
    path("", include("enquiries.urls")),
    path("", include("subscriptions.urls")),
    path("", include("dashboard.urls")),
    path("management/", include("adminpanel.urls")),
    path("payments/", include("payments.urls")),
    path("notifications/", include("notifications.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
elif settings.SERVE_MEDIA:
    # Fallback for hosts without a web-server alias for /media/. Only public
    # media is served; verification documents are stored outside MEDIA_ROOT.
    urlpatterns += [re_path(r"^media/(?P<path>.*)$", serve, {"document_root": settings.MEDIA_ROOT})]

handler404 = "core.views.error_404"
handler500 = "core.views.error_500"
handler403 = "core.views.error_403"
