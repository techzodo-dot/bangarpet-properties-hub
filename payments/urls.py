from django.urls import path

from payments import views, views_contact

app_name = "payments"

urlpatterns = [
    path("checkout/<slug:slug>/", views.checkout, name="checkout"),
    path("pay/<uuid:uid>/", views.pay, name="pay"),
    path("verify/", views.verify, name="verify"),
    path("failed/", views.failed, name="failed"),
    path("manual/<slug:slug>/", views.manual_payment, name="manual"),
    path("request/<slug:slug>/", views.request_plan, name="request"),
    path("contact-pass/", views_contact.contact_pass, name="contact_pass"),
    path("contact-pass/subscribe/", views_contact.subscribe, name="contact_pass_subscribe"),
    path("contact-pass/pay/<uuid:uid>/", views_contact.pay, name="contact_pass_pay"),
    path("contact-pass/verify/", views_contact.verify, name="contact_pass_verify"),
    path("contact-pass/failed/", views_contact.failed, name="contact_pass_failed"),
    path("contact-pass/cancel/", views_contact.cancel, name="contact_pass_cancel"),
    path("receipt/<uuid:uid>/", views.receipt, name="receipt"),
    path("razorpay/webhook/", views.razorpay_webhook, name="razorpay_webhook"),
]
