from django.contrib.auth import views as auth_views
from django.urls import path

from accounts import views

app_name = "accounts"

urlpatterns = [
    path("login/", views.LoginView.as_view(), name="login"),
    path("register/", views.RegisterView.as_view(), name="register"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("accounts/continue/", views.post_login, name="post_login"),
    path("accounts/verify-email/<str:token>/", views.verify_email, name="verify_email"),
    path("accounts/verify-email-resend/", views.resend_verification, name="resend_verification"),
    path("accounts/password/change/", views.password_change, name="password_change"),
    path("accounts/password-reset/", views.PasswordResetView.as_view(), name="password_reset"),
    path(
        "accounts/password-reset/sent/",
        auth_views.PasswordResetDoneView.as_view(template_name="accounts/password_reset_done.html"),
        name="password_reset_done",
    ),
    path("accounts/reset/<uidb64>/<token>/", views.PasswordResetConfirmView.as_view(), name="password_reset_confirm"),
    path(
        "accounts/reset/complete/",
        auth_views.PasswordResetCompleteView.as_view(template_name="accounts/password_reset_complete.html"),
        name="password_reset_complete",
    ),
    path("accounts/documents/<int:pk>/", views.verification_document, name="verification_document"),
]
