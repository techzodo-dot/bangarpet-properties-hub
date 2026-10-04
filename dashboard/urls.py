from django.urls import path

from dashboard import views_customer as c
from dashboard import views_partner as p

app_name = "dashboard"

urlpatterns = [
    # Customer dashboard
    path("dashboard/", c.overview, name="customer_overview"),
    path("dashboard/profile/", c.profile, name="customer_profile"),
    path("dashboard/enquiries/", c.enquiries, name="customer_enquiries"),
    path("dashboard/enquiries/<int:pk>/cancel/", c.enquiry_cancel, name="customer_enquiry_cancel"),
    path("dashboard/visits/", c.visits, name="customer_visits"),
    path("dashboard/visits/<int:pk>/cancel/", c.visit_cancel, name="customer_visit_cancel"),
    path("dashboard/favourites/", c.favourites, name="customer_favourites"),
    path("dashboard/saved-searches/", c.saved_searches, name="customer_saved_searches"),
    path("dashboard/saved-searches/<int:pk>/delete/", c.saved_search_delete, name="customer_saved_search_delete"),
    path("dashboard/recently-viewed/", c.recently_viewed, name="customer_recent"),
    path("dashboard/settings/", c.account_settings, name="customer_settings"),
    # Owner / broker dashboard
    path("partner/dashboard/", p.overview, name="partner_overview"),
    path("partner/properties/", p.property_list, name="partner_properties"),
    path("partner/properties/add/", p.property_add, name="partner_property_add"),
    path("partner/properties/<int:pk>/", p.property_manage, name="partner_property_manage"),
    path("partner/properties/<int:pk>/edit/<int:step>/", p.property_step, name="partner_property_step"),
    path("partner/properties/<int:pk>/images/<int:image_pk>/<str:action>/", p.image_action, name="partner_image_action"),
    path("partner/properties/<int:pk>/<str:action>/", p.property_action, name="partner_property_action"),
    path("partner/enquiries/", p.enquiry_list, name="partner_enquiries"),
    path("partner/enquiries/<int:pk>/", p.enquiry_detail, name="partner_enquiry_detail"),
    path("partner/visits/", p.visit_list, name="partner_visits"),
    path("partner/visits/<int:pk>/<str:action>/", p.visit_action, name="partner_visit_action"),
    path("partner/subscription/", p.subscription, name="partner_subscription"),
    path("partner/payments/", p.payment_list, name="partner_payments"),
    path("partner/profile/", p.profile, name="partner_profile"),
    path("partner/profile/documents/<int:pk>/delete/", p.document_delete, name="partner_document_delete"),
]
