from django.conf import settings
from django.db import models
from django.db.models import Q

from core.models import TimeStampedModel


class Report(TimeStampedModel):
    class Reason(models.TextChoices):
        FRAUD = "fraud", "Suspected fraud or scam"
        WRONG_INFO = "wrong_info", "Incorrect price or details"
        NOT_AVAILABLE = "not_available", "Property no longer available"
        DUPLICATE = "duplicate", "Duplicate listing"
        WRONG_PHOTOS = "wrong_photos", "Photos do not match the property"
        ADVANCE_PAYMENT = "advance_payment", "Asked for advance payment before a visit"
        OFFENSIVE = "offensive", "Offensive or inappropriate content"
        OTHER = "other", "Other"

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        REVIEWING = "reviewing", "Under review"
        RESOLVED = "resolved", "Resolved - action taken"
        DISMISSED = "dismissed", "Dismissed"

    property = models.ForeignKey("properties.Property", on_delete=models.CASCADE, related_name="reports")
    reporter = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="reports_filed")
    reason = models.CharField(max_length=20, choices=Reason.choices)
    details = models.TextField(max_length=2000, blank=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.OPEN, db_index=True)
    admin_note = models.TextField(blank=True)
    handled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    handled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["property", "reporter"], condition=Q(status__in=["open", "reviewing"]),
                name="one_open_report_per_user_property",
            )
        ]

    def __str__(self):
        return f"Report #{self.pk} on {self.property.reference}"


class ListingReview(models.Model):
    """Moderation decisions on a listing; reasons are shown to the owner."""

    class Decision(models.TextChoices):
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        CHANGES_REQUESTED = "changes_requested", "Changes requested"
        REMOVED = "removed", "Removed for policy violation"
        FLAGGED = "flagged", "Marked suspicious"
        UNFLAGGED = "unflagged", "Suspicious flag cleared"
        FEATURED = "featured", "Featured"
        UNFEATURED = "unfeatured", "Feature removed"

    property = models.ForeignKey("properties.Property", on_delete=models.CASCADE, related_name="reviews")
    reviewer = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    decision = models.CharField(max_length=20, choices=Decision.choices)
    reason = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
