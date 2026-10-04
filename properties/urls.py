from django.urls import path

from properties import views

app_name = "properties"

urlpatterns = [
    path("properties/", views.property_search, name="search"),
    path("properties/save-search/", views.save_search, name="save_search"),
    path("properties/<slug:category_slug>/", views.property_search, name="category"),
    path("rent/", views.property_search, {"preset": "rent"}, name="rent"),
    path("buy/", views.property_search, {"preset": "buy"}, name="buy"),
    path("pg-rooms/", views.property_search, {"preset": "pg_rooms"}, name="pg_rooms"),
    path("commercial/", views.property_search, {"preset": "commercial"}, name="commercial"),
    path("property/<slug:slug>/", views.property_detail, name="detail"),
    path("property/<int:pk>/favourite/", views.toggle_favourite, name="toggle_favourite"),
    path("property/<int:pk>/report/", views.report_property, name="report"),
    path("partners/<int:pk>/", views.partner_profile, name="partner_profile"),
]
