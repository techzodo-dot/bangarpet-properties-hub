"""Lightweight factories shared by the test suite."""
import io
import itertools
from datetime import timedelta

from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from PIL import Image

from accounts.models import Role, User
from properties.models import Category, Location, Property

_seq = itertools.count(1)
PASSWORD = "Str0ng-Pass!word"


def make_user(role=Role.CUSTOMER, **kwargs):
    n = next(_seq)
    defaults = {"email": f"user{n}@example.com", "full_name": f"Test User {n}", "phone": f"+9198765{n:05d}",
                "role": role, "email_verified": True}
    defaults.update(kwargs)
    if role == Role.ADMIN:
        return User.objects.create_superuser(password=PASSWORD, **{k: v for k, v in defaults.items() if k != "role"})
    return User.objects.create_user(password=PASSWORD, **defaults)


def image_file(name="photo.jpg", size=(800, 600), fmt="JPEG", exif=False):
    buf = io.BytesIO()
    img = Image.new("RGB", size, (200, 120, 40))
    kwargs = {}
    if exif:
        ex = Image.Exif()
        ex[0x010F] = "TestCamera"  # Make
        kwargs["exif"] = ex.tobytes()
    img.save(buf, fmt, **kwargs)
    content_type = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}[fmt]
    return SimpleUploadedFile(name, buf.getvalue(), content_type=content_type)


def make_property(owner=None, status=Property.Status.ACTIVE, purpose="rent", category="houses", price=10000,
                  with_image=False, **kwargs):
    owner = owner or make_user(Role.OWNER)
    town = Location.objects.get(slug="bangarpet")
    now = timezone.now()
    data = dict(
        owner=owner, title=kwargs.pop("title", "Spacious 2 BHK house for families"), purpose=purpose,
        category=Category.objects.get(slug=category),
        description="A well maintained house with good water supply, close to schools and the bus stand.",
        town=town, area=Location.objects.get(slug="bangarpet-town-centre"), locality="Main road", pin_code="563114",
        bedrooms=2, bathrooms=2, built_up_area=900, furnishing="semi", parking="bike",
        monthly_rent=price if purpose == "rent" else None, sale_price=price if purpose == "sale" else None,
        contact_name="Owner", contact_phone="+919876500000", whatsapp_number="+919876500000",
        status=status, wizard_step=7,
    )
    if status == Property.Status.ACTIVE:
        data.update(published_at=now, expires_at=now + timedelta(days=30), submitted_at=now)
    data.update(kwargs)
    prop = Property.objects.create(**data)
    if with_image:
        from properties.services import add_images

        add_images(prop, [image_file()])
    return prop
