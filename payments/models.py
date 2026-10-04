import uuid

from django.conf import settings
from django.db import models

from core.models import TimeStampedModel


class Payment(TimeStampedModel):
    class Status(models.TextChoices):
        CREATED = "created", "Created"
        PENDING_VERIFICATION = "pending_verification", "Awaiting manual verification"
        PAID = "paid", "Paid"
        FAILED = "failed", "Failed"
        REJECTED = "rejected", "Rejected"
        REFUNDED = "refunded", "Refunded"

    class Gateway(models.TextChoices):
        RAZORPAY = "razorpay", "Razorpay"
        MANUAL = "manual", "Manual (UPI / bank transfer)"

    uid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="payments")
    plan = models.ForeignKey("subscriptions.SubscriptionPlan", on_delete=models.PROTECT, related_name="payments")
    subscription = models.ForeignKey(
        "subscriptions.Subscription", null=True, blank=True, on_delete=models.SET_NULL, related_name="payments"
    )
    gateway = models.CharField(max_length=10, choices=Gateway.choices, default=Gateway.RAZORPAY)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=3, default="INR")
    status = models.CharField(max_length=24, choices=Status.choices, default=Status.CREATED, db_index=True)
    gateway_order_id = models.CharField(max_length=64, blank=True, null=True, unique=True)
    gateway_payment_id = models.CharField(max_length=64, blank=True, null=True, unique=True)
    gateway_signature = models.CharField(max_length=128, blank=True)
    manual_reference = models.CharField(max_length=80, blank=True, help_text="UPI transaction ID / bank reference.")
    failure_reason = models.CharField(max_length=255, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    verified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    raw_response = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Payment {self.uid.hex[:8]} - {self.amount} {self.currency} ({self.status})"

    @property
    def amount_paise(self):
        return int(self.amount * 100)


class Invoice(models.Model):
    payment = models.OneToOneField(Payment, on_delete=models.PROTECT, related_name="invoice")
    number = models.CharField(max_length=30, unique=True)
    issued_at = models.DateTimeField(auto_now_add=True)
    billed_name = models.CharField(max_length=160)
    billed_email = models.EmailField()
    billed_phone = models.CharField(max_length=15, blank=True)
    description = models.CharField(max_length=255)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    period_start = models.DateTimeField(null=True, blank=True)
    period_end = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-issued_at"]

    def __str__(self):
        return self.number


class WebhookEvent(models.Model):
    """Stores processed gateway webhook IDs so retries are handled idempotently."""

    gateway = models.CharField(max_length=20, default="razorpay")
    event_id = models.CharField(max_length=100)
    event_type = models.CharField(max_length=60)
    payload = models.JSONField(default=dict)
    processed_at = models.DateTimeField(auto_now_add=True)
    result = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-processed_at"]
        constraints = [models.UniqueConstraint(fields=["gateway", "event_id"], name="unique_webhook_event")]
