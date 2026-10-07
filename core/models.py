from django.conf import settings
from django.core.cache import cache
from django.core.validators import FileExtensionValidator, RegexValidator
from django.db import models
from django.utils import timezone

from core.validators import validate_image_file

SETTINGS_CACHE_KEY = "bph:platform-settings"


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class PlatformSetting(models.Model):
    """Singleton holding admin-editable platform configuration."""

    site_name = models.CharField(max_length=120, default="Bangarpet Property Hub")
    tagline = models.CharField(max_length=160, default="Find. Rent. Buy. Manage.")
    logo = models.ImageField(
        upload_to="branding/", blank=True,
        validators=[FileExtensionValidator(["png", "jpg", "jpeg", "webp"]), validate_image_file],
        help_text="Optional. When empty the built-in logo is used.",
    )
    contact_email = models.EmailField(blank=True)
    support_phone = models.CharField(max_length=20, blank=True)
    whatsapp_number = models.CharField(max_length=20, blank=True, help_text="Public WhatsApp support number, e.g. +919876543210")
    office_address = models.TextField(blank=True)
    facebook_url = models.URLField(blank=True)
    instagram_url = models.URLField(blank=True)
    youtube_url = models.URLField(blank=True)
    x_url = models.URLField("X / Twitter URL", blank=True)
    linkedin_url = models.URLField(blank=True)

    default_state = models.CharField(max_length=60, default="Karnataka")
    default_district = models.CharField(max_length=60, default="Kolar")
    default_town = models.ForeignKey(
        "properties.Location", null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
        limit_choices_to={"kind": "town"},
    )
    default_map_lat = models.DecimalField(max_digits=9, decimal_places=6, default=12.991100)
    default_map_lng = models.DecimalField(max_digits=9, decimal_places=6, default=78.177400)

    listing_policy = models.TextField(
        default=(
            "Only list properties you own or are authorised to market.\n"
            "Provide accurate prices, photos and details. Do not post duplicate listings.\n"
            "Photos must be of the actual property and must not contain contact details or watermarks of other platforms.\n"
            "Listings are reviewed before publishing and may be removed if they violate these rules."
        )
    )
    require_listing_approval = models.BooleanField(default=True)
    expiry_reminder_days = models.PositiveSmallIntegerField(default=5)
    allow_manual_payments = models.BooleanField(
        "Allow UPI / bank transfer payments", default=True,
        help_text="Owners, brokers and Contact Pass buyers can pay to your UPI ID and submit the UPI reference; "
                  "the plan activates when you approve it in Payments.",
    )
    upi_id = models.CharField(
        "UPI ID", max_length=100, blank=True, validators=[RegexValidator(
            r"^[A-Za-z0-9.\-_]{2,256}@[A-Za-z][A-Za-z0-9]{1,63}$", "Enter a UPI ID like name@okhdfcbank.")],
        help_text="Shown with a QR code and a pay-by-UPI-app link on the payment page, e.g. bangarpetproperty@okhdfcbank.",
    )
    upi_payee_name = models.CharField(
        "UPI payee name", max_length=60, blank=True, help_text="The name UPI apps show when paying, e.g. Bangarpet Property Hub.",
    )
    manual_payment_instructions = models.TextField(
        blank=True, help_text="Optional extra steps, e.g. bank account details for NEFT/IMPS.",
    )
    contact_limit_enabled = models.BooleanField(
        "Limit free owner contacts", default=True,
        help_text="Customers see a limited number of owner phone numbers each month, then need the Contact Pass.",
    )
    free_contacts_per_month = models.PositiveSmallIntegerField(
        default=5, help_text="Owner contacts a customer can unlock for free each month.",
    )
    gstin = models.CharField("GSTIN", max_length=20, blank=True, help_text="Shown on invoices when set.")
    invoice_business_name = models.CharField(max_length=160, blank=True)

    email_notifications_enabled = models.BooleanField(default=True)
    whatsapp_notifications_enabled = models.BooleanField(default=False)

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "platform settings"
        verbose_name_plural = "platform settings"

    def __str__(self):
        return "Platform settings"

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)
        cache.delete(SETTINGS_CACHE_KEY)

    def delete(self, *args, **kwargs):  # pragma: no cover - singleton is never deleted
        return None

    @classmethod
    def load(cls):
        obj = cache.get(SETTINGS_CACHE_KEY)
        if obj is None:
            obj, _ = cls.objects.get_or_create(pk=1)
            cache.set(SETTINGS_CACHE_KEY, obj, 300)
        return obj

    @property
    def whatsapp_digits(self):
        return "".join(ch for ch in self.whatsapp_number if ch.isdigit())


class ActiveScheduleQuerySet(models.QuerySet):
    def live(self):
        now = timezone.now()
        return self.filter(is_active=True).filter(
            models.Q(starts_at__isnull=True) | models.Q(starts_at__lte=now),
            models.Q(ends_at__isnull=True) | models.Q(ends_at__gt=now),
        )


class Banner(TimeStampedModel):
    class Placement(models.TextChoices):
        HOME_TOP = "home_top", "Homepage - below hero"
        HOME_MIDDLE = "home_middle", "Homepage - middle"
        SEARCH = "search", "Search results sidebar"

    title = models.CharField(max_length=120)
    subtitle = models.CharField(max_length=200, blank=True)
    image = models.ImageField(
        upload_to="banners/",
        validators=[FileExtensionValidator(["png", "jpg", "jpeg", "webp"]), validate_image_file],
    )
    alt_text = models.CharField(max_length=160, blank=True)
    link_url = models.URLField(blank=True)
    cta_label = models.CharField(max_length=40, blank=True)
    placement = models.CharField(max_length=20, choices=Placement.choices, default=Placement.HOME_TOP, db_index=True)
    display_order = models.PositiveSmallIntegerField(default=0)
    starts_at = models.DateTimeField(null=True, blank=True)
    ends_at = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    objects = ActiveScheduleQuerySet.as_manager()

    class Meta:
        ordering = ["placement", "display_order", "-created_at"]

    def __str__(self):
        return self.title


class Advertisement(TimeStampedModel):
    """Sponsored property placement shown with a clear 'Sponsored' label."""

    class Placement(models.TextChoices):
        HOME_FEATURED = "home_featured", "Homepage featured row"
        SEARCH_TOP = "search_top", "Top of search results"

    property = models.ForeignKey("properties.Property", on_delete=models.CASCADE, related_name="advertisements")
    placement = models.CharField(max_length=20, choices=Placement.choices, default=Placement.SEARCH_TOP)
    label = models.CharField(max_length=40, default="Sponsored")
    display_order = models.PositiveSmallIntegerField(default=0)
    starts_at = models.DateTimeField(null=True, blank=True)
    ends_at = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    notes = models.CharField(max_length=255, blank=True, help_text="Internal notes (not shown publicly).")

    objects = ActiveScheduleQuerySet.as_manager()

    class Meta:
        ordering = ["display_order", "-created_at"]

    def __str__(self):
        return f"{self.label}: {self.property}"


class AuditLog(models.Model):
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="audit_actions"
    )
    action = models.CharField(max_length=80, db_index=True)
    target_type = models.CharField(max_length=60, blank=True, db_index=True)
    target_id = models.CharField(max_length=40, blank=True, db_index=True)
    target_repr = models.CharField(max_length=255, blank=True)
    details = models.JSONField(default=dict, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.created_at:%Y-%m-%d %H:%M} {self.action} {self.target_repr}"


class ContactMessage(TimeStampedModel):
    name = models.CharField(max_length=120)
    email = models.EmailField()
    phone = models.CharField(max_length=20, blank=True)
    subject = models.CharField(max_length=160)
    message = models.TextField(max_length=3000)
    is_resolved = models.BooleanField(default=False)
    ip_address = models.GenericIPAddressField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.subject} ({self.email})"


class StoredFile(models.Model):
    """A private file kept in the database (see core.storage.DatabaseStorage).

    Used on hosts without a persistent disk (e.g. Vercel) for verification
    documents, which must never be reachable at a public URL.
    """

    name = models.CharField(max_length=255, unique=True)
    data = models.BinaryField()
    size = models.PositiveIntegerField(default=0)
    content_type = models.CharField(max_length=100, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.name
