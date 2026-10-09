"""Owner / broker dashboard (/partner/). Every query is scoped to request.user."""
import csv
from datetime import timedelta

from django.contrib import messages
from django.core.files.base import ContentFile
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Prefetch, Q, Sum
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.forms import AccountForm, BrokerProfileForm, OwnerProfileForm, ProfileForm, VerificationDocumentForm
from accounts.models import BrokerProfile, OwnerProfile, Role, UserProfile, VerificationStatus
from core import ratelimit
from core.audit import log_action
from core.models import Advertisement, PlatformSetting
from core.permissions import lister_required, partner_required
from enquiries.forms import EnquiryStatusForm, PartnerNoteForm, ScheduleVisitForm
from enquiries.models import Enquiry, EnquiryStatusChange, PropertyVisit
from enquiries.services import EnquiryError, cancel_visit, change_enquiry_status, complete_visit, schedule_visit
from notifications.models import Notification
from notifications.services import notify_admins
from payments.models import Payment
from properties.forms import (
    STEP_FORMS,
    WIZARD_STEPS,
    BasicsForm,
    ImageUploadForm,
    MediaForm,
    SubmitListingForm,
    listing_completeness,
)
from properties.models import Property, PropertyDailyStat, PropertyImage
from properties.services import (
    ClosingError, add_images, mark_closed, record_changes, remove_image, reopen, set_primary_image, snapshot,
)
from subscriptions import services as subs
from subscriptions.models import SubscriptionPlan

BASE = "dashboard/partner_base.html"


def _ctx(active, **extra):
    extra.update({"active": active, "base_template": BASE})
    return extra


def _own_property(user, pk, editable=False):
    prop = get_object_or_404(Property.objects.exclude(status=Property.Status.DELETED), pk=pk, owner=user)
    if editable and not prop.can_edit:
        raise Http404("This listing can no longer be edited.")
    return prop


# ---------------------------------------------------------------------------
# Overview
# ---------------------------------------------------------------------------
@partner_required
def overview(request):
    user = request.user
    props = Property.objects.owned_by(user)
    now = timezone.now()
    counts = props.aggregate(
        active=Count("pk", filter=Q(status=Property.Status.ACTIVE, expires_at__gt=now)),
        pending=Count("pk", filter=Q(status=Property.Status.PENDING)),
        rejected=Count("pk", filter=Q(status__in=[Property.Status.REJECTED, Property.Status.CHANGES_REQUESTED])),
        expiring=Count("pk", filter=Q(status=Property.Status.ACTIVE, expires_at__gt=now, expires_at__lte=now + timedelta(days=7))),
        drafts=Count("pk", filter=Q(status=Property.Status.DRAFT)),
        views=Sum("view_count"),
    )
    enquiries = Enquiry.objects.filter(partner=user)
    recent_enquiries = enquiries.select_related("property").order_by("-created_at")[:5]
    recent_reviews = (
        Property.objects.owned_by(user).exclude(moderation_note="")
        .filter(status__in=[Property.Status.REJECTED, Property.Status.CHANGES_REQUESTED])[:3]
    )
    plan = subs.current_plan(user)
    return render(request, "dashboard/partner/overview.html", _ctx(
        "overview",
        counts=counts,
        total_enquiries=enquiries.count(),
        new_enquiries=enquiries.filter(status=Enquiry.Status.NEW).count(),
        visit_requests=PropertyVisit.objects.filter(partner=user, status=PropertyVisit.Status.REQUESTED).count(),
        subscription=subs.current_subscription(user),
        plan=plan,
        used_slots=subs.used_listing_slots(user),
        recent_enquiries=recent_enquiries,
        recent_reviews=recent_reviews,
        expiring_list=props.filter(status=Property.Status.ACTIVE, expires_at__gt=now, expires_at__lte=now + timedelta(days=7))[:5],
        verification_status=user.verification_status,
    ))


# ---------------------------------------------------------------------------
# Listings
# ---------------------------------------------------------------------------
@lister_required
def property_list(request):
    qs = (
        Property.objects.owned_by(request.user)
        .select_related("category", "town", "area")
        .prefetch_related(Prefetch("images", queryset=PropertyImage.objects.filter(is_primary=True)))
        .annotate(fav_count=Count("favourited_by", distinct=True))
        .order_by("-updated_at")
    )
    status = request.GET.get("status")
    groups = {
        "active": [Property.Status.ACTIVE],
        "pending": [Property.Status.PENDING],
        "attention": [Property.Status.REJECTED, Property.Status.CHANGES_REQUESTED, Property.Status.EXPIRED],
        "draft": [Property.Status.DRAFT],
        "paused": [Property.Status.PAUSED],
        "closed": [Property.Status.RENTED, Property.Status.SOLD, Property.Status.REMOVED],
    }
    if status in groups:
        qs = qs.filter(status__in=groups[status])
    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(Q(title__icontains=q) | Q(reference__iexact=q.upper()))
    page_obj = Paginator(qs, 15).get_page(request.GET.get("page"))
    return render(request, "dashboard/partner/properties.html", _ctx(
        "properties", page_obj=page_obj, status=status, q=q,
        used_slots=subs.used_listing_slots(request.user), limit=subs.listing_limit(request.user),
        querystring="&".join(p for p in [f"status={status}" if status else "", f"q={q}" if q else ""] if p),
    ))


def _wizard_context(prop, step, **extra):
    done = prop.wizard_step if prop and prop.pk else 1
    ctx = {"steps": WIZARD_STEPS, "step": step, "prop": prop, "done_step": done,
           "step_title": dict((s, t) for s, t, _ in WIZARD_STEPS)[step],
           "is_live": prop is not None and prop.pk and prop.status in (Property.Status.ACTIVE, Property.Status.PAUSED)}
    ctx.update(extra)
    return _ctx("add" if not prop or prop.status == Property.Status.DRAFT else "properties", **ctx)


@lister_required
def property_add(request):
    user = request.user
    form = BasicsForm(request.POST or None, initial={"purpose": request.GET.get("purpose", "rent")})
    if request.method == "POST" and form.is_valid():
        prop = form.save(commit=False)
        prop.owner = user
        prop.status = Property.Status.DRAFT
        prop.wizard_step = 2
        site = PlatformSetting.load()
        prop.state, prop.district = site.default_state, site.default_district
        prop.town = site.default_town
        profile = getattr(user, "profile", None)
        prop.contact_name = user.display_name
        prop.contact_phone = user.phone
        prop.whatsapp_number = profile.whatsapp_number if profile else ""
        prop.save()
        log_action(request, "property.draft_created", prop)
        messages.success(request, "Draft saved. Next, add the location.")
        return redirect("dashboard:partner_property_step", pk=prop.pk, step=2)
    return render(request, "dashboard/partner/wizard.html", _wizard_context(
        None, 1, form=form, slots_ok=subs.can_occupy_slot(user),
        used_slots=subs.used_listing_slots(user), limit=subs.listing_limit(user),
    ))


@lister_required
def property_step(request, pk, step):
    if step not in range(1, 8):
        raise Http404
    prop = _own_property(request.user, pk, editable=True)
    if step == 5:
        return _photos_step(request, prop)
    if step == 7:
        return _review_step(request, prop)
    form_class = STEP_FORMS[step]
    form = form_class(request.POST or None, instance=prop)
    if request.method == "POST" and form.is_valid():
        before = snapshot(Property.objects.get(pk=prop.pk))  # instance is already mutated by is_valid()
        old_amenities = sorted(prop.amenities.values_list("pk", flat=True)) if step == 3 else None
        with transaction.atomic():
            obj = form.save(commit=False)
            obj.wizard_step = max(prop.wizard_step, step + 1)
            obj.save()
            form.save_m2m()
            moved = False
            if obj.status != Property.Status.DRAFT:
                extra = None
                if step == 3:
                    new_amenities = sorted(obj.amenities.values_list("pk", flat=True))
                    if new_amenities != old_amenities:
                        extra = {"amenities": [old_amenities, new_amenities]}
                moved = record_changes(obj, before, request.user, extra_changes=extra, extra_moderated=False)
        if moved:
            messages.warning(request, "Your changes will be reviewed by our team before they go live. The listing is hidden until approved.")
            notify_admins(Notification.Event.ADMIN_NOTICE, f"Listing edited: {obj.reference}", f"{obj.title} was edited and needs review.",
                          reverse("adminpanel:property_review", args=[obj.pk]))
        else:
            messages.success(request, "Saved.")
        if request.POST.get("save_and_exit"):
            return redirect("dashboard:partner_properties")
        if request.POST.get("next_step") or obj.status == Property.Status.DRAFT:
            return redirect("dashboard:partner_property_step", pk=prop.pk, step=step + 1)
        return redirect("dashboard:partner_property_step", pk=prop.pk, step=step)
    return render(request, "dashboard/partner/wizard.html", _wizard_context(prop, step, form=form))


def _photos_step(request, prop):
    upload_form = ImageUploadForm(property_obj=prop)
    media_form = MediaForm(instance=prop)
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "upload":
            if not ratelimit.check_and_hit("upload", f"user:{request.user.pk}"):
                messages.error(request, "Upload limit reached. Please try again later.")
                return redirect("dashboard:partner_property_step", pk=prop.pk, step=5)
            upload_form = ImageUploadForm(request.POST, request.FILES, property_obj=prop)
            if upload_form.is_valid():
                files = upload_form.cleaned_data["images"]
                if not files:
                    messages.error(request, "Choose at least one photo to upload.")
                else:
                    try:
                        created = add_images(prop, files)
                    except Exception:  # corrupted image that passed verification
                        messages.error(request, "One of the images could not be processed. Please try a different file.")
                        return redirect("dashboard:partner_property_step", pk=prop.pk, step=5)
                    if prop.status != Property.Status.DRAFT:
                        moved = record_changes(prop, snapshot(prop), request.user,
                                               extra_changes={"images": [None, f"{len(created)} photo(s) added"]})
                        if moved:
                            messages.warning(request, "New photos will be reviewed before the listing goes live again.")
                    messages.success(request, f"{len(created)} photo(s) uploaded.")
                    return redirect("dashboard:partner_property_step", pk=prop.pk, step=5)
        elif action == "save":
            media_form = MediaForm(request.POST, instance=prop)
            if media_form.is_valid():
                if not prop.images.exists():
                    messages.error(request, "Add at least one photo before continuing.")
                else:
                    before = snapshot(Property.objects.get(pk=prop.pk))  # instance is already mutated by is_valid()
                    obj = media_form.save(commit=False)
                    obj.wizard_step = max(prop.wizard_step, 6)
                    obj.save()
                    if prop.status != Property.Status.DRAFT and record_changes(obj, before, request.user):
                        messages.warning(request, "Your changes will be reviewed before they go live.")
                    return redirect("dashboard:partner_property_step", pk=prop.pk, step=6)
    images = prop.images.all()
    return render(request, "dashboard/partner/wizard.html", _wizard_context(
        prop, 5, upload_form=upload_form, media_form=media_form, images=images,
    ))


@lister_required
@require_POST
def image_action(request, pk, image_pk, action):
    prop = _own_property(request.user, pk, editable=True)
    if not prop.images.filter(pk=image_pk).exists():
        raise Http404
    if action == "primary":
        set_primary_image(prop, image_pk)
        messages.success(request, "Cover photo updated.")
    elif action == "delete":
        if prop.status in (Property.Status.ACTIVE, Property.Status.PENDING) and prop.images.count() <= 1:
            messages.error(request, "A live listing must keep at least one photo. Upload another photo first.")
        else:
            remove_image(prop, image_pk)
            messages.success(request, "Photo removed.")
    else:
        raise Http404
    return redirect("dashboard:partner_property_step", pk=prop.pk, step=5)


def _review_step(request, prop):
    problems = listing_completeness(prop)
    form = SubmitListingForm(request.POST or None)
    submittable = prop.status in (Property.Status.DRAFT, Property.Status.CHANGES_REQUESTED, Property.Status.REJECTED)
    slots_ok = subs.can_occupy_slot(request.user, prop)
    if request.method == "POST" and submittable:
        if problems:
            messages.error(request, "Please complete the missing details before submitting.")
        elif not slots_ok:
            messages.error(request, "You've reached the active listing limit of your plan. Upgrade your plan or close another listing.")
        elif form.is_valid():
            now = timezone.now()
            with transaction.atomic():
                locked = Property.objects.select_for_update().get(pk=prop.pk)
                if locked.status not in (Property.Status.DRAFT, Property.Status.CHANGES_REQUESTED, Property.Status.REJECTED):
                    return redirect("dashboard:partner_properties")
                locked.policy_accepted_at = now
                locked.submitted_at = now
                if PlatformSetting.load().require_listing_approval and not request.user.is_platform_admin:
                    locked.status = Property.Status.PENDING
                else:
                    duration, priority = subs.listing_terms(request.user)
                    locked.activate(duration, priority)
                locked.save()
            log_action(request, "property.submitted", locked)
            if locked.status == Property.Status.PENDING:
                notify_admins(Notification.Event.ADMIN_NOTICE, f"New listing to review: {locked.reference}", locked.title,
                              reverse("adminpanel:property_review", args=[locked.pk]))
                messages.success(request, "Submitted for review. We'll notify you once it's approved (usually within 24 hours).")
            else:
                messages.success(request, "Your listing is now live.")
            return redirect("dashboard:partner_properties")
    return render(request, "dashboard/partner/wizard.html", _wizard_context(
        prop, 7, form=form, problems=problems, submittable=submittable, slots_ok=slots_ok,
        images=prop.images.all(), amenities=prop.amenities.all(), site_policy=PlatformSetting.load().listing_policy,
    ))


@lister_required
@require_POST
def property_action(request, pk, action):
    prop = _own_property(request.user, pk)
    user = request.user
    S = Property.Status
    if action == "pause" and prop.can_pause:
        prop.status = S.PAUSED
        prop.save(update_fields=["status", "updated_at"])
        messages.success(request, "Listing paused. It is hidden from search until you resume it.")
    elif action == "resume" and prop.can_resume:
        if prop.expires_at and prop.expires_at <= timezone.now():
            prop.status = S.EXPIRED
            messages.warning(request, "This listing has expired. Renew it to make it live again.")
        else:
            prop.status = S.ACTIVE
            messages.success(request, "Listing is live again.")
        prop.save(update_fields=["status", "updated_at"])
    elif action in ("close", "rented", "sold") and prop.can_close:
        try:
            prop = mark_closed(prop, user, request)
        except ClosingError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, f"Marked as {prop.get_status_display().lower()}. Congratulations! "
                                      "It's off the market and open enquiries are closed.")
    elif action == "reopen":
        try:
            prop = reopen(prop, user, request)
        except ClosingError as exc:
            messages.error(request, str(exc))
        else:
            if prop.status == S.ACTIVE:
                messages.success(request, "The listing is available and live again.")
            else:
                messages.warning(request, "The listing is available again but has expired. Renew it to make it live.")
    elif action == "renew" and prop.can_renew:
        if not subs.can_occupy_slot(user, prop):
            messages.error(request, "You've reached your plan's active listing limit. Upgrade your plan to renew this listing.")
        else:
            duration, priority = subs.listing_terms(user)
            prop.activate(duration, priority)
            prop.save()
            messages.success(request, f"Listing renewed until {timezone.localtime(prop.expires_at):%d %b %Y}.")
    elif action == "duplicate":
        new = _duplicate(prop)
        messages.success(request, f"Created a draft copy ({new.reference}). Review and submit it when ready.")
        return redirect("dashboard:partner_property_step", pk=new.pk, step=1)
    elif action == "delete" and prop.can_delete:
        if request.POST.get("confirm_reference", "").strip().upper() != prop.reference:
            messages.error(request, f"Type the listing ID {prop.reference} to confirm deletion.")
            return redirect("dashboard:partner_property_manage", pk=prop.pk)
        _close_open_enquiries(prop, user, "The listing was withdrawn by the owner.")
        prop.status = S.DELETED
        prop.closed_at = timezone.now()
        prop.save(update_fields=["status", "closed_at", "updated_at"])
        Advertisement.objects.filter(property=prop).update(is_active=False)
        log_action(request, "property.deleted", prop)
        messages.success(request, "Listing deleted.")
    else:
        messages.error(request, "That action isn't available for this listing right now.")
    log_action(request, f"property.{action}", prop)
    return redirect(request.POST.get("return_to") == "manage" and reverse("dashboard:partner_property_manage", args=[prop.pk])
                    or reverse("dashboard:partner_properties"))


def _close_open_enquiries(prop, user, reason):
    for enquiry in prop.enquiries.filter(status__in=Enquiry.OPEN_STATUSES):
        try:
            change_enquiry_status(enquiry, Enquiry.Status.CLOSED, user, reason)
        except EnquiryError:
            continue


def _duplicate(prop):
    amenities = list(prop.amenities.all())
    images = list(prop.images.all())
    skip = {"id", "reference", "slug", "status", "published_at", "expires_at", "submitted_at", "policy_accepted_at",
            "view_count", "enquiry_count", "featured_until", "priority", "moderation_note", "is_suspicious",
            "expiry_reminder_sent_at", "closed_at", "created_at", "updated_at", "is_demo", "wizard_step"}
    data = {f.attname: getattr(prop, f.attname) for f in Property._meta.concrete_fields if f.name not in skip and f.attname not in skip}
    data["title"] = f"{prop.title} (copy)"[:120]
    with transaction.atomic():
        new = Property.objects.create(**data, status=Property.Status.DRAFT, wizard_step=7)
        new.amenities.set(amenities)
        for img in images:
            clone = PropertyImage(property=new, is_primary=img.is_primary, order=img.order, caption=img.caption)
            with img.image.open("rb") as fh:
                clone.image.save(img.image.name.rsplit("/", 1)[-1], ContentFile(fh.read()), save=False)
            if img.thumbnail:
                with img.thumbnail.open("rb") as fh:
                    clone.thumbnail.save(img.thumbnail.name.rsplit("/", 1)[-1], ContentFile(fh.read()), save=False)
            clone.save()
    return new


@lister_required
def property_manage(request, pk):
    prop = _own_property(request.user, pk)
    plan = subs.current_plan(request.user)
    since = timezone.localdate() - timedelta(days=29)
    stats = {s.date: s.views for s in PropertyDailyStat.objects.filter(property=prop, date__gte=since)}
    days = [since + timedelta(days=i) for i in range(30)]
    chart = {"labels": [d.strftime("%d %b") for d in days], "views": [stats.get(d, 0) for d in days]}
    return render(request, "dashboard/partner/property_manage.html", _ctx(
        "properties", prop=prop, plan=plan, chart=chart,
        favourites=prop.favourited_by.count(),
        visits=prop.visits.count(),
        enquiries=prop.enquiries.select_related("customer").order_by("-created_at")[:10],
        reviews=prop.reviews.select_related("reviewer")[:10],
        revisions=prop.revisions.all()[:10],
    ))


# ---------------------------------------------------------------------------
# Enquiries & visits
# ---------------------------------------------------------------------------
@lister_required
def enquiry_list(request):
    qs = Enquiry.objects.filter(partner=request.user).select_related("property", "customer").order_by("-created_at")
    status = request.GET.get("status")
    if status == "open":
        qs = qs.filter(status__in=Enquiry.OPEN_STATUSES)
    elif status in Enquiry.Status.values:
        qs = qs.filter(status=status)
    prop_id = request.GET.get("property")
    if prop_id and prop_id.isdigit():
        qs = qs.filter(property_id=int(prop_id))
    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(Q(contact_name__icontains=q) | Q(contact_phone__icontains=q) | Q(property__reference__iexact=q.upper()))
    plan = subs.current_plan(request.user)
    if request.GET.get("export") == "csv":
        if not (plan and plan.has_advanced_enquiry_tools):
            messages.error(request, "CSV export is available on the Broker plan.")
            return redirect("dashboard:partner_enquiries")
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = 'attachment; filename="enquiries.csv"'
        writer = csv.writer(response)
        writer.writerow(["Enquiry ID", "Date", "Property", "Customer", "Phone", "Email", "Preferred contact", "Status", "Message"])
        for e in qs[:5000]:
            writer.writerow([e.pk, timezone.localtime(e.created_at).strftime("%Y-%m-%d %H:%M"), e.property.reference,
                             _csv_safe(e.contact_name), _csv_safe(e.contact_phone), _csv_safe(e.contact_email),
                             e.get_preferred_contact_display(), e.get_status_display(), _csv_safe(e.message)])
        log_action(request, "enquiries.exported", None, count=qs.count())
        return response
    page_obj = Paginator(qs, 15).get_page(request.GET.get("page"))
    params = request.GET.copy()
    params.pop("page", None)
    return render(request, "dashboard/partner/enquiries.html", _ctx(
        "enquiries", page_obj=page_obj, status=status, statuses=Enquiry.Status.choices, q=q, plan=plan,
        properties=Property.objects.owned_by(request.user).only("pk", "reference", "title"), property_id=prop_id,
        querystring=params.urlencode(),
    ))


def _csv_safe(value):
    """Neutralise spreadsheet formula injection."""
    value = str(value or "")
    return "'" + value if value[:1] in ("=", "+", "-", "@") else value


@lister_required
def enquiry_detail(request, pk):
    enquiry = get_object_or_404(Enquiry.objects.select_related("property", "customer"), pk=pk, partner=request.user)
    status_form = EnquiryStatusForm(prefix="status")
    note_form = PartnerNoteForm(instance=enquiry, prefix="note")
    if request.method == "POST":
        if "status-status" in request.POST:
            status_form = EnquiryStatusForm(request.POST, prefix="status")
            if status_form.is_valid():
                try:
                    change_enquiry_status(enquiry, status_form.cleaned_data["status"], request.user, status_form.cleaned_data["note"])
                    messages.success(request, "Enquiry status updated and the customer has been notified.")
                    return redirect("dashboard:partner_enquiry_detail", pk=enquiry.pk)
                except EnquiryError as exc:
                    messages.error(request, str(exc))
        else:
            note_form = PartnerNoteForm(request.POST, instance=enquiry, prefix="note")
            if note_form.is_valid():
                note_form.save()
                messages.success(request, "Notes saved.")
                return redirect("dashboard:partner_enquiry_detail", pk=enquiry.pk)
    return render(request, "dashboard/partner/enquiry_detail.html", _ctx(
        "enquiries", enquiry=enquiry, status_form=status_form, note_form=note_form,
        history=EnquiryStatusChange.objects.filter(enquiry=enquiry).select_related("changed_by"),
        visits=enquiry.visits.all(), schedule_form=ScheduleVisitForm(prefix="visit"),
    ))


@lister_required
def visit_list(request):
    qs = PropertyVisit.objects.filter(partner=request.user).select_related("property", "customer", "enquiry").order_by("preferred_date")
    active = [v for v in qs if v.is_active]
    past = list(qs.exclude(status__in=["requested", "scheduled"]).order_by("-updated_at")[:30])
    return render(request, "dashboard/partner/visits.html", _ctx(
        "visits", requested=[v for v in active if v.status == "requested"],
        scheduled=sorted([v for v in active if v.status == "scheduled"], key=lambda v: v.scheduled_at or timezone.now()),
        past=past, schedule_form=ScheduleVisitForm(prefix="visit"),
    ))


@lister_required
@require_POST
def visit_action(request, pk, action):
    visit = get_object_or_404(PropertyVisit.objects.select_related("property", "enquiry", "customer"), pk=pk, partner=request.user)
    try:
        if action == "schedule":
            form = ScheduleVisitForm(request.POST, prefix="visit")
            if not form.is_valid():
                messages.error(request, "Choose a valid date and time for the visit.")
                return redirect("dashboard:partner_visits")
            schedule_visit(visit, form.cleaned_data["scheduled_at"], request.user, form.cleaned_data.get("partner_note", ""))
            messages.success(request, "Visit scheduled and the customer has been notified.")
        elif action == "complete":
            complete_visit(visit, request.user)
            messages.success(request, "Visit marked as completed.")
        elif action == "no_show":
            complete_visit(visit, request.user, no_show=True)
            messages.success(request, "Visit marked as no-show.")
        elif action == "cancel":
            cancel_visit(visit, request.user, request.POST.get("reason", "")[:200])
            messages.success(request, "Visit cancelled and the customer has been notified.")
        else:
            raise Http404
    except EnquiryError as exc:
        messages.error(request, str(exc))
    return redirect(request.POST.get("return_to") == "enquiry" and reverse("dashboard:partner_enquiry_detail", args=[visit.enquiry_id])
                    or reverse("dashboard:partner_visits"))


# ---------------------------------------------------------------------------
# Subscription & payments
# ---------------------------------------------------------------------------
@partner_required
def subscription(request):
    user = request.user
    plans = [p for p in SubscriptionPlan.objects.filter(is_active=True, unlimited_contacts=False) if p.available_for(user)]
    return render(request, "dashboard/partner/subscription.html", _ctx(
        "subscription", plans=plans, current=subs.current_subscription(user), plan=subs.current_plan(user),
        used_slots=subs.used_listing_slots(user), history=user.subscriptions.select_related("plan")[:10],
        site=PlatformSetting.load(),
    ))


@partner_required
def payment_list(request):
    payments = Payment.objects.filter(user=request.user).select_related("plan").prefetch_related("invoice")
    page_obj = Paginator(payments, 15).get_page(request.GET.get("page"))
    return render(request, "dashboard/partner/payments.html", _ctx("payments", page_obj=page_obj))


# ---------------------------------------------------------------------------
# Profile & verification
# ---------------------------------------------------------------------------
@partner_required
def profile(request):
    user = request.user
    prof, _ = UserProfile.objects.get_or_create(user=user)
    if user.role == Role.OWNER:
        partner_profile, _ = OwnerProfile.objects.get_or_create(user=user)
        business_form_class = OwnerProfileForm
    else:
        partner_profile, _ = BrokerProfile.objects.get_or_create(user=user, defaults={"agency_name": user.full_name})
        business_form_class = BrokerProfileForm
    section = request.POST.get("section")
    account_form = AccountForm(request.POST if section == "profile" else None, instance=user, prefix="account")
    profile_form = ProfileForm(request.POST if section == "profile" else None, request.FILES if section == "profile" else None,
                               instance=prof, prefix="profile")
    business_form = business_form_class(request.POST if section == "profile" else None, instance=partner_profile, prefix="biz")
    doc_form = VerificationDocumentForm(request.POST if section == "verification" else None,
                                        request.FILES if section == "verification" else None, user=user, prefix="doc")
    if request.method == "POST":
        if section == "profile" and account_form.is_valid() and profile_form.is_valid() and business_form.is_valid():
            account_form.save()
            profile_form.save()
            business_form.save()
            log_action(request, "account.profile_updated", user)
            messages.success(request, "Profile updated.")
            return redirect("dashboard:partner_profile")
        if section == "verification" and doc_form.is_valid():
            if not ratelimit.check_and_hit("upload", f"user:{user.pk}"):
                messages.error(request, "Upload limit reached. Please try again later.")
                return redirect("dashboard:partner_profile")
            if user.verification_documents.filter(status="pending").count() >= 6:
                messages.error(request, "You have several documents awaiting review. Please wait for the review to finish.")
                return redirect("dashboard:partner_profile")
            doc = doc_form.save(commit=False)
            doc.user = user
            doc.original_name = (request.FILES["doc-file"].name or "")[:255]
            doc.save()
            if partner_profile.verification_status != VerificationStatus.VERIFIED:
                partner_profile.verification_status = VerificationStatus.PENDING
                partner_profile.submitted_at = timezone.now()
                partner_profile.save(update_fields=["verification_status", "submitted_at", "updated_at"])
            log_action(request, "verification.document_uploaded", doc, doc_type=doc.doc_type)
            notify_admins(Notification.Event.ADMIN_NOTICE, "Verification document submitted",
                          f"{user.display_name} uploaded a {doc.get_doc_type_display()}.", reverse("adminpanel:verifications"))
            messages.success(request, "Document uploaded. Our team will review it shortly.")
            return redirect("dashboard:partner_profile")
    return render(request, "dashboard/partner/profile.html", _ctx(
        "profile", account_form=account_form, profile_form=profile_form, business_form=business_form,
        doc_form=doc_form, partner_profile=partner_profile, documents=user.verification_documents.all(),
    ))


@partner_required
@require_POST
def document_delete(request, pk):
    doc = get_object_or_404(user_docs(request.user), pk=pk)
    if doc.status != "pending":
        messages.error(request, "Only documents awaiting review can be withdrawn.")
    else:
        doc.file.delete(save=False)
        doc.delete()
        messages.success(request, "Document withdrawn and deleted.")
    return redirect("dashboard:partner_profile")


def user_docs(user):
    return user.verification_documents.all()
