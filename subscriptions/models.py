from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q
from django.utils import timezone

from core.models import TimeStampedModel


class SubscriptionPlan(TimeStampedModel):
    name = models.CharField(max_length=60)
    slug = models.SlugField(max_length=60, unique=True)
    description = models.CharField(max_length=255, blank=True)
    price = models.DecimalField("Price (INR)", max_digits=10, decimal_places=2, validators=[MinValueValidator(0)])
    billing_period_days = models.PositiveSmallIntegerField(default=30, validators=[MinValueValidator(1)])
    listing_limit = models.PositiveSmallIntegerField(default=1, help_text="Maximum active listings at a time.")
    listing_duration_days = models.PositiveSmallIntegerField(
        default=30, validators=[MinValueValidator(1)], help_text="How long an approved listing stays live."
    )
    has_performance_stats = models.BooleanField(default=False)
    has_priority_visibility = models.BooleanField(default=False)
    has_advanced_enquiry_tools = models.BooleanField(default=False)
    visibility_priority = models.PositiveSmallIntegerField(default=0, help_text="Higher numbers rank earlier in default sorting.")
    features = models.TextField(blank=True, help_text="One feature per line, shown on the pricing page.")
    for_roles = models.CharField(
        max_length=20, default="owner,broker", help_text="Comma separated roles allowed to buy this plan (owner,broker)."
    )
    discount_percent = models.PositiveSmallIntegerField(default=0, validators=[MaxValueValidator(90)])
    discount_label = models.CharField(max_length=60, blank=True)
    discount_ends_at = models.DateTimeField(null=True, blank=True)
    is_default = models.BooleanField(default=False, help_text="Plan applied to partners without a paid subscription.")
    is_active = models.BooleanField(default=True)
    display_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["display_order", "price"]
        constraints = [
            models.UniqueConstraint(fields=["is_default"], condition=Q(is_default=True), name="single_default_plan")
        ]

    def __str__(self):
        return self.name

    @property
    def is_free(self):
        return self.price == 0

    @property
    def discount_active(self):
        if not self.discount_percent:
            return False
        return self.discount_ends_at is None or self.discount_ends_at > timezone.now()

    @property
    def effective_price(self):
        if self.discount_active:
            value = self.price * (Decimal(100 - self.discount_percent) / Decimal(100))
            return value.quantize(Decimal("1.00"), rounding=ROUND_HALF_UP)
        return self.price

    @property
    def feature_list(self):
        return [line.strip() for line in self.features.splitlines() if line.strip()]

    def available_for(self, user):
        roles = {r.strip() for r in self.for_roles.split(",") if r.strip()}
        return user.role in roles


class SubscriptionQuerySet(models.QuerySet):
    def current(self):
        now = timezone.now()
        return self.filter(status=Subscription.Status.ACTIVE, starts_at__lte=now, ends_at__gt=now)


class Subscription(TimeStampedModel):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending payment"
        ACTIVE = "active", "Active"
        EXPIRED = "expired", "Expired"
        CANCELLED = "cancelled", "Cancelled"
        SUPERSEDED = "superseded", "Replaced by another plan"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="subscriptions")
    plan = models.ForeignKey(SubscriptionPlan, on_delete=models.PROTECT, related_name="subscriptions")
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PENDING, db_index=True)
    starts_at = models.DateTimeField(null=True, blank=True)
    ends_at = models.DateTimeField(null=True, blank=True, db_index=True)
    amount_paid = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    expiry_reminder_sent_at = models.DateTimeField(null=True, blank=True)
    granted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
        help_text="Admin who granted this subscription manually, if any.",
    )
    notes = models.CharField(max_length=255, blank=True)

    objects = SubscriptionQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["user", "status", "ends_at"], name="sub_user_status_end")]

    def __str__(self):
        return f"{self.user} - {self.plan}"

    @property
    def is_current(self):
        now = timezone.now()
        return self.status == self.Status.ACTIVE and self.starts_at and self.starts_at <= now < self.ends_at

    @property
    def days_remaining(self):
        if not self.ends_at:
            return None
        return max((self.ends_at - timezone.now()).days, 0)
