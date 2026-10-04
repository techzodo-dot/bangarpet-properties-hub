from django.urls import path

from enquiries import views

app_name = "enquiries"

urlpatterns = [
    path("property/<int:pk>/enquire/", views.create_enquiry, name="create"),
]
