"""Customer dashboard (/dashboard/). Available to every signed-in member:
owners and brokers can also save properties and send enquiries."""
from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Prefetch
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.forms import AccountForm, NotificationPreferencesForm, ProfileForm
from accounts.models import UserProfile
from core.audit import log_action
from core.permissions import member_required
from enquiries.models import Enquiry, EnquiryStatusChange, PropertyVisit
from enquiries.services import EnquiryError, cancel_enquiry, cancel_visit
from properties.models import Favourite, Property, PropertyImage, RecentlyViewed, SavedSearch

BASE = "dashboard/customer_base.html"


def _ctx(active, **extra):
    extra.update({"active": active, "base_template": BASE})
    return extra


def _card_prefetch(prefix):
    return [
        f"{prefix}__category", f"{prefix}__town", f"{prefix}__area",
    ], Prefetch(f"{prefix}__images", queryset=PropertyImage.objects.order_by("-is_primary", "order", "id"))


@member_required
def overview(request):
    user = request.user
    enquiries = user.enquiries.select_related("property").order_by("-updated_at")
    upcoming = user.visits.filter(status__in=["requested", "scheduled"]).select_related("property").order_by("preferred_date")
    sel, pre = _card_prefetch("property")
    recent = RecentlyViewed.objects.filter(user=user, property__in=Property.objects.public()).select_related("property", *sel).prefetch_related(pre)[:4]
    return render(request, "dashboard/customer/overview.html", _ctx(
        "overview",
        open_enquiries=enquiries.filter(status__in=Enquiry.OPEN_STATUSES).count(),
        upcoming_count=upcoming.count(),
        favourites_count=user.favourites.count(),
        saved_search_count=user.saved_searches.count(),
        latest_enquiries=enquiries[:5],
        upcoming_visits=upcoming[:5],
        recent=recent,
    ))


@member_required
def profile(request):
    user = request.user
    prof, _ = UserProfile.objects.get_or_create(user=user)
    account_form = AccountForm(request.POST or None, instance=user, prefix="account")
    profile_form = ProfileForm(request.POST or None, request.FILES or None, instance=prof, prefix="profile")
    if request.method == "POST" and account_form.is_valid() and profile_form.is_valid():
        account_form.save()
        profile_form.save()
        log_action(request, "account.profile_updated", user)
        messages.success(request, "Your profile has been updated.")
        return redirect("dashboard:customer_profile")
    return render(request, "dashboard/customer/profile.html", _ctx("profile", account_form=account_form, profile_form=profile_form))


@member_required
def enquiries(request):
    qs = (
        request.user.enquiries.select_related("property", "property__town", "property__area")
        .prefetch_related(Prefetch("status_changes", queryset=EnquiryStatusChange.objects.order_by("created_at")), "visits")
    )
    status = request.GET.get("status")
    if status == "open":
        qs = qs.filter(status__in=Enquiry.OPEN_STATUSES)
    elif status in Enquiry.Status.values:
        qs = qs.filter(status=status)
    page_obj = Paginator(qs, 10).get_page(request.GET.get("page"))
    return render(request, "dashboard/customer/enquiries.html", _ctx(
        "enquiries", page_obj=page_obj, status=status, statuses=Enquiry.Status.choices,
        querystring=f"status={status}" if status else "",
    ))


@member_required
@require_POST
def enquiry_cancel(request, pk):
    enquiry = get_object_or_404(Enquiry, pk=pk, customer=request.user)
    try:
        cancel_enquiry(enquiry, request.user)
        messages.success(request, "Your enquiry has been cancelled.")
    except EnquiryError as exc:
        messages.error(request, str(exc))
    return redirect("dashboard:customer_enquiries")


@member_required
def visits(request):
    qs = request.user.visits.select_related("property", "property__town", "property__area", "partner").order_by("-created_at")
    today = timezone.localdate()
    return render(request, "dashboard/customer/visits.html", _ctx(
        "visits",
        upcoming=[v for v in qs if v.is_active],
        past=[v for v in qs if not v.is_active][:30],
        today=today,
    ))


@member_required
@require_POST
def visit_cancel(request, pk):
    visit = get_object_or_404(PropertyVisit, pk=pk, customer=request.user)
    try:
        cancel_visit(visit, request.user, request.POST.get("reason", "")[:200])
        messages.success(request, "Your visit request has been cancelled.")
    except EnquiryError as exc:
        messages.error(request, str(exc))
    return redirect("dashboard:customer_visits")


@member_required
def favourites(request):
    sel, pre = _card_prefetch("property")
    favs = Favourite.objects.filter(user=request.user).select_related("property", "property__owner", *sel).prefetch_related(pre)
    page_obj = Paginator(favs, 12).get_page(request.GET.get("page"))
    fav_ids = set(Favourite.objects.filter(user=request.user).values_list("property_id", flat=True))
    return render(request, "dashboard/customer/favourites.html", _ctx("favourites", page_obj=page_obj, favourite_ids=fav_ids))


@member_required
def saved_searches(request):
    return render(request, "dashboard/customer/saved_searches.html", _ctx("searches", searches=request.user.saved_searches.all()))


@member_required
@require_POST
def saved_search_delete(request, pk):
    search = get_object_or_404(SavedSearch, pk=pk, user=request.user)
    search.delete()
    messages.success(request, "Saved search deleted.")
    return redirect("dashboard:customer_saved_searches")


@member_required
def recently_viewed(request):
    sel, pre = _card_prefetch("property")
    items = RecentlyViewed.objects.filter(user=request.user).select_related("property", "property__owner", *sel).prefetch_related(pre)[:24]
    fav_ids = set(Favourite.objects.filter(user=request.user).values_list("property_id", flat=True))
    return render(request, "dashboard/customer/recent.html", _ctx("recent", items=items, favourite_ids=fav_ids))


@member_required
def account_settings(request):
    prof, _ = UserProfile.objects.get_or_create(user=request.user)
    form = NotificationPreferencesForm(request.POST or None, instance=prof)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Notification preferences saved.")
        return redirect("dashboard:customer_settings")
    return render(request, "dashboard/customer/settings.html", _ctx("settings", form=form))
