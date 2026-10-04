from django.conf import settings
from django.db import models


class Notification(models.Model):
    """In-app notification. Delivery over email/WhatsApp is tracked in NotificationDelivery."""

    class Event(models.TextChoices):
        NEW_ENQUIRY = "new_enquiry", "New customer enquiry"
        VISIT_REQUEST = "visit_request", "New property visit request"
        VISIT_CONFIRMED = "visit_confirmed", "Visit confirmation"
        VISIT_CANCELLED = "visit_cancelled", "Visit cancelled"
        ENQUIRY_UPDATE = "enquiry_update", "Enquiry status update"
        LISTING_APPROVED = "listing_approved", "Listing approved"
        LISTING_REJECTED = "listing_rejected", "Listing rejected"
        LISTING_CHANGES = "listing_changes", "Listing changes requested"
        LISTING_REMOVED = "listing_removed", "Listing removed"
        LISTING_EXPIRING = "listing_expiring", "Listing expiry reminder"
        LISTING_EXPIRED = "listing_expired", "Listing expired"
        SUBSCRIPTION_PURCHASED = "subscription_purchased", "Subscription purchase"
        PAYMENT_CONFIRMED = "payment_confirmed", "Payment confirmation"
        PAYMENT_FAILED = "payment_failed", "Payment failed"
        SUBSCRIPTION_EXPIRING = "subscription_expiring", "Subscription expiry reminder"
        SUBSCRIPTION_EXPIRED = "subscription_expired", "Subscription expired"
        VERIFICATION_UPDATE = "verification_update", "Verification status update"
        ACCOUNT_NOTICE = "account_notice", "Account notice"
        ADMIN_NOTICE = "admin_notice", "Important notice"

    recipient = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notifications")
    event = models.CharField(max_length=30, choices=Event.choices, db_index=True)
    title = models.CharField(max_length=160)
    body = models.TextField(max_length=2000)
    link = models.CharField(max_length=255, blank=True)
    is_read = models.BooleanField(default=False, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.title


class NotificationDelivery(models.Model):
    class Channel(models.TextChoices):
        EMAIL = "email", "Email"
        WHATSAPP = "whatsapp", "WhatsApp"

    class Status(models.TextChoices):
        SENT = "sent", "Sent"
        FAILED = "failed", "Failed"
        NOT_CONFIGURED = "not_configured", "Skipped - integration not configured"
        SKIPPED = "skipped", "Skipped - disabled or no recipient"

    notification = models.ForeignKey(Notification, on_delete=models.CASCADE, related_name="deliveries")
    channel = models.CharField(max_length=10, choices=Channel.choices)
    status = models.CharField(max_length=16, choices=Status.choices)
    destination = models.CharField(max_length=160, blank=True)
    provider_message_id = models.CharField(max_length=120, blank=True)
    error = models.CharField(max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
