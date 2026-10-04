"""Enquiry and visit workflow rules."""
from django.db import IntegrityError, transaction
from django.db.models import F
from django.urls import reverse
from django.utils import timezone

from enquiries.models import Enquiry, EnquiryStatusChange, PropertyVisit
from notifications.models import Notification
from notifications.services import notify
from properties.models import Property


class EnquiryError(Exception):
    pass


def _set_status(enquiry, status, user, note=""):
    if enquiry.status == status:
        return
    EnquiryStatusChange.objects.create(enquiry=enquiry, from_status=enquiry.status, to_status=status, changed_by=user, note=note[:255])
    enquiry.status = status
    enquiry.last_status_change_at = timezone.now()
    enquiry.save(update_fields=["status", "last_status_change_at", "updated_at"])


def submit_enquiry(user, prop, data):
    """Create an enquiry (and optional visit request). Returns (enquiry, created_visit).

    Duplicate protection: a customer can have only one open enquiry per property
    (also enforced by a database constraint).
    """
    if prop.owner_id == user.pk:
        raise EnquiryError("You cannot enquire about your own listing.")
    if not prop.is_public:
        raise EnquiryError("This property is not accepting enquiries.")
    with transaction.atomic():
        existing = Enquiry.objects.select_for_update().filter(
            customer=user, property=prop, status__in=Enquiry.OPEN_STATUSES
        ).first()
        if existing:
            if data.get("request_visit") and not existing.visits.filter(status__in=["requested", "scheduled"]).exists():
                visit = _create_visit(existing, data)
                return existing, visit
            raise EnquiryError("You already have an open enquiry for this property. Track it in your dashboard.")
        try:
            with transaction.atomic():
                enquiry = Enquiry.objects.create(
                    property=prop, customer=user, partner=prop.owner,
                    message=data["message"], contact_name=data["contact_name"], contact_phone=data["contact_phone"],
                    contact_email=data.get("contact_email") or "", preferred_contact=data["preferred_contact"],
                )
        except IntegrityError:
            raise EnquiryError("You already have an open enquiry for this property.")
        EnquiryStatusChange.objects.create(enquiry=enquiry, to_status=Enquiry.Status.NEW, changed_by=user)
        Property.objects.filter(pk=prop.pk).update(enquiry_count=F("enquiry_count") + 1)
        visit = _create_visit(enquiry, data) if data.get("request_visit") else None
        if not visit:
            notify(
                prop.owner, Notification.Event.NEW_ENQUIRY, f"New enquiry for {prop.reference}",
                f"{enquiry.contact_name} sent an enquiry about \"{prop.title}\": {enquiry.message[:300]}",
                reverse("dashboard:partner_enquiry_detail", args=[enquiry.pk]),
            )
    return enquiry, visit


def _create_visit(enquiry, data):
    prop = enquiry.property
    try:
        with transaction.atomic():
            visit = PropertyVisit.objects.create(
                enquiry=enquiry, property=prop, customer=enquiry.customer, partner=enquiry.partner,
                preferred_date=data["preferred_date"], preferred_slot=data["preferred_slot"],
                customer_note=data.get("visit_note") or "",
            )
    except IntegrityError:
        raise EnquiryError("You already have an active visit request for this property.")
    _set_status(enquiry, Enquiry.Status.VISIT_REQUESTED, enquiry.customer)
    notify(
        enquiry.partner, Notification.Event.VISIT_REQUEST, f"Visit request for {prop.reference}",
        f"{enquiry.contact_name} would like to visit \"{prop.title}\" on {visit.preferred_date:%d %b %Y} "
        f"({visit.get_preferred_slot_display()}).",
        reverse("dashboard:partner_visits"),
    )
    return visit


def change_enquiry_status(enquiry, status, user, note=""):
    if status not in Enquiry.PARTNER_SETTABLE:
        raise EnquiryError("This status cannot be set manually.")
    if not enquiry.is_open and status != Enquiry.Status.CLOSED:
        raise EnquiryError("This enquiry is already closed.")
    with transaction.atomic():
        _set_status(enquiry, status, user, note)
        if status in (Enquiry.Status.COMPLETED, Enquiry.Status.CLOSED):
            enquiry.visits.filter(status=PropertyVisit.Status.REQUESTED).update(status=PropertyVisit.Status.CANCELLED, cancelled_by=user)
        body = f"Your enquiry about \"{enquiry.property.title}\" is now: {Enquiry.Status(status).label}."
        if note:
            body += f" Message from the owner/broker: {note}"
        notify(enquiry.customer, Notification.Event.ENQUIRY_UPDATE, f"Enquiry update for {enquiry.property.reference}",
               body, reverse("dashboard:customer_enquiries"))


def schedule_visit(visit, scheduled_at, user, note=""):
    if visit.status not in (PropertyVisit.Status.REQUESTED, PropertyVisit.Status.SCHEDULED):
        raise EnquiryError("This visit can no longer be scheduled.")
    rescheduled = visit.status == PropertyVisit.Status.SCHEDULED
    with transaction.atomic():
        visit.status = PropertyVisit.Status.SCHEDULED
        visit.scheduled_at = scheduled_at
        visit.partner_note = note
        visit.save(update_fields=["status", "scheduled_at", "partner_note", "updated_at"])
        _set_status(visit.enquiry, visit.enquiry.Status.VISIT_SCHEDULED, user)
        when = timezone.localtime(scheduled_at).strftime("%d %b %Y, %I:%M %p")
        notify(
            visit.customer, Notification.Event.VISIT_CONFIRMED,
            f"Visit {'rescheduled' if rescheduled else 'confirmed'} for {visit.property.reference}",
            f"Your visit to \"{visit.property.title}\" is scheduled for {when}." + (f" Note: {note}" if note else ""),
            reverse("dashboard:customer_visits"),
        )


def complete_visit(visit, user, no_show=False):
    if visit.status != PropertyVisit.Status.SCHEDULED:
        raise EnquiryError("Only scheduled visits can be marked as completed.")
    with transaction.atomic():
        visit.status = PropertyVisit.Status.NO_SHOW if no_show else PropertyVisit.Status.COMPLETED
        visit.save(update_fields=["status", "updated_at"])
        if not no_show:
            _set_status(visit.enquiry, Enquiry.Status.COMPLETED, user)


def cancel_visit(visit, user, reason=""):
    is_customer = user.pk == visit.customer_id
    if is_customer and visit.status != PropertyVisit.Status.REQUESTED:
        raise EnquiryError("Only pending visit requests can be cancelled here. Contact the owner to cancel a scheduled visit.")
    if not visit.is_active:
        raise EnquiryError("This visit is not active.")
    with transaction.atomic():
        visit.status = PropertyVisit.Status.CANCELLED
        visit.cancelled_by = user
        visit.save(update_fields=["status", "cancelled_by", "updated_at"])
        enquiry = visit.enquiry
        if enquiry.status in (Enquiry.Status.VISIT_REQUESTED, Enquiry.Status.VISIT_SCHEDULED):
            _set_status(enquiry, Enquiry.Status.CONTACTED if not is_customer else Enquiry.Status.NEW, user, "Visit cancelled")
        recipient = visit.partner if is_customer else visit.customer
        link = reverse("dashboard:partner_visits") if is_customer else reverse("dashboard:customer_visits")
        notify(recipient, Notification.Event.VISIT_CANCELLED, f"Visit cancelled for {visit.property.reference}",
               f"The visit to \"{visit.property.title}\" was cancelled." + (f" Reason: {reason}" if reason else ""), link)


def cancel_enquiry(enquiry, user):
    if enquiry.customer_id != user.pk:
        raise EnquiryError("Not allowed.")
    if not enquiry.is_open:
        raise EnquiryError("This enquiry is already closed.")
    with transaction.atomic():
        enquiry.visits.filter(status__in=["requested", "scheduled"]).update(status=PropertyVisit.Status.CANCELLED, cancelled_by=user)
        _set_status(enquiry, Enquiry.Status.CANCELLED, user)
