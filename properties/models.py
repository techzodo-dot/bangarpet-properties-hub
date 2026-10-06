import builtins
from datetime import timedelta

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify

from core.models import TimeStampedModel
from core.validators import validate_indian_phone, validate_pin_code


class Location(models.Model):
    """Admin-managed towns and localities (Bangarpet by default)."""

    class Kind(models.TextChoices):
        TOWN = "town", "Town / city"
        AREA = "area", "Area / locality"

    name = models.CharField(max_length=100)
    slug = models.SlugField(max_length=120, unique=True)
    kind = models.CharField(max_length=10, choices=Kind.choices, default=Kind.AREA, db_index=True)
    parent = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.PROTECT, related_name="areas",
        limit_choices_to={"kind": "town"}, help_text="Town this area belongs to.",
    )
    district = models.CharField(max_length=60, default="Kolar")
    state = models.CharField(max_length=60, default="Karnataka")
    pin_code = models.CharField(max_length=6, blank=True, validators=[validate_pin_code])
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    is_active = models.BooleanField(default=True)
    is_popular = models.BooleanField(default=False, help_text="Show in 'Popular areas' on the homepage.")
    display_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["display_order", "name"]
        constraints = [
            models.CheckConstraint(
                condition=Q(kind="town", parent__isnull=True) | Q(kind="area", parent__isnull=False),
                name="location_kind_parent_consistent",
            )
        ]

    def __str__(self):
        if self.kind == self.Kind.AREA and self.parent_id:
            return f"{self.name}, {self.parent.name}"
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            base = slugify(f"{self.name}-{self.parent.name}" if self.parent_id else self.name)[:100] or "location"
            slug, n = base, 2
            while Location.objects.filter(slug=slug).exclude(pk=self.pk).exists():
                slug, n = f"{base}-{n}", n + 1
            self.slug = slug
        super().save(*args, **kwargs)


class Category(models.Model):
    name = models.CharField(max_length=80)
    slug = models.SlugField(max_length=90, unique=True)
    icon = models.CharField(max_length=40, default="house", help_text="Bootstrap Icons name, e.g. 'house-door'.")
    description = models.CharField(max_length=255, blank=True)
    allows_rent = models.BooleanField(default=True)
    allows_sale = models.BooleanField(default=True)
    has_rooms = models.BooleanField(default=True, help_text="Bedrooms/bathrooms are relevant for this category.")
    is_commercial = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    display_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["display_order", "name"]
        verbose_name_plural = "categories"

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse("properties:category", args=[self.slug])


class Amenity(models.Model):
    name = models.CharField(max_length=80, unique=True)
    icon = models.CharField(max_length=40, default="check2-circle")
    is_active = models.BooleanField(default=True)
    display_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["display_order", "name"]
        verbose_name_plural = "amenities"

    def __str__(self):
        return self.name


class PropertyQuerySet(models.QuerySet):
    def public(self):
        """Listings that may be shown to the public and search engines."""
        now = timezone.now()
        return self.filter(status=Property.Status.ACTIVE).filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now))

    def owned_by(self, user):
        return self.filter(owner=user).exclude(status=Property.Status.DELETED)

    def occupying_slots(self):
        return self.filter(status__in=Property.SLOT_STATUSES)

    def with_card_data(self):
        return self.select_related(
            "category", "town", "area", "owner", "owner__owner_profile", "owner__broker_profile"
        ).prefetch_related(
            models.Prefetch("images", queryset=PropertyImage.objects.order_by("-is_primary", "order", "id"))
        )


class Property(TimeStampedModel):
    class Purpose(models.TextChoices):
        RENT = "rent", "For rent"
        SALE = "sale", "For sale"

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        PENDING = "pending", "Pending approval"
        CHANGES_REQUESTED = "changes_requested", "Changes requested"
        REJECTED = "rejected", "Rejected"
        ACTIVE = "active", "Active"
        PAUSED = "paused", "Paused"
        RENTED = "rented", "Rented out"
        SOLD = "sold", "Sold"
        EXPIRED = "expired", "Expired"
        REMOVED = "removed", "Removed by admin"
        DELETED = "deleted", "Deleted"

    class Furnishing(models.TextChoices):
        UNFURNISHED = "unfurnished", "Unfurnished"
        SEMI = "semi", "Semi-furnished"
        FULLY = "fully", "Fully furnished"

    class Parking(models.TextChoices):
        NONE = "none", "No parking"
        BIKE = "bike", "Two-wheeler"
        CAR = "car", "Car"
        BOTH = "both", "Car and two-wheeler"

    class Availability(models.TextChoices):
        IMMEDIATE = "immediate", "Ready to move / immediately"
        FROM_DATE = "from_date", "Available from a date"
        UNDER_CONSTRUCTION = "under_construction", "Under construction"

    class ContactVisibility(models.TextChoices):
        PUBLIC = "public", "Show phone to everyone"
        REGISTERED = "registered", "Show phone to signed-in users only"
        HIDDEN = "hidden", "Hide phone - enquiries only"

    class ContactMethod(models.TextChoices):
        PHONE = "phone", "Phone call"
        WHATSAPP = "whatsapp", "WhatsApp"
        ENQUIRY = "enquiry", "Website enquiry"

    class AreaUnit(models.TextChoices):
        SQFT = "sqft", "sq. ft"
        SQYD = "sqyd", "sq. yd"
        ACRE = "acre", "acre"
        GUNTA = "gunta", "gunta"

    SLOT_STATUSES = (Status.PENDING, Status.ACTIVE, Status.PAUSED, Status.CHANGES_REQUESTED)
    EDITABLE_STATUSES = (
        Status.DRAFT, Status.PENDING, Status.CHANGES_REQUESTED, Status.REJECTED,
        Status.ACTIVE, Status.PAUSED, Status.EXPIRED,
    )
    # Changing any of these on a live listing sends it back for moderation.
    MODERATED_FIELDS = (
        "title", "description", "purpose", "category_id", "town_id", "area_id", "locality",
        "street_address", "monthly_rent", "sale_price", "security_deposit", "video_url",
    )

    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="properties")
    reference = models.CharField(max_length=20, unique=True, editable=False, null=True)
    slug = models.SlugField(max_length=160, unique=True, null=True, blank=True)

    # Step 1 - basics
    title = models.CharField(max_length=120)
    purpose = models.CharField(max_length=10, choices=Purpose.choices, db_index=True)
    category = models.ForeignKey(Category, on_delete=models.PROTECT, related_name="properties")
    description = models.TextField(max_length=5000)
    availability = models.CharField(max_length=20, choices=Availability.choices, default=Availability.IMMEDIATE)
    available_from = models.DateField(null=True, blank=True)

    # Step 2 - location
    state = models.CharField(max_length=60, default="Karnataka")
    district = models.CharField(max_length=60, default="Kolar")
    town = models.ForeignKey(
        Location, null=True, on_delete=models.PROTECT, related_name="town_properties", limit_choices_to={"kind": "town"}
    )
    area = models.ForeignKey(
        Location, null=True, blank=True, on_delete=models.PROTECT, related_name="area_properties",
        limit_choices_to={"kind": "area"},
    )
    locality = models.CharField("Locality / landmark", max_length=150, blank=True)
    street_address = models.CharField(max_length=255, blank=True)
    pin_code = models.CharField("PIN code", max_length=6, blank=True, validators=[validate_pin_code])
    latitude = models.DecimalField(
        max_digits=9, decimal_places=6, null=True, blank=True,
        validators=[MinValueValidator(-90), MaxValueValidator(90)],
    )
    longitude = models.DecimalField(
        max_digits=9, decimal_places=6, null=True, blank=True,
        validators=[MinValueValidator(-180), MaxValueValidator(180)],
    )
    show_exact_address = models.BooleanField(
        default=False, help_text="When off, only the area and town are shown publicly and the map pin is approximate."
    )

    # Step 3 - details
    bedrooms = models.PositiveSmallIntegerField(null=True, blank=True, validators=[MaxValueValidator(20)])
    bathrooms = models.PositiveSmallIntegerField(null=True, blank=True, validators=[MaxValueValidator(20)])
    balconies = models.PositiveSmallIntegerField(null=True, blank=True, validators=[MaxValueValidator(20)])
    built_up_area = models.PositiveIntegerField("Built-up area (sq. ft)", null=True, blank=True)
    land_area = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    land_area_unit = models.CharField(max_length=10, choices=AreaUnit.choices, default=AreaUnit.SQFT)
    furnishing = models.CharField(max_length=15, choices=Furnishing.choices, blank=True)
    parking = models.CharField(max_length=10, choices=Parking.choices, blank=True)
    floor_number = models.SmallIntegerField(null=True, blank=True, validators=[MinValueValidator(-2), MaxValueValidator(100)])
    total_floors = models.PositiveSmallIntegerField(null=True, blank=True, validators=[MaxValueValidator(100)])
    property_age_years = models.PositiveSmallIntegerField("Property age (years)", null=True, blank=True, validators=[MaxValueValidator(150)])
    amenities = models.ManyToManyField(Amenity, blank=True, related_name="properties")

    # Step 4 - pricing (INR)
    monthly_rent = models.DecimalField(max_digits=12, decimal_places=0, null=True, blank=True, validators=[MinValueValidator(0)])
    sale_price = models.DecimalField(max_digits=14, decimal_places=0, null=True, blank=True, validators=[MinValueValidator(0)])
    security_deposit = models.DecimalField(max_digits=12, decimal_places=0, null=True, blank=True, validators=[MinValueValidator(0)])
    maintenance_charge = models.DecimalField(
        "Monthly maintenance", max_digits=10, decimal_places=0, null=True, blank=True, validators=[MinValueValidator(0)]
    )
    is_negotiable = models.BooleanField(default=False)
    # Denormalised price used for filtering and sorting.
    price = models.DecimalField(max_digits=14, decimal_places=0, null=True, blank=True, db_index=True)

    # Step 5 - media
    video_url = models.URLField("Video tour URL", blank=True, help_text="YouTube link to a walkthrough video.")

    # Step 6 - contact
    contact_name = models.CharField(max_length=120, blank=True)
    contact_phone = models.CharField(max_length=15, blank=True, validators=[validate_indian_phone])
    whatsapp_number = models.CharField(max_length=15, blank=True, validators=[validate_indian_phone])
    preferred_contact = models.CharField(max_length=10, choices=ContactMethod.choices, default=ContactMethod.PHONE)
    contact_visibility = models.CharField(
        max_length=12, choices=ContactVisibility.choices, default=ContactVisibility.REGISTERED
    )

    # Workflow
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT, db_index=True)
    wizard_step = models.PositiveSmallIntegerField(default=1)
    policy_accepted_at = models.DateTimeField(null=True, blank=True)
    submitted_at = models.DateTimeField(null=True, blank=True)
    published_at = models.DateTimeField(null=True, blank=True, db_index=True)
    expires_at = models.DateTimeField(null=True, blank=True, db_index=True)
    expiry_reminder_sent_at = models.DateTimeField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    moderation_note = models.TextField(blank=True, help_text="Latest reason shown to the owner.")
    is_suspicious = models.BooleanField(default=False, db_index=True)
    featured_until = models.DateTimeField(null=True, blank=True, db_index=True)
    priority = models.PositiveSmallIntegerField(default=0, help_text="Visibility boost from the owner's plan.")
    is_demo = models.BooleanField(default=False, help_text="Sample data created by the seed_demo command.")

    # Counters
    view_count = models.PositiveIntegerField(default=0)
    enquiry_count = models.PositiveIntegerField(default=0)

    objects = PropertyQuerySet.as_manager()

    class Meta:
        ordering = ["-published_at", "-created_at"]
        verbose_name_plural = "properties"
        indexes = [
            models.Index(fields=["status", "purpose", "category"], name="prop_status_purpose_cat"),
            models.Index(fields=["status", "town", "area"], name="prop_status_location"),
            models.Index(fields=["status", "price"], name="prop_status_price"),
            models.Index(fields=["owner", "status"], name="prop_owner_status"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(monthly_rent__isnull=True) | Q(monthly_rent__gte=0), name="prop_rent_non_negative"
            ),
            models.CheckConstraint(
                condition=Q(sale_price__isnull=True) | Q(sale_price__gte=0), name="prop_sale_non_negative"
            ),
        ]

    def __str__(self):
        return f"{self.reference or 'New'} - {self.title}"

    def save(self, *args, **kwargs):
        self.price = self.monthly_rent if self.purpose == self.Purpose.RENT else self.sale_price
        if self.purpose == self.Purpose.SALE:
            self.monthly_rent = None
            self.security_deposit = None
        elif self.purpose == self.Purpose.RENT:
            self.sale_price = None
        creating = self.pk is None
        super().save(*args, **kwargs)
        if creating or not self.reference or not self.slug:
            self.reference = self.reference or f"BPH{self.pk:06d}"
            self.slug = self.slug or f"{slugify(self.title)[:120] or 'property'}-{self.pk}"
            super().save(update_fields=["reference", "slug"])

    def get_absolute_url(self):
        return reverse("properties:detail", args=[self.slug])

    # ---- presentation helpers -------------------------------------------
    @property
    def location_label(self):
        parts = [p for p in [self.area.name if self.area_id else self.locality, self.town.name if self.town_id else ""] if p]
        return ", ".join(parts)

    @property
    def public_address(self):
        if self.show_exact_address and self.street_address:
            return ", ".join(p for p in [self.street_address, self.location_label, self.pin_code] if p)
        return self.location_label

    @property
    def primary_image(self):
        images = list(self.images.all())
        if not images:
            return None
        for img in images:
            if img.is_primary:
                return img
        return images[0]

    @property
    def is_public(self):
        return self.status == self.Status.ACTIVE and (self.expires_at is None or self.expires_at > timezone.now())

    @property
    def is_featured(self):
        return bool(self.featured_until and self.featured_until > timezone.now())

    @property
    def is_owner_verified(self):
        return self.owner.is_verified_partner

    @property
    def listed_by_broker(self):
        return self.owner.role == "broker"

    @property
    def youtube_embed_url(self):
        from properties.utils import youtube_embed_url

        return youtube_embed_url(self.video_url)

    @property
    def days_to_expiry(self):
        if not self.expires_at:
            return None
        return (self.expires_at - timezone.now()).days

    @property
    def is_expiring_soon(self):
        days = self.days_to_expiry
        return self.status == self.Status.ACTIVE and days is not None and 0 <= days <= 7

    @property
    def can_edit(self):
        return self.status in self.EDITABLE_STATUSES

    @property
    def can_pause(self):
        return self.status == self.Status.ACTIVE

    @property
    def can_resume(self):
        return self.status == self.Status.PAUSED

    @property
    def can_close(self):
        return self.status in (self.Status.ACTIVE, self.Status.PAUSED)

    @property
    def can_renew(self):
        return self.status == self.Status.EXPIRED or self.is_expiring_soon

    @property
    def can_delete(self):
        return self.status not in (self.Status.DELETED, self.Status.REMOVED)

    def map_coordinates(self):
        """Coordinates safe to show publicly (rounded when the exact address is private)."""
        if self.latitude is None or self.longitude is None:
            return None
        if self.show_exact_address:
            return float(self.latitude), float(self.longitude)
        return round(float(self.latitude), 2), round(float(self.longitude), 2)

    def activate(self, duration_days, priority=0):
        now = timezone.now()
        self.status = self.Status.ACTIVE
        self.published_at = self.published_at or now
        base = self.expires_at if self.expires_at and self.expires_at > now else now
        self.expires_at = base + timedelta(days=duration_days)
        self.expiry_reminder_sent_at = None
        self.priority = priority


def property_image_upload_to(instance, filename):
    return f"properties/{instance.property_id}/{filename}"


class PropertyImage(models.Model):
    property = models.ForeignKey(Property, on_delete=models.CASCADE, related_name="images")
    image = models.ImageField(upload_to=property_image_upload_to, width_field="width", height_field="height")
    thumbnail = models.ImageField(upload_to=property_image_upload_to, blank=True)
    width = models.PositiveIntegerField(null=True, blank=True)
    height = models.PositiveIntegerField(null=True, blank=True)
    caption = models.CharField(max_length=120, blank=True)
    is_primary = models.BooleanField(default=False)
    order = models.PositiveSmallIntegerField(default=0)
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-is_primary", "order", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["property"], condition=Q(is_primary=True), name="one_primary_image_per_property"
            )
        ]

    def __str__(self):
        return f"Image {self.pk} of {self.property_id}"

    @builtins.property
    def thumb_url(self):
        return (self.thumbnail or self.image).url

    def alt_text(self):
        return self.caption or f"{self.property.title} - photo"

    def delete(self, *args, **kwargs):
        storage_files = [f for f in (self.image, self.thumbnail) if f]
        result = super().delete(*args, **kwargs)
        for f in storage_files:
            f.storage.delete(f.name)
        return result


class PropertyRevision(models.Model):
    """Edit history used by moderators."""

    property = models.ForeignKey(Property, on_delete=models.CASCADE, related_name="revisions")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    changes = models.JSONField(default=dict)
    requires_moderation = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]


class Favourite(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="favourites")
    property = models.ForeignKey(Property, on_delete=models.CASCADE, related_name="favourited_by")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [models.UniqueConstraint(fields=["user", "property"], name="unique_favourite")]


class SavedSearch(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="saved_searches")
    name = models.CharField(max_length=80)
    query_string = models.CharField(max_length=1000)
    notify = models.BooleanField(default=False, help_text="Notify me about new matching listings.")
    last_notified_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return f"{reverse('properties:search')}?{self.query_string}"


class ContactUnlock(models.Model):
    """A customer revealed a listing's phone/WhatsApp. Each listing counts once."""

    class Via(models.TextChoices):
        FREE = "free", "Free monthly contact"
        PASS = "pass", "Contact Pass"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="contact_unlocks")
    property = models.ForeignKey(Property, on_delete=models.CASCADE, related_name="contact_unlocks")
    via = models.CharField(max_length=8, choices=Via.choices, default=Via.FREE)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [models.UniqueConstraint(fields=["user", "property"], name="unique_contact_unlock")]

    def __str__(self):
        return f"{self.user_id} unlocked {self.property_id}"


class RecentlyViewed(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="recently_viewed")
    property = models.ForeignKey(Property, on_delete=models.CASCADE, related_name="+")
    viewed_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ["-viewed_at"]
        constraints = [models.UniqueConstraint(fields=["user", "property"], name="unique_recently_viewed")]


class PropertyDailyStat(models.Model):
    """Per-day views used for listing performance charts."""

    property = models.ForeignKey(Property, on_delete=models.CASCADE, related_name="daily_stats")
    date = models.DateField(db_index=True)
    views = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["date"]
        constraints = [models.UniqueConstraint(fields=["property", "date"], name="unique_property_daily_stat")]

