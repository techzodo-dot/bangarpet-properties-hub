import builtins
from django.conf import settings
from django.db import models
from django.db.models import Q

from core.models import TimeStampedModel
from core.validators import validate_indian_phone


class Enquiry(TimeStampedModel):
    class Status(models.TextChoices):
        NEW = "new", "New"
        CONTACTED = "contacted", "Contacted"
        VISIT_REQUESTED = "visit_requested", "Visit requested"
        VISIT_SCHEDULED = "visit_scheduled", "Visit scheduled"
        COMPLETED = "completed", "Completed"
        CLOSED = "closed", "Closed"
        CANCELLED = "cancelled", "Cancelled"

    class ContactMethod(models.TextChoices):
        PHONE = "phone", "Phone call"
        WHATSAPP = "whatsapp", "WhatsApp"
        EMAIL = "email", "Email"

    OPEN_STATUSES = (Status.NEW, Status.CONTACTED, Status.VISIT_REQUESTED, Status.VISIT_SCHEDULED)
    # Statuses an owner/broker may set manually.
    PARTNER_SETTABLE = (Status.CONTACTED, Status.COMPLETED, Status.CLOSED)

    property = models.ForeignKey("properties.Property", on_delete=models.CASCADE, related_name="enquiries")
    customer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="enquiries")
    partner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="received_enquiries",
        help_text="Owner or broker who manages the property at the time of the enquiry.",
    )
    message = models.TextField(max_length=2000)
    contact_name = models.CharField(max_length=120)
    contact_phone = models.CharField(max_length=15, validators=[validate_indian_phone])
    contact_email = models.EmailField(blank=True)
    preferred_contact = models.CharField(max_length=10, choices=ContactMethod.choices, default=ContactMethod.PHONE)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.NEW, db_index=True)
    partner_note = models.TextField(blank=True, max_length=2000, help_text="Private note visible only to the owner/broker.")
    last_status_change_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name_plural = "enquiries"
        constraints = [
            models.UniqueConstraint(
                fields=["customer", "property"],
                condition=Q(status__in=["new", "contacted", "visit_requested", "visit_scheduled"]),
                name="one_open_enquiry_per_customer_property",
            )
        ]
        indexes = [models.Index(fields=["partner", "status"], name="enq_partner_status")]

    def __str__(self):
        return f"Enquiry #{self.pk} on {self.property.reference}"

    @builtins.property
    def is_open(self):
        return self.status in self.OPEN_STATUSES


class EnquiryStatusChange(models.Model):
    enquiry = models.ForeignKey(Enquiry, on_delete=models.CASCADE, related_name="status_changes")
    from_status = models.CharField(max_length=20, blank=True)
    to_status = models.CharField(max_length=20)
    changed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    note = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]

    def get_to_status_display(self):
        return Enquiry.Status(self.to_status).label if self.to_status in Enquiry.Status.values else self.to_status


class PropertyVisit(TimeStampedModel):
    class Status(models.TextChoices):
        REQUESTED = "requested", "Requested"
        SCHEDULED = "scheduled", "Scheduled"
        COMPLETED = "completed", "Completed"
        CANCELLED = "cancelled", "Cancelled"
        NO_SHOW = "no_show", "No-show"

    class TimeSlot(models.TextChoices):
        MORNING = "morning", "Morning (9 am - 12 pm)"
        AFTERNOON = "afternoon", "Afternoon (12 pm - 4 pm)"
        EVENING = "evening", "Evening (4 pm - 7 pm)"

    enquiry = models.ForeignKey(Enquiry, on_delete=models.CASCADE, related_name="visits")
    property = models.ForeignKey("properties.Property", on_delete=models.CASCADE, related_name="visits")
    customer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="visits")
    partner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="hosted_visits")
    preferred_date = models.DateField()
    preferred_slot = models.CharField(max_length=10, choices=TimeSlot.choices, default=TimeSlot.MORNING)
    scheduled_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.REQUESTED, db_index=True)
    customer_note = models.CharField(max_length=500, blank=True)
    partner_note = models.CharField(max_length=500, blank=True, help_text="Shared with the customer (e.g. meeting point).")
    cancelled_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["customer", "property"],
                condition=Q(status__in=["requested", "scheduled"]),
                name="one_active_visit_per_customer_property",
            )
        ]

    def __str__(self):
        return f"Visit #{self.pk} for {self.property.reference}"

    @builtins.property
    def is_active(self):
        return self.status in (self.Status.REQUESTED, self.Status.SCHEDULED)
