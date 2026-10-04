"""Reusable validators for uploads and Indian contact details."""
import re

from django.conf import settings
from django.core.exceptions import ValidationError
from PIL import Image, UnidentifiedImageError

ALLOWED_IMAGE_FORMATS = {"JPEG", "PNG", "WEBP"}
ALLOWED_IMAGE_EXTENSIONS = ["jpg", "jpeg", "png", "webp"]
ALLOWED_DOCUMENT_EXTENSIONS = ["pdf", "jpg", "jpeg", "png"]
MAX_IMAGE_PIXELS = 40_000_000  # guards against decompression bombs

PHONE_RE = re.compile(r"^(?:\+?91[\s-]?|0)?([6-9]\d{9})$")
PIN_RE = re.compile(r"^[1-9]\d{5}$")


def normalize_indian_phone(value):
    """Return +91XXXXXXXXXX or raise ValidationError."""
    cleaned = re.sub(r"[\s\-()]", "", value or "")
    match = PHONE_RE.match(cleaned)
    if not match:
        raise ValidationError("Enter a valid 10-digit Indian mobile number.")
    return "+91" + match.group(1)


def validate_indian_phone(value):
    normalize_indian_phone(value)


def validate_pin_code(value):
    if value and not PIN_RE.match(value):
        raise ValidationError("Enter a valid 6-digit PIN code.")


def _check_size(upload, max_mb):
    size = getattr(upload, "size", 0) or 0
    if size > max_mb * 1024 * 1024:
        raise ValidationError(f"File is too large. Maximum size is {max_mb} MB.")


def validate_image_file(upload):
    """Verify that an upload is a real JPEG/PNG/WEBP image within size limits."""
    if not upload:
        return
    _check_size(upload, settings.MAX_IMAGE_UPLOAD_MB)
    try:
        pos = upload.tell() if hasattr(upload, "tell") else None
        upload.seek(0)
        with Image.open(upload) as img:
            fmt = img.format
            width, height = img.size
            img.verify()
        upload.seek(pos or 0)
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError, Image.DecompressionBombError):
        raise ValidationError("Upload a valid image (JPG, PNG or WEBP).")
    if fmt not in ALLOWED_IMAGE_FORMATS:
        raise ValidationError("Only JPG, PNG and WEBP images are allowed.")
    if width * height > MAX_IMAGE_PIXELS:
        raise ValidationError("Image dimensions are too large.")
    if width < 300 or height < 200:
        raise ValidationError("Image is too small. Use at least 300 x 200 pixels.")


def validate_document_file(upload):
    """Accept PDF or image identity documents within size limits."""
    if not upload:
        return
    _check_size(upload, settings.MAX_DOCUMENT_UPLOAD_MB)
    name = (getattr(upload, "name", "") or "").lower()
    ext = name.rsplit(".", 1)[-1] if "." in name else ""
    if ext not in ALLOWED_DOCUMENT_EXTENSIONS:
        raise ValidationError("Upload a PDF, JPG or PNG file.")
    upload.seek(0)
    head = upload.read(8)
    upload.seek(0)
    if ext == "pdf":
        if not head.startswith(b"%PDF"):
            raise ValidationError("The file is not a valid PDF document.")
        return
    try:
        with Image.open(upload) as img:
            fmt = img.format
            img.verify()
        upload.seek(0)
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError, Image.DecompressionBombError):
        raise ValidationError("The file is not a valid image.")
    if fmt not in {"JPEG", "PNG"}:
        raise ValidationError("Upload a PDF, JPG or PNG file.")
