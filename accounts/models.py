import uuid

from django.conf import settings
from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.core.files.storage import FileSystemStorage
from django.core.validators import FileExtensionValidator
from django.db import models
from django.utils import timezone
from django.utils.functional import cached_property

from core.models import TimeStampedModel
from core.validators import validate_document_file, validate_image_file, validate_indian_phone


class Role(models.TextChoices):
    CUSTOMER = "customer", "Customer / Tenant / Buyer"
    OWNER = "owner", "Property owner"
    BROKER = "broker", "Real estate broker"
    STAFF = "staff", "Staff member"
    ADMIN = "admin", "Super admin"


PARTNER_ROLES = (Role.OWNER, Role.BROKER)


class UserManager(BaseUserManager):
    use_in_migrations = True

    def _create_user(self, email, password, **extra):
        if not email:
            raise ValueError("Email is required")
        email = self.normalize_email(email).lower()
        user = self.model(email=email, **extra)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email, password=None, **extra):
        extra.setdefault("is_staff", False)
        extra.setdefault("is_superuser", False)
        extra.setdefault("role", Role.CUSTOMER)
        return self._create_user(email, password, **extra)

    def create_superuser(self, email, password=None, **extra):
        extra.setdefault("is_staff", True)
        extra.setdefault("is_superuser", True)
        extra.setdefault("role", Role.ADMIN)
        extra.setdefault("email_verified", True)
        if extra.get("role") != Role.ADMIN:
            raise ValueError("Superuser must have the admin role.")
        return self._create_user(email, password, **extra)


class User(AbstractUser):
    """Email-based user with a platform role."""

    username = None
    first_name = None
    last_name = None
    email = models.EmailField("email address", unique=True)
    full_name = models.CharField(max_length=120)
    phone = models.CharField(max_length=15, blank=True, validators=[validate_indian_phone], db_index=True)
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.CUSTOMER, db_index=True)
    email_verified = models.BooleanField(default=False)
    phone_verified = models.BooleanField(default=False)
    suspended_at = models.DateTimeField(null=True, blank=True)
    suspension_reason = models.CharField(max_length=255, blank=True)
    staff_permissions = models.JSONField(
        default=list, blank=True, help_text="Management areas a staff member may use (see accounts.staff.AREAS).",
    )

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["full_name"]

    objects = UserManager()

    class Meta:
        ordering = ["-date_joined"]

    def __str__(self):
        return self.full_name or self.email

    def get_full_name(self):
        return self.full_name

    def get_short_name(self):
        return (self.full_name or self.email).split(" ")[0]

    @property
    def is_customer(self):
        return self.role == Role.CUSTOMER

    @property
    def is_owner(self):
        return self.role == Role.OWNER

    @property
    def is_broker(self):
        return self.role == Role.BROKER

    @property
    def is_partner(self):
        return self.role in PARTNER_ROLES

    @property
    def is_platform_admin(self):
        return self.is_active and (self.role == Role.ADMIN or self.is_superuser)

    @property
    def is_staff_member(self):
        return self.is_active and self.role == Role.STAFF and not self.is_suspended

    @property
    def is_management(self):
        """Admins and staff members: people who use the Management panel."""
        return self.is_platform_admin or self.is_staff_member

    def can_manage(self, area):
        """Admins can use every Management area; staff only the areas given to them."""
        if self.is_platform_admin:
            return True
        return self.is_staff_member and area in (self.staff_permissions or [])

    @property
    def is_suspended(self):
        return self.suspended_at is not None

    @cached_property
    def partner_profile(self):
        if self.role == Role.OWNER:
            return getattr(self, "owner_profile", None)
        if self.role == Role.BROKER:
            return getattr(self, "broker_profile", None)
        return None

    @property
    def verification_status(self):
        profile = self.partner_profile
        return profile.verification_status if profile else VerificationStatus.NOT_SUBMITTED

    @property
    def is_verified_partner(self):
        return self.verification_status == VerificationStatus.VERIFIED

    @property
    def display_name(self):
        if self.role == Role.BROKER and getattr(self, "broker_profile", None) and self.broker_profile.agency_name:
            return self.broker_profile.agency_name
        return self.full_name or self.email.split("@")[0]

    def dashboard_url_name(self):
        if self.is_platform_admin:
            return "adminpanel:dashboard"
        if self.is_staff_member:
            return "adminpanel:staff_home"
        if self.is_partner:
            return "dashboard:partner_overview"
        return "dashboard:customer_overview"


def avatar_upload_to(instance, filename):
    return f"avatars/{instance.user_id}/{uuid.uuid4().hex}.{filename.rsplit('.', 1)[-1].lower()}"


class UserProfile(TimeStampedModel):
    class ContactMethod(models.TextChoices):
        PHONE = "phone", "Phone call"
        WHATSAPP = "whatsapp", "WhatsApp"
        EMAIL = "email", "Email"

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="profile")
    avatar = models.ImageField(
        upload_to=avatar_upload_to, blank=True,
        validators=[FileExtensionValidator(["jpg", "jpeg", "png", "webp"]), validate_image_file],
    )
    whatsapp_number = models.CharField(max_length=15, blank=True, validators=[validate_indian_phone])
    city = models.CharField(max_length=80, blank=True, default="Bangarpet")
    about = models.TextField(max_length=1000, blank=True)
    preferred_contact = models.CharField(max_length=10, choices=ContactMethod.choices, default=ContactMethod.PHONE)
    notify_email = models.BooleanField("Email notifications", default=True)
    notify_whatsapp = models.BooleanField("WhatsApp notifications", default=False)
    notify_marketing = models.BooleanField("Product updates and offers", default=False)

    def __str__(self):
        return f"Profile of {self.user}"


class VerificationStatus(models.TextChoices):
    NOT_SUBMITTED = "not_submitted", "Not submitted"
    PENDING = "pending", "Pending review"
    VERIFIED = "verified", "Verified"
    REJECTED = "rejected", "Rejected"
    EXPIRED = "expired", "Expired"


class PartnerProfileBase(TimeStampedModel):
    verification_status = models.CharField(
        max_length=20, choices=VerificationStatus.choices, default=VerificationStatus.NOT_SUBMITTED, db_index=True
    )
    verification_note = models.CharField(max_length=255, blank=True, help_text="Reason shown to the partner.")
    verified_at = models.DateTimeField(null=True, blank=True)
    verification_expires_at = models.DateTimeField(null=True, blank=True)
    verified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    submitted_at = models.DateTimeField(null=True, blank=True)
    address = models.TextField(blank=True)

    class Meta:
        abstract = True

    @property
    def is_verified(self):
        if self.verification_status != VerificationStatus.VERIFIED:
            return False
        return not (self.verification_expires_at and self.verification_expires_at < timezone.now())


class OwnerProfile(PartnerProfileBase):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="owner_profile")

    def __str__(self):
        return f"Owner: {self.user}"


class BrokerProfile(PartnerProfileBase):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="broker_profile")
    agency_name = models.CharField(max_length=160)
    business_phone = models.CharField(max_length=15, blank=True, validators=[validate_indian_phone])
    business_email = models.EmailField(blank=True)
    office_address = models.TextField(blank=True)
    rera_number = models.CharField("RERA registration number", max_length=60, blank=True)
    years_of_experience = models.PositiveSmallIntegerField(null=True, blank=True)
    website = models.URLField(blank=True)

    def __str__(self):
        return f"Broker: {self.agency_name}"


class PrivateDocumentStorage(FileSystemStorage):
    """Storage outside MEDIA_ROOT with no public URL."""

    def __init__(self, **kwargs):
        kwargs.setdefault("location", settings.PRIVATE_MEDIA_ROOT)
        kwargs["base_url"] = None
        super().__init__(**kwargs)

    def url(self, name):  # pragma: no cover - deliberately unsupported
        raise NotImplementedError("Private documents are served through a permission-checked view only.")


def private_storage():
    """Storage for verification documents (configurable per host, never public)."""
    backend = getattr(settings, "PRIVATE_FILE_STORAGE", "")
    if backend:
        from django.utils.module_loading import import_string

        return import_string(backend)()
    return PrivateDocumentStorage()


def document_upload_to(instance, filename):
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "bin"
    return f"verification/{instance.user_id}/{uuid.uuid4().hex}.{ext}"


class VerificationDocument(TimeStampedModel):
    class DocType(models.TextChoices):
        AADHAAR = "aadhaar", "Aadhaar card (mask the first 8 digits)"
        PAN = "pan", "PAN card"
        VOTER_ID = "voter_id", "Voter ID"
        PASSPORT = "passport", "Passport"
        DRIVING_LICENCE = "driving_licence", "Driving licence"
        OWNERSHIP = "ownership", "Ownership proof (tax receipt, sale deed extract, khata)"
        AUTHORIZATION = "authorization", "Authorisation letter from owner"
        BUSINESS = "business", "Business registration / GST / trade licence"
        RERA = "rera", "RERA registration certificate"
        OTHER = "other", "Other supporting document"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending review"
        ACCEPTED = "accepted", "Accepted"
        REJECTED = "rejected", "Rejected"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="verification_documents")
    doc_type = models.CharField(max_length=30, choices=DocType.choices)
    file = models.FileField(
        upload_to=document_upload_to, storage=private_storage,
        validators=[FileExtensionValidator(["pdf", "jpg", "jpeg", "png"]), validate_document_file],
    )
    original_name = models.CharField(max_length=255, blank=True)
    note = models.CharField(max_length=255, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING, db_index=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_note = models.CharField(max_length=255, blank=True)
    retain_until = models.DateTimeField(
        null=True, blank=True, help_text="The file is purged after this date by the purge_verification_documents command."
    )
    file_purged_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.get_doc_type_display()} - {self.user}"

    def purge_file(self):
        if self.file:
            self.file.delete(save=False)
        self.file = ""
        self.file_purged_at = timezone.now()
        self.save(update_fields=["file", "file_purged_at", "updated_at"])


class EmailOTP(models.Model):
    """A one-time code emailed to a user. Only a keyed hash of the code is stored."""

    class Purpose(models.TextChoices):
        VERIFY_EMAIL = "verify_email", "Verify email"
        LOGIN = "login", "Sign in with a code"
        PASSWORD_RESET = "password_reset", "Reset password"
        ADMIN_LOGIN = "admin_login", "Admin 2-step sign-in"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="email_otps")
    purpose = models.CharField(max_length=20, choices=Purpose.choices)
    sent_to = models.CharField(max_length=254)
    code_hash = models.CharField(max_length=64)
    attempts = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["user", "purpose", "created_at"])]

    def __str__(self):
        return f"{self.get_purpose_display()} code for {self.user_id}"

    @property
    def is_usable(self):
        return self.used_at is None and self.expires_at > timezone.now()
