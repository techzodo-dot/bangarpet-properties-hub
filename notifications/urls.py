from django.urls import path

from notifications import views

app_name = "notifications"

urlpatterns = [
    path("", views.notification_list, name="list"),
    path("<int:pk>/open/", views.open_notification, name="open"),
    path("mark-all-read/", views.mark_all_read, name="mark_all_read"),
]
