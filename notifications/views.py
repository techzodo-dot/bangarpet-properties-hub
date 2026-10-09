from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from notifications.models import Notification


def _base_template(user):
    if user.is_management:
        return "adminpanel/base.html"
    return "dashboard/partner_base.html" if user.is_partner else "dashboard/customer_base.html"


@login_required
def notification_list(request):
    qs = request.user.notifications.all()
    if request.GET.get("filter") == "unread":
        qs = qs.filter(is_read=False)
    page_obj = Paginator(qs, 20).get_page(request.GET.get("page"))
    return render(request, "notifications/list.html", {
        "page_obj": page_obj, "base_template": _base_template(request.user), "active": "notifications",
        "querystring": "filter=unread" if request.GET.get("filter") == "unread" else "",
    })


@login_required
@require_POST
def open_notification(request, pk):
    notification = get_object_or_404(Notification, pk=pk, recipient=request.user)
    if not notification.is_read:
        notification.is_read = True
        notification.save(update_fields=["is_read"])
    link = notification.link
    if link and url_has_allowed_host_and_scheme(link, allowed_hosts={request.get_host()}):
        return redirect(link)
    return redirect("notifications:list")


@login_required
@require_POST
def mark_all_read(request):
    request.user.notifications.filter(is_read=False).update(is_read=True)
    return redirect("notifications:list")
