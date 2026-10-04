"""Custom administration panel (/management/). Every view requires the admin role."""
from datetime import timedelta

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.core.paginator import Paginator
from django.db.models import Count, Q, Sum
from django.db.models.functions import TruncMonth
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.models import BrokerProfile, OwnerProfile, Role, VerificationDocument, VerificationStatus
from adminpanel import forms as f
from core.audit import log_action
from core.models import Advertisement, AuditLog, Banner, ContactMessage, PlatformSetting
from core.permissions import admin_required
from enquiries.models import Enquiry
from moderation import services as mod
from moderation.models import Report
from notifications.models import Notification
from notifications.services import integration_status, notify
from payments.models import Payment
from payments.services import mark_paid
from properties.models import Amenity, Category, Location, Property
from subscriptions.models import Subscription, SubscriptionPlan
from subscriptions.services import activate_subscription

User = get_user_model()
PAGE = 25


def _ctx(active, **extra):
    extra["active"] = active
    return extra


def _page(request, qs, size=PAGE):
    return Paginator(qs, size).get_page(request.GET.get("page"))


def _qs(request):
    params = request.GET.copy()
    params.pop("page", None)
    return params.urlencode()


def _months(n=6):
    today = timezone.localdate().replace(day=1)
    months = []
    for _ in range(n):
        months.insert(0, today)
        today = (today - timedelta(days=1)).replace(day=1)
    return months


def _monthly(qs, field, months, value=None):
    rows = qs.filter(**{f"{field}__date__gte": months[0]}).annotate(m=TruncMonth(field)).values("m")
    rows = rows.annotate(v=Sum(value) if value else Count("pk"))
    lookup = {r["m"].date() if hasattr(r["m"], "date") else r["m"]: r["v"] or 0 for r in rows}
    return [float(lookup.get(m, 0)) for m in months]


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------
@admin_required
def dashboard(request):
    now = timezone.now()
    month_start = timezone.localtime(now).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    props = Property.objects.exclude(status=Property.Status.DELETED)
    metrics = {
        "users": User.objects.filter(is_active=True).exclude(role=Role.ADMIN).count(),
        "customers": User.objects.filter(role=Role.CUSTOMER, is_active=True).count(),
        "owners": User.objects.filter(role=Role.OWNER, is_active=True).count(),
        "brokers": User.objects.filter(role=Role.BROKER, is_active=True).count(),
        "listings": props.exclude(status=Property.Status.DRAFT).count(),
        "pending": props.filter(status=Property.Status.PENDING).count(),
        "approved": props.public().count(),
        "rejected": props.filter(status__in=[Property.Status.REJECTED, Property.Status.CHANGES_REQUESTED]).count(),
        "active_subs": Subscription.objects.current().filter(plan__price__gt=0).count(),
        "month_revenue": Payment.objects.filter(status=Payment.Status.PAID, paid_at__gte=month_start).aggregate(s=Sum("amount"))["s"] or 0,
        "enquiries": Enquiry.objects.count(),
        "month_enquiries": Enquiry.objects.filter(created_at__gte=month_start).count(),
        "open_reports": Report.objects.filter(status__in=[Report.Status.OPEN, Report.Status.REVIEWING]).count(),
        "pending_verifications": OwnerProfile.objects.filter(verification_status=VerificationStatus.PENDING).count()
        + BrokerProfile.objects.filter(verification_status=VerificationStatus.PENDING).count(),
        "manual_payments": Payment.objects.filter(status=Payment.Status.PENDING_VERIFICATION).count(),
    }
    months = _months(6)
    by_cat = Category.objects.annotate(n=Count("properties", filter=Q(properties__in=props.public()))).order_by("display_order")
    inactive = props.exclude(status=Property.Status.DRAFT).count() - metrics["approved"]
    charts = {
        "months": [m.strftime("%b %Y") for m in months],
        "registrations": _monthly(User.objects.exclude(role=Role.ADMIN), "date_joined", months),
        "enquiries": _monthly(Enquiry.objects.all(), "created_at", months),
        "revenue": _monthly(Payment.objects.filter(status=Payment.Status.PAID), "paid_at", months, "amount"),
        "categories": {"labels": [c.name for c in by_cat], "values": [c.n for c in by_cat]},
        "active_inactive": [metrics["approved"], max(inactive, 0)],
    }
    return render(request, "adminpanel/dashboard.html", _ctx(
        "dashboard", metrics=metrics, charts=charts,
        pending_list=props.filter(status=Property.Status.PENDING).select_related("owner", "category").order_by("submitted_at")[:6],
        recent_reports=Report.objects.filter(status=Report.Status.OPEN).select_related("property")[:5],
        integrations=integration_status(),
    ))


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------
@admin_required
def users(request):
    qs = User.objects.select_related("owner_profile", "broker_profile").order_by("-date_joined")
    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(Q(full_name__icontains=q) | Q(email__icontains=q) | Q(phone__icontains=q) | Q(broker_profile__agency_name__icontains=q))
    role = request.GET.get("role")
    if role in Role.values:
        qs = qs.filter(role=role)
    status = request.GET.get("status")
    if status == "active":
        qs = qs.filter(is_active=True)
    elif status == "suspended":
        qs = qs.filter(is_active=False)
    elif status == "unverified_email":
        qs = qs.filter(email_verified=False)
    return render(request, "adminpanel/users.html", _ctx(
        "users", page_obj=_page(request, qs), q=q, role=role, status=status, roles=Role.choices, querystring=_qs(request),
    ))


@admin_required
def user_detail(request, pk):
    member = get_object_or_404(User.objects.select_related("profile"), pk=pk)
    partner_profile = member.partner_profile
    return render(request, "adminpanel/user_detail.html", _ctx(
        "users", member=member, partner_profile=partner_profile,
        documents=member.verification_documents.all(),
        properties=member.properties.exclude(status=Property.Status.DELETED).select_related("category")[:20],
        payments=member.payments.select_related("plan")[:10],
        subscriptions=member.subscriptions.select_related("plan")[:10],
        enquiries_sent=member.enquiries.count(),
        reports_filed=member.reports_filed.count(),
        audit=AuditLog.objects.filter(Q(actor=member) | Q(target_type="accounts.user", target_id=str(member.pk))).select_related("actor")[:30],
        suspend_form=f.SuspendForm(), role_form=f.RoleForm(initial={"role": member.role}),
        verification_form=f.VerificationDecisionForm(initial={"decision": "approve", "valid_days": 365}),
    ))


@admin_required
@require_POST
def user_action(request, pk, action):
    member = get_object_or_404(User, pk=pk)
    if member.pk == request.user.pk:
        messages.error(request, "You cannot change your own account here.")
        return redirect("adminpanel:user_detail", pk=pk)
    if member.is_superuser and not request.user.is_superuser:
        messages.error(request, "Only a superuser can modify another superuser.")
        return redirect("adminpanel:user_detail", pk=pk)
    if action == "suspend":
        form = f.SuspendForm(request.POST)
        if form.is_valid():
            member.is_active = False
            member.suspended_at = timezone.now()
            member.suspension_reason = form.cleaned_data["reason"]
            member.save(update_fields=["is_active", "suspended_at", "suspension_reason"])
            hidden = member.properties.filter(status=Property.Status.ACTIVE).update(status=Property.Status.PAUSED)
            from django.contrib.sessions.models import Session

            for session in Session.objects.filter(expire_date__gt=timezone.now()):
                if str(session.get_decoded().get("_auth_user_id")) == str(member.pk):
                    session.delete()
            log_action(request, "user.suspended", member, reason=form.cleaned_data["reason"], listings_paused=hidden)
            messages.success(request, f"Account suspended. {hidden} live listing(s) were paused.")
        else:
            messages.error(request, "Enter a reason for suspension.")
    elif action == "reactivate":
        member.is_active = True
        member.suspended_at = None
        member.suspension_reason = ""
        member.save(update_fields=["is_active", "suspended_at", "suspension_reason"])
        log_action(request, "user.reactivated", member)
        notify(member, Notification.Event.ACCOUNT_NOTICE, "Account reactivated",
               "Your account has been reactivated. Paused listings can be resumed from your dashboard.")
        messages.success(request, "Account reactivated.")
    elif action == "role":
        form = f.RoleForm(request.POST)
        if form.is_valid() and member.role != Role.ADMIN:
            old = member.role
            member.role = form.cleaned_data["role"]
            member.save(update_fields=["role"])
            from accounts.signals import ensure_profiles

            ensure_profiles(User, member, created=False)
            log_action(request, "user.role_changed", member, old=old, new=member.role)
            messages.success(request, "Role updated.")
    elif action == "verify_phone":
        member.phone_verified = not member.phone_verified
        member.save(update_fields=["phone_verified"])
        log_action(request, "user.phone_verified" if member.phone_verified else "user.phone_unverified", member)
        messages.success(request, "Phone verification status updated.")
    elif action == "verify_email":
        member.email_verified = True
        member.save(update_fields=["email_verified"])
        log_action(request, "user.email_marked_verified", member)
        messages.success(request, "Email marked as verified.")
    else:
        raise Http404
    return redirect("adminpanel:user_detail", pk=pk)


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------
@admin_required
def verifications(request):
    status = request.GET.get("status") or VerificationStatus.PENDING
    owners = OwnerProfile.objects.filter(verification_status=status).select_related("user")
    brokers = BrokerProfile.objects.filter(verification_status=status).select_related("user")
    items = sorted(list(owners) + list(brokers), key=lambda p: p.submitted_at or p.created_at, reverse=True)
    return render(request, "adminpanel/verifications.html", _ctx(
        "verifications", items=items, status=status, statuses=VerificationStatus.choices,
    ))


@admin_required
@require_POST
def verification_decide(request, pk):
    member = get_object_or_404(User, pk=pk, role__in=[Role.OWNER, Role.BROKER])
    profile = member.partner_profile
    if profile is None:
        raise Http404
    if request.POST.get("decision") == "expire":
        mod.expire_verification(request, profile)
        messages.success(request, "Verification marked as expired.")
        return redirect("adminpanel:user_detail", pk=pk)
    form = f.VerificationDecisionForm(request.POST)
    if form.is_valid():
        try:
            mod.decide_verification(request, profile, form.cleaned_data["decision"] == "approve",
                                    form.cleaned_data.get("note") or "", form.cleaned_data.get("valid_days") or 0)
            messages.success(request, "Verification decision saved and the partner has been notified.")
        except mod.ModerationError as exc:
            messages.error(request, str(exc))
    else:
        messages.error(request, "; ".join(e for errs in form.errors.values() for e in errs))
    return redirect("adminpanel:user_detail", pk=pk)


@admin_required
@require_POST
def document_decide(request, pk):
    doc = get_object_or_404(VerificationDocument, pk=pk)
    decision = request.POST.get("decision")
    if decision not in ("accepted", "rejected"):
        raise Http404
    doc.status = decision
    doc.reviewed_by = request.user
    doc.reviewed_at = timezone.now()
    doc.review_note = request.POST.get("note", "")[:255]
    from django.conf import settings

    doc.retain_until = timezone.now() + timedelta(days=settings.VERIFICATION_DOC_RETENTION_DAYS if decision == "accepted" else 30)
    doc.save()
    log_action(request, f"verification.document_{decision}", doc)
    messages.success(request, f"Document {decision}.")
    return redirect("adminpanel:user_detail", pk=doc.user_id)


# ---------------------------------------------------------------------------
# Listings & moderation
# ---------------------------------------------------------------------------
@admin_required
def properties(request, approvals=False):
    qs = Property.objects.exclude(status=Property.Status.DELETED).select_related("owner", "category", "town", "area")
    status = request.GET.get("status")
    if approvals:
        qs = qs.filter(status=Property.Status.PENDING).order_by("submitted_at")
    else:
        qs = qs.exclude(status=Property.Status.DRAFT) if status != "draft" else qs
        if status in Property.Status.values:
            qs = qs.filter(status=status)
        qs = qs.order_by("-updated_at")
    if request.GET.get("suspicious"):
        qs = qs.filter(is_suspicious=True)
    if request.GET.get("featured"):
        qs = qs.filter(featured_until__gt=timezone.now())
    cat = request.GET.get("category")
    if cat:
        qs = qs.filter(category__slug=cat)
    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(Q(title__icontains=q) | Q(reference__iexact=q.upper()) | Q(owner__email__icontains=q) | Q(owner__full_name__icontains=q))
    qs = qs.annotate(open_reports=Count("reports", filter=Q(reports__status__in=["open", "reviewing"])))
    return render(request, "adminpanel/properties.html", _ctx(
        "approvals" if approvals else "properties", page_obj=_page(request, qs), approvals=approvals, status=status, q=q,
        statuses=Property.Status.choices, categories=Category.objects.all(), category=cat, querystring=_qs(request),
    ))


@admin_required
def property_review(request, pk):
    prop = get_object_or_404(
        Property.objects.select_related("owner", "category", "town", "area").prefetch_related("images", "amenities"), pk=pk
    )
    return render(request, "adminpanel/property_review.html", _ctx(
        "approvals" if prop.status == Property.Status.PENDING else "properties",
        prop=prop, images=prop.images.all(), revisions=prop.revisions.select_related("user")[:15],
        reviews=prop.reviews.select_related("reviewer")[:15], reports=prop.reports.select_related("reporter")[:15],
        reason_form=f.ReasonForm(), feature_form=f.FeatureForm(), note_form=f.OptionalReasonForm(),
        owner_listing_count=Property.objects.owned_by(prop.owner).count(),
    ))


@admin_required
@require_POST
def property_action(request, pk, action):
    prop = get_object_or_404(Property, pk=pk)
    reason = request.POST.get("reason", "").strip()
    try:
        if action == "approve":
            mod.approve_listing(request, prop)
            messages.success(request, f"{prop.reference} approved and now live.")
            nxt = Property.objects.filter(status=Property.Status.PENDING).order_by("submitted_at").first()
            if nxt and request.POST.get("next") == "queue":
                return redirect("adminpanel:property_review", pk=nxt.pk)
        elif action == "reject":
            mod.reject_listing(request, prop, reason)
            messages.success(request, f"{prop.reference} rejected.")
        elif action == "request_changes":
            mod.reject_listing(request, prop, reason, request_changes=True)
            messages.success(request, f"Changes requested for {prop.reference}.")
        elif action == "remove":
            mod.remove_listing(request, prop, reason)
            messages.success(request, f"{prop.reference} removed.")
        elif action == "flag":
            mod.set_suspicious(request, prop, True, reason)
            messages.success(request, "Listing marked as suspicious.")
        elif action == "unflag":
            mod.set_suspicious(request, prop, False, reason)
            messages.success(request, "Suspicious flag cleared.")
        elif action == "feature":
            form = f.FeatureForm(request.POST)
            if not form.is_valid():
                raise mod.ModerationError("Enter a number of days between 0 and 365.")
            mod.set_featured(request, prop, form.cleaned_data["days"])
            messages.success(request, "Featured setting updated.")
        else:
            raise Http404
    except mod.ModerationError as exc:
        messages.error(request, str(exc))
    return redirect("adminpanel:property_review", pk=pk)


# ---------------------------------------------------------------------------
# Enquiries (read-only overview)
# ---------------------------------------------------------------------------
@admin_required
def enquiries(request):
    qs = Enquiry.objects.select_related("property", "customer", "partner").order_by("-created_at")
    status = request.GET.get("status")
    if status in Enquiry.Status.values:
        qs = qs.filter(status=status)
    return render(request, "adminpanel/enquiries.html", _ctx(
        "enquiries", page_obj=_page(request, qs), status=status, statuses=Enquiry.Status.choices, querystring=_qs(request),
    ))


# ---------------------------------------------------------------------------
# Subscriptions & payments
# ---------------------------------------------------------------------------
@admin_required
def subscriptions(request):
    qs = Subscription.objects.select_related("user", "plan").order_by("-created_at")
    status = request.GET.get("status")
    if status == "current":
        qs = qs.current()
    elif status in Subscription.Status.values:
        qs = qs.filter(status=status)
    grant_form = f.GrantSubscriptionForm(request.POST or None)
    if request.method == "POST" and grant_form.is_valid():
        sub = activate_subscription(grant_form.user, grant_form.cleaned_data["plan"], amount_paid=0,
                                    granted_by=request.user, notes=grant_form.cleaned_data["notes"])
        log_action(request, "subscription.granted", sub, plan=sub.plan.slug, notes=grant_form.cleaned_data["notes"])
        notify(grant_form.user, Notification.Event.SUBSCRIPTION_PURCHASED, f"{sub.plan.name} plan activated",
               f"Your {sub.plan.name} plan is active until {timezone.localtime(sub.ends_at):%d %b %Y}.",
               reverse("dashboard:partner_subscription"))
        messages.success(request, "Subscription granted.")
        return redirect("adminpanel:subscriptions")
    renewals_due = Subscription.objects.current().filter(ends_at__lte=timezone.now() + timedelta(days=7)).select_related("user", "plan")
    return render(request, "adminpanel/subscriptions.html", _ctx(
        "subscriptions", plans=SubscriptionPlan.objects.annotate(
            active_count=Count("subscriptions", filter=Q(subscriptions__status="active", subscriptions__ends_at__gt=timezone.now()))
        ),
        page_obj=_page(request, qs), status=status, statuses=Subscription.Status.choices, grant_form=grant_form,
        renewals_due=renewals_due, querystring=_qs(request),
    ))


@admin_required
def payments(request):
    qs = Payment.objects.select_related("user", "plan").order_by("-created_at")
    status = request.GET.get("status")
    if status in Payment.Status.values:
        qs = qs.filter(status=status)
    gateway = request.GET.get("gateway")
    if gateway in Payment.Gateway.values:
        qs = qs.filter(gateway=gateway)
    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(Q(user__email__icontains=q) | Q(gateway_payment_id__iexact=q) | Q(gateway_order_id__iexact=q) | Q(manual_reference__icontains=q))
    totals = Payment.objects.filter(status=Payment.Status.PAID).aggregate(total=Sum("amount"), count=Count("pk"))
    return render(request, "adminpanel/payments.html", _ctx(
        "payments", page_obj=_page(request, qs), status=status, gateway=gateway, q=q, totals=totals,
        statuses=Payment.Status.choices, gateways=Payment.Gateway.choices, querystring=_qs(request),
    ))


@admin_required
@require_POST
def payment_action(request, pk, action):
    payment = get_object_or_404(Payment, pk=pk)
    if payment.status != Payment.Status.PENDING_VERIFICATION:
        messages.error(request, "Only manual payments awaiting verification can be changed here.")
    elif action == "approve":
        mark_paid(payment.pk, verified_by=request.user, source="manual")
        messages.success(request, "Payment verified and subscription activated.")
    elif action == "reject":
        payment.status = Payment.Status.REJECTED
        payment.failure_reason = request.POST.get("reason", "")[:255] or "Payment could not be verified"
        payment.verified_by = request.user
        payment.save(update_fields=["status", "failure_reason", "verified_by", "updated_at"])
        log_action(request, "payment.manual_rejected", payment, reason=payment.failure_reason)
        notify(payment.user, Notification.Event.PAYMENT_FAILED, "Payment could not be verified",
               f"We could not verify your payment reference {payment.manual_reference}: {payment.failure_reason}",
               reverse("dashboard:partner_payments"))
        messages.success(request, "Payment rejected.")
    else:
        raise Http404
    return redirect(reverse("adminpanel:payments") + "?status=pending_verification")


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------
@admin_required
def reports(request):
    qs = Report.objects.select_related("property", "reporter").order_by("-created_at")
    status = request.GET.get("status", "open")
    if status in Report.Status.values:
        qs = qs.filter(status=status)
    return render(request, "adminpanel/reports.html", _ctx(
        "reports", page_obj=_page(request, qs), status=status, statuses=Report.Status.choices, querystring=_qs(request),
    ))


@admin_required
@require_POST
def report_action(request, pk, action):
    report = get_object_or_404(Report.objects.select_related("property"), pk=pk)
    note = request.POST.get("note", "").strip()[:1000]
    mapping = {"reviewing": Report.Status.REVIEWING, "resolve": Report.Status.RESOLVED, "dismiss": Report.Status.DISMISSED}
    if action not in mapping:
        raise Http404
    report.status = mapping[action]
    report.admin_note = note
    report.handled_by = request.user
    report.handled_at = timezone.now()
    report.save()
    log_action(request, f"report.{action}", report, note=note)
    if action in ("resolve", "dismiss"):
        notify(report.reporter, Notification.Event.ADMIN_NOTICE, "Update on your report",
               f"Thank you for reporting {report.property.reference}. Our team has reviewed it"
               + (" and taken action." if action == "resolve" else ". No policy violation was found."))
    messages.success(request, "Report updated.")
    return redirect(request.POST.get("return_to") or reverse("adminpanel:reports"))


# ---------------------------------------------------------------------------
# Generic CRUD for configuration models
# ---------------------------------------------------------------------------
CRUD = {
    "banners": {
        "model": Banner, "form": f.BannerForm, "title": "Banners", "singular": "banner", "active": "banners",
        "columns": [("Title", "title"), ("Placement", "get_placement_display"), ("Order", "display_order"),
                    ("Starts", "starts_at"), ("Ends", "ends_at"), ("Active", "is_active")],
        "image": "image",
    },
    "ads": {
        "model": Advertisement, "form": f.AdvertisementForm, "title": "Sponsored placements", "singular": "sponsored placement",
        "active": "banners", "columns": [("Listing", "property"), ("Placement", "get_placement_display"),
                                         ("Label", "label"), ("Starts", "starts_at"), ("Ends", "ends_at"), ("Active", "is_active")],
    },
    "plans": {
        "model": SubscriptionPlan, "form": f.PlanForm, "title": "Subscription plans", "singular": "plan", "active": "subscriptions",
        "columns": [("Name", "name"), ("Price (₹)", "price"), ("Effective (₹)", "effective_price"),
                    ("Listing limit", "listing_limit"), ("Duration (days)", "listing_duration_days"),
                    ("Default", "is_default"), ("Active", "is_active")],
        "no_delete": True,
    },
    "locations": {
        "model": Location, "form": f.LocationAdminForm, "title": "Locations", "singular": "location", "active": "catalog",
        "columns": [("Name", "__str__"), ("Type", "get_kind_display"), ("PIN", "pin_code"), ("Popular", "is_popular"), ("Active", "is_active")],
        "no_delete": True,
    },
    "categories": {
        "model": Category, "form": f.CategoryForm, "title": "Property categories", "singular": "category", "active": "catalog",
        "columns": [("Name", "name"), ("Slug", "slug"), ("Rent", "allows_rent"), ("Sale", "allows_sale"), ("Active", "is_active")],
        "no_delete": True,
    },
    "amenities": {
        "model": Amenity, "form": f.AmenityForm, "title": "Amenities", "singular": "amenity", "active": "catalog",
        "columns": [("Name", "name"), ("Icon", "icon"), ("Order", "display_order"), ("Active", "is_active")],
        "no_delete": True,
    },
}


def _crud(kind):
    conf = CRUD.get(kind)
    if not conf:
        raise Http404
    return conf


def _cell(obj, attr):
    value = getattr(obj, attr)
    return value() if callable(value) else value


@admin_required
def crud_list(request, kind):
    conf = _crud(kind)
    qs = conf["model"].objects.all()
    rows = [{"obj": o, "cells": [_cell(o, a) for _, a in conf["columns"]]} for o in qs[:500]]
    return render(request, "adminpanel/crud_list.html", _ctx(conf["active"], conf=conf, kind=kind, rows=rows))


@admin_required
def crud_edit(request, kind, pk=None):
    conf = _crud(kind)
    obj = get_object_or_404(conf["model"], pk=pk) if pk else None
    form = conf["form"](request.POST or None, request.FILES or None, instance=obj)
    if request.method == "POST" and form.is_valid():
        saved = form.save()
        log_action(request, f"{kind}.{'updated' if pk else 'created'}", saved)
        messages.success(request, f"{conf['singular'].capitalize()} saved.")
        return redirect("adminpanel:crud_list", kind=kind)
    return render(request, "adminpanel/crud_form.html", _ctx(conf["active"], conf=conf, kind=kind, form=form, obj=obj))


@admin_required
@require_POST
def crud_delete(request, kind, pk):
    conf = _crud(kind)
    if conf.get("no_delete"):
        messages.error(request, "Deactivate this item instead of deleting it, so existing records stay intact.")
        return redirect("adminpanel:crud_list", kind=kind)
    obj = get_object_or_404(conf["model"], pk=pk)
    log_action(request, f"{kind}.deleted", obj)
    obj.delete()
    messages.success(request, f"{conf['singular'].capitalize()} deleted.")
    return redirect("adminpanel:crud_list", kind=kind)


# ---------------------------------------------------------------------------
# Settings, audit logs, messages, notices
# ---------------------------------------------------------------------------
@admin_required
def settings_view(request):
    site = PlatformSetting.load()
    site = PlatformSetting.objects.get(pk=site.pk)
    form = f.PlatformSettingForm(request.POST or None, request.FILES or None, instance=site)
    if request.method == "POST" and form.is_valid():
        changed = form.changed_data
        form.save()
        log_action(request, "settings.updated", site, fields=changed)
        messages.success(request, "Settings saved.")
        return redirect("adminpanel:settings")
    return render(request, "adminpanel/settings.html", _ctx("settings", form=form, integrations=integration_status()))


@admin_required
def audit_logs(request):
    qs = AuditLog.objects.select_related("actor")
    action = request.GET.get("action", "").strip()
    if action:
        qs = qs.filter(action__startswith=action)
    actor = request.GET.get("actor", "").strip()
    if actor:
        qs = qs.filter(actor__email__icontains=actor)
    target = request.GET.get("target", "").strip()
    if target:
        qs = qs.filter(Q(target_repr__icontains=target) | Q(target_id=target))
    return render(request, "adminpanel/audit_logs.html", _ctx(
        "audit", page_obj=_page(request, qs, 50), action=action, actor=actor, target=target, querystring=_qs(request),
        action_prefixes=["listing", "user", "verification", "payment", "subscription", "report", "settings", "property", "account"],
    ))


@admin_required
def contact_messages(request):
    if request.method == "POST":
        msg = get_object_or_404(ContactMessage, pk=request.POST.get("id"))
        msg.is_resolved = not msg.is_resolved
        msg.save(update_fields=["is_resolved", "updated_at"])
        return redirect("adminpanel:messages")
    qs = ContactMessage.objects.all()
    if request.GET.get("show") != "all":
        qs = qs.filter(is_resolved=False)
    return render(request, "adminpanel/messages.html", _ctx("messages", page_obj=_page(request, qs), show=request.GET.get("show")))


@admin_required
def broadcast(request):
    form = f.BroadcastForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        audience = form.cleaned_data["audience"]
        qs = User.objects.filter(is_active=True).exclude(role=Role.ADMIN)
        if audience == "partners":
            qs = qs.filter(role__in=[Role.OWNER, Role.BROKER])
        elif audience in Role.values:
            qs = qs.filter(role=audience)
        count = 0
        for member in qs.iterator():
            if form.cleaned_data["send_external"]:
                notify(member, Notification.Event.ADMIN_NOTICE, form.cleaned_data["title"], form.cleaned_data["body"])
            else:
                Notification.objects.create(recipient=member, event=Notification.Event.ADMIN_NOTICE,
                                            title=form.cleaned_data["title"], body=form.cleaned_data["body"])
            count += 1
        log_action(request, "notice.broadcast", None, audience=audience, recipients=count, title=form.cleaned_data["title"])
        messages.success(request, f"Notice sent to {count} member(s).")
        return redirect("adminpanel:broadcast")
    return render(request, "adminpanel/broadcast.html", _ctx("broadcast", form=form))
