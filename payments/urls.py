from django.urls import path

from payments import views, views_contact

app_name = "payments"

urlpatterns = [
    path("checkout/<slug:slug>/", views.checkout, name="checkout"),
    path("pay/<uuid:uid>/", views.pay, name="pay"),
    path("payu/return/", views.payu_return, name="payu_return"),
    path("payu/webhook/", views.payu_webhook, name="payu_webhook"),
    path("manual/<slug:slug>/", views.manual_payment, name="manual"),
    path("request/<slug:slug>/", views.request_plan, name="request"),
    path("contact-pass/", views_contact.contact_pass, name="contact_pass"),
    path("contact-pass/subscribe/", views_contact.subscribe, name="contact_pass_subscribe"),
    path("receipt/<uuid:uid>/", views.receipt, name="receipt"),
]
