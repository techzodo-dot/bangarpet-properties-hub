import uuid

from django.conf import settings
from django.core.validators import FileExtensionValidator, MinValueValidator
from django.db import models
from django.utils import timezone

from accounts.models import private_storage
from core.validators import validate_document_file


def receipt_upload_to(instance, filename):
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "bin"
    return f"expenses/{timezone.now():%Y/%m}/{uuid.uuid4().hex}.{ext}"


class Expense(models.Model):
    """A business expense recorded in Management (Expenses)."""

    class Category(models.TextChoices):
        OFFICE = "office", "Office rent & maintenance"
        SALARIES = "salaries", "Salaries & staff"
        MARKETING = "marketing", "Marketing & ads"
        SOFTWARE = "software", "Website, software & hosting"
        PHONE = "phone", "Phone & internet"
        TRAVEL = "travel", "Travel & fuel"
        COMMISSION = "commission", "Commission paid"
        PRINTING = "printing", "Printing & stationery"
        UTILITIES = "utilities", "Electricity & utilities"
        TAXES = "taxes", "Taxes, GST & fees"
        OTHER = "other", "Other"

    class Method(models.TextChoices):
        UPI = "upi", "UPI"
        CASH = "cash", "Cash"
        BANK = "bank", "Bank transfer"
        CARD = "card", "Card"
        CHEQUE = "cheque", "Cheque"

    spent_on = models.DateField("Date", default=timezone.localdate, db_index=True)
    category = models.CharField(max_length=20, choices=Category.choices, default=Category.OTHER, db_index=True)
    description = models.CharField("What for", max_length=160)
    amount = models.DecimalField("Amount (₹)", max_digits=12, decimal_places=2, validators=[MinValueValidator(1)])
    payment_method = models.CharField("Paid by", max_length=10, choices=Method.choices, default=Method.UPI)
    paid_to = models.CharField(max_length=120, blank=True)
    reference = models.CharField("Bill / UPI reference", max_length=80, blank=True)
    notes = models.TextField(blank=True)
    receipt = models.FileField(
        "Bill / receipt photo", upload_to=receipt_upload_to, storage=private_storage, blank=True,
        validators=[FileExtensionValidator(["pdf", "jpg", "jpeg", "png"]), validate_document_file],
        help_text="Optional. PDF or photo; only Management can open it.",
    )
    receipt_name = models.CharField(max_length=160, blank=True, editable=False)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name="expenses_recorded", editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-spent_on", "-created_at"]

    def __str__(self):
        return f"{self.description} ({self.amount})"
