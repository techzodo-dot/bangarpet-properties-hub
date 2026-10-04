"""Listing moderation and partner verification actions (admin only)."""
from datetime import timedelta

from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from accounts.models import VerificationDocument, VerificationStatus
from core.audit import log_action
from moderation.models import ListingReview
from notifications.models import Notification
from notifications.services import notify
from properties.models import Property
from subscriptions.services import listing_terms


class ModerationError(Exception):
    pass


def _review(prop, admin, decision, reason=""):
    return ListingReview.objects.create(property=prop, reviewer=admin, decision=decision, reason=reason)


@transaction.atomic
def approve_listing(request, prop):
    prop = Property.objects.select_for_update().get(pk=prop.pk)
    if prop.status != Property.Status.PENDING:
        raise ModerationError("Only listings pending approval can be approved.")
    duration, priority = listing_terms(prop.owner)
    first_publication = prop.published_at is None
    if first_publication or not prop.expires_at or prop.expires_at <= timezone.now():
        prop.activate(duration, priority)
    else:
        prop.status = Property.Status.ACTIVE  # re-approval after an edit keeps the original expiry
    prop.moderation_note = ""
    prop.save()
    _review(prop, request.user, ListingReview.Decision.APPROVED)
    prop.revisions.filter(requires_moderation=True).update(requires_moderation=False)
    log_action(request, "listing.approved", prop)
    notify(prop.owner, Notification.Event.LISTING_APPROVED, f"Listing approved: {prop.reference}",
           f"Your listing \"{prop.title}\" is now live until {timezone.localtime(prop.expires_at):%d %b %Y}.",
           prop.get_absolute_url())
    return prop


@transaction.atomic
def reject_listing(request, prop, reason, request_changes=False):
    if not reason.strip():
        raise ModerationError("A reason is required.")
    prop = Property.objects.select_for_update().get(pk=prop.pk)
    if prop.status not in (Property.Status.PENDING, Property.Status.ACTIVE, Property.Status.PAUSED):
        raise ModerationError("This listing cannot be rejected in its current state.")
    prop.status = Property.Status.CHANGES_REQUESTED if request_changes else Property.Status.REJECTED
    prop.moderation_note = reason.strip()
    prop.save(update_fields=["status", "moderation_note", "updated_at"])
    decision = ListingReview.Decision.CHANGES_REQUESTED if request_changes else ListingReview.Decision.REJECTED
    _review(prop, request.user, decision, reason)
    log_action(request, f"listing.{decision}", prop, reason=reason)
    event = Notification.Event.LISTING_CHANGES if request_changes else Notification.Event.LISTING_REJECTED
    title = f"Changes requested: {prop.reference}" if request_changes else f"Listing not approved: {prop.reference}"
    notify(prop.owner, event, title, f"\"{prop.title}\": {reason}",
           reverse("dashboard:partner_property_manage", args=[prop.pk]))
    return prop


@transaction.atomic
def remove_listing(request, prop, reason):
    if not reason.strip():
        raise ModerationError("A reason is required.")
    prop = Property.objects.select_for_update().get(pk=prop.pk)
    if prop.status in (Property.Status.REMOVED, Property.Status.DELETED):
        raise ModerationError("This listing has already been removed.")
    prop.status = Property.Status.REMOVED
    prop.moderation_note = reason.strip()
    prop.featured_until = None
    prop.save(update_fields=["status", "moderation_note", "featured_until", "updated_at"])
    prop.advertisements.update(is_active=False)
    _review(prop, request.user, ListingReview.Decision.REMOVED, reason)
    log_action(request, "listing.removed", prop, reason=reason)
    notify(prop.owner, Notification.Event.LISTING_REMOVED, f"Listing removed: {prop.reference}",
           f"\"{prop.title}\" was removed for violating our listing policy: {reason}",
           reverse("dashboard:partner_property_manage", args=[prop.pk]))
    return prop


def set_suspicious(request, prop, flagged, reason=""):
    prop.is_suspicious = flagged
    prop.save(update_fields=["is_suspicious", "updated_at"])
    _review(prop, request.user, ListingReview.Decision.FLAGGED if flagged else ListingReview.Decision.UNFLAGGED, reason)
    log_action(request, "listing.flagged" if flagged else "listing.unflagged", prop, reason=reason)


def set_featured(request, prop, days):
    if days and not prop.is_public:
        raise ModerationError("Only live listings can be featured.")
    prop.featured_until = timezone.now() + timedelta(days=days) if days else None
    prop.save(update_fields=["featured_until", "updated_at"])
    _review(prop, request.user, ListingReview.Decision.FEATURED if days else ListingReview.Decision.UNFEATURED,
            f"{days} days" if days else "")
    log_action(request, "listing.featured" if days else "listing.unfeatured", prop, days=days)


@transaction.atomic
def decide_verification(request, partner_profile, approve, note="", valid_days=365):
    user = partner_profile.user
    now = timezone.now()
    from django.conf import settings

    retain_until = now + timedelta(days=settings.VERIFICATION_DOC_RETENTION_DAYS)
    if approve:
        partner_profile.verification_status = VerificationStatus.VERIFIED
        partner_profile.verified_at = now
        partner_profile.verification_expires_at = now + timedelta(days=valid_days) if valid_days else None
        partner_profile.verified_by = request.user
        partner_profile.verification_note = note
        user.verification_documents.filter(status=VerificationDocument.Status.PENDING).update(
            status=VerificationDocument.Status.ACCEPTED, reviewed_by=request.user, reviewed_at=now, retain_until=retain_until)
        body = "Your account has been verified. A 'Verified' badge now appears on your listings."
    else:
        if not note.strip():
            raise ModerationError("A reason is required to reject verification.")
        partner_profile.verification_status = VerificationStatus.REJECTED
        partner_profile.verification_note = note
        user.verification_documents.filter(status=VerificationDocument.Status.PENDING).update(
            status=VerificationDocument.Status.REJECTED, reviewed_by=request.user, reviewed_at=now,
            review_note=note[:255], retain_until=now + timedelta(days=30))
        body = f"Your verification could not be approved: {note}. You can upload new documents from your profile."
    partner_profile.save()
    log_action(request, "verification.approved" if approve else "verification.rejected", user, note=note)
    notify(user, Notification.Event.VERIFICATION_UPDATE, "Verification update", body, reverse("dashboard:partner_profile"))


def expire_verification(request, partner_profile):
    partner_profile.verification_status = VerificationStatus.EXPIRED
    partner_profile.save(update_fields=["verification_status", "updated_at"])
    log_action(request, "verification.expired", partner_profile.user)
    notify(partner_profile.user, Notification.Event.VERIFICATION_UPDATE, "Verification expired",
           "Your verification has expired. Please upload current documents to get verified again.",
           reverse("dashboard:partner_profile"))
