from django.urls import path

from payments import views

app_name = "payments"

urlpatterns = [
    path("checkout/<slug:slug>/", views.checkout, name="checkout"),
    path("pay/<uuid:uid>/", views.pay, name="pay"),
    path("verify/", views.verify, name="verify"),
    path("failed/", views.failed, name="failed"),
    path("manual/<slug:slug>/", views.manual_payment, name="manual"),
    path("receipt/<uuid:uid>/", views.receipt, name="receipt"),
    path("razorpay/webhook/", views.razorpay_webhook, name="razorpay_webhook"),
]
