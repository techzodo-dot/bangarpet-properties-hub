from django.urls import path

from adminpanel import views, views_staff

app_name = "adminpanel"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("users/", views.users, name="users"),
    path("users/<int:pk>/", views.user_detail, name="user_detail"),
    path("users/<int:pk>/verification/", views.verification_decide, name="verification_decide"),
    path("users/<int:pk>/<str:action>/", views.user_action, name="user_action"),
    path("verifications/", views.verifications, name="verifications"),
    path("documents/<int:pk>/decide/", views.document_decide, name="document_decide"),
    path("properties/", views.properties, name="properties"),
    path("approvals/", views.properties, {"approvals": True}, name="approvals"),
    path("properties/<int:pk>/", views.property_review, name="property_review"),
    path("properties/<int:pk>/<str:action>/", views.property_action, name="property_action"),
    path("enquiries/", views.enquiries, name="enquiries"),
    path("subscriptions/", views.subscriptions, name="subscriptions"),
    path("payments/", views.payments, name="payments"),
    path("payments/<int:pk>/<str:action>/", views.payment_action, name="payment_action"),
    path("reports/", views.reports, name="reports"),
    path("reports/<int:pk>/<str:action>/", views.report_action, name="report_action"),
    path("settings/", views.settings_view, name="settings"),
    path("audit-logs/", views.audit_logs, name="audit_logs"),
    path("messages/", views.contact_messages, name="messages"),
    path("notices/", views.broadcast, name="broadcast"),
    path("banners/", views.crud_list, {"kind": "banners"}, name="banners"),
    path("staff-home/", views_staff.staff_home, name="staff_home"),
    path("staff/", views_staff.staff_list, name="staff"),
    path("staff/add/", views_staff.staff_edit, name="staff_add"),
    path("staff/<int:pk>/", views_staff.staff_edit, name="staff_edit"),
    path("expenses/", views_staff.expenses, name="expenses"),
    path("expenses/add/", views_staff.expense_edit, name="expense_add"),
    path("expenses/<int:pk>/", views_staff.expense_edit, name="expense_edit"),
    path("expenses/<int:pk>/delete/", views_staff.expense_delete, name="expense_delete"),
    path("expenses/<int:pk>/receipt/", views_staff.expense_receipt, name="expense_receipt"),
    path("manage/<str:kind>/", views.crud_list, name="crud_list"),
    path("manage/<str:kind>/add/", views.crud_edit, name="crud_add"),
    path("manage/<str:kind>/<int:pk>/", views.crud_edit, name="crud_edit"),
    path("manage/<str:kind>/<int:pk>/delete/", views.crud_delete, name="crud_delete"),
]
