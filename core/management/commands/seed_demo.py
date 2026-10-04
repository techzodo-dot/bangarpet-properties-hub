"""Create clearly-marked DEMO data for local development and testing.

    python manage.py seed_demo            # create demo users and listings
    python manage.py seed_demo --clear    # remove all demo data

Demo users use the domain demo.bph.local and every listing has is_demo=True,
a "[DEMO]" title prefix and generated illustration images (not real photos).
Never run this on a production database.
"""
import io
import random
from datetime import timedelta

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from PIL import Image, ImageDraw

from accounts.models import BrokerProfile, OwnerProfile, Role, User, VerificationStatus
from properties.models import Amenity, Category, Location, Property
from properties.services import add_images

DEMO_DOMAIN = "demo.bph.local"
DEMO_PASSWORD = "DemoPass#2024"

LISTINGS = [
    # title, category, purpose, beds, baths, area, price, furnishing, parking, area_slug
    ("2 BHK independent house near the railway station", "houses", "rent", 2, 2, 950, 9000, "semi", "bike", "bangarpet-railway-station-area"),
    ("3 BHK house with terrace on Kolar Road", "houses", "sale", 3, 2, 1450, 5200000, "unfurnished", "car", "bangarpet-kolar-road"),
    ("1 BHK first-floor apartment in the town centre", "apartments", "rent", 1, 1, 600, 6500, "semi", "bike", "bangarpet-town-centre"),
    ("Spacious 2 BHK apartment with lift", "apartments", "sale", 2, 2, 1100, 3800000, "semi", "car", "bangarpet-kgf-road"),
    ("PG for working men with meals", "pg-rooms", "rent", 4, 2, 1200, 5500, "fully", "bike", "bangarpet-town-centre"),
    ("Single room for students near the bus stand", "pg-rooms", "rent", 1, 1, 180, 3000, "semi", "none", "bangarpet-railway-station-area"),
    ("Ground floor shop on the main road", "commercial", "rent", None, 1, 320, 12000, "unfurnished", "bike", "bangarpet-kolar-road"),
    ("30x40 residential plot with clear title documents", "plots", "sale", None, None, None, 1800000, "", "", "bangarpet-budikote-road"),
    ("Small office space above a bank", "commercial", "sale", None, 1, 540, 2600000, "unfurnished", "car", "bangarpet-town-centre"),
    ("3 BHK duplex house with garden", "houses", "rent", 3, 3, 1800, 18000, "fully", "both", "bangarpet-kgf-road"),
]

PALETTES = [((23, 43, 77), (255, 214, 0)), ((34, 57, 106), (240, 200, 60)), ((60, 90, 130), (255, 230, 120)),
            ((18, 60, 70), (250, 190, 70)), ((70, 50, 90), (255, 214, 0))]


def demo_image(seed, label):
    rnd = random.Random(seed)
    w, h = 1200, 900
    navy, yellow = PALETTES[seed % len(PALETTES)]
    img = Image.new("RGB", (w, h), (230, 236, 245))
    d = ImageDraw.Draw(img)
    for y in range(h // 2):
        c = 200 + int(45 * y / (h / 2))
        d.line([(0, y), (w, y)], fill=(c - 20, c - 5, 255))
    d.rectangle([0, h * 0.68, w, h], fill=(120, 170, 110))
    x0, y0 = rnd.randint(200, 380), int(h * 0.38)
    bw, bh = rnd.randint(480, 620), int(h * 0.32)
    d.rectangle([x0, y0, x0 + bw, y0 + bh], fill=(245, 242, 235))
    d.polygon([(x0 - 40, y0), (x0 + bw // 2, y0 - 200), (x0 + bw + 40, y0)], fill=navy)
    for i in range(3):
        wx = x0 + 50 + i * (bw - 100) // 3
        d.rectangle([wx, y0 + 50, wx + 90, y0 + 140], fill=yellow)
    d.rectangle([x0 + bw // 2 - 50, y0 + bh - 170, x0 + bw // 2 + 50, y0 + bh], fill=navy)
    d.rectangle([0, h - 90, w, h], fill=(13, 27, 51))
    d.text((40, h - 62), f"DEMO IMAGE - {label}", fill=(255, 214, 0))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=85)
    buf.seek(0)
    f = ContentFile(buf.getvalue(), name=f"demo-{seed}.jpg")
    f.size = len(buf.getvalue())
    return f


class Command(BaseCommand):
    help = "Create (or --clear) clearly marked demo data for development."

    def add_arguments(self, parser):
        parser.add_argument("--clear", action="store_true", help="Delete all demo users and listings.")
        parser.add_argument("--force", action="store_true", help="Allow running when DEBUG is False.")

    def handle(self, *args, **options):
        if not settings.DEBUG and not options["force"]:
            raise CommandError("Refusing to seed demo data with DEBUG=False. Use --force only on a staging copy.")
        if options["clear"]:
            self.clear()
            return
        with transaction.atomic():
            self.seed()

    def clear(self):
        props = Property.objects.filter(is_demo=True)
        for prop in props:
            for img in prop.images.all():
                img.delete()
        count = props.count()
        props.delete()
        users = User.objects.filter(email__endswith=f"@{DEMO_DOMAIN}")
        ucount = users.count()
        users.delete()
        self.stdout.write(self.style.SUCCESS(f"Removed {count} demo listings and {ucount} demo users."))

    def _user(self, email, name, role, phone):
        user, created = User.objects.get_or_create(
            email=email, defaults={"full_name": name, "role": role, "phone": phone, "email_verified": True}
        )
        if created:
            user.set_password(DEMO_PASSWORD)
            user.save()
        return user

    def seed(self):
        now = timezone.now()
        owner = self._user(f"owner@{DEMO_DOMAIN}", "Demo Owner", Role.OWNER, "+919000000001")
        broker = self._user(f"broker@{DEMO_DOMAIN}", "Demo Broker", Role.BROKER, "+919000000002")
        self._user(f"customer@{DEMO_DOMAIN}", "Demo Customer", Role.CUSTOMER, "+919000000003")
        BrokerProfile.objects.filter(user=broker).update(
            agency_name="Demo Realty (sample agency)", verification_status=VerificationStatus.VERIFIED, verified_at=now
        )
        OwnerProfile.objects.filter(user=owner).update(verification_status=VerificationStatus.NOT_SUBMITTED)
        if Property.objects.filter(is_demo=True).exists():
            self.stdout.write("Demo listings already exist. Use --clear first to recreate them.")
        else:
            town = Location.objects.get(slug="bangarpet")
            amenities = list(Amenity.objects.all()[:8])
            for i, (title, cat, purpose, beds, baths, area, price, furn, parking, area_slug) in enumerate(LISTINGS):
                partner = broker if i % 2 else owner
                category = Category.objects.get(slug=cat)
                prop = Property(
                    owner=partner, title=f"[DEMO] {title}", category=category, purpose=purpose,
                    description=(
                        "DEMO LISTING - sample data for testing the platform. This property does not exist.\n\n"
                        f"{title}. Located in Bangarpet with access to schools, shops and public transport. "
                        "Water supply and power backup details would be described here by the owner."
                    ),
                    town=town, area=Location.objects.filter(slug=area_slug).first(), locality="Near main road",
                    pin_code="563114", bedrooms=beds, bathrooms=baths, built_up_area=area,
                    land_area=1200 if cat == "plots" else None, furnishing=furn, parking=parking,
                    monthly_rent=price if purpose == "rent" else None,
                    security_deposit=price * 5 if purpose == "rent" else None,
                    sale_price=price if purpose == "sale" else None,
                    is_negotiable=bool(i % 3 == 0),
                    latitude=12.9911 + random.uniform(-0.01, 0.01), longitude=78.1774 + random.uniform(-0.01, 0.01),
                    contact_name=partner.display_name, contact_phone=partner.phone, whatsapp_number=partner.phone,
                    contact_visibility=Property.ContactVisibility.REGISTERED,
                    status=Property.Status.ACTIVE, published_at=now - timedelta(days=i), expires_at=now + timedelta(days=30),
                    submitted_at=now - timedelta(days=i), policy_accepted_at=now, wizard_step=7, is_demo=True,
                    featured_until=now + timedelta(days=7) if i in (0, 3, 6) else None,
                )
                prop.save()
                prop.amenities.set(random.sample(amenities, k=min(4, len(amenities))))
                add_images(prop, [demo_image(i * 3 + n, f"{prop.reference} photo {n + 1}") for n in range(3)])
        self.stdout.write(self.style.SUCCESS(
            "Demo data ready. Sign in with any of these accounts (password: %s):\n"
            "  owner@%s (owner)\n  broker@%s (verified broker)\n  customer@%s (customer)\n"
            "Create an admin with: python manage.py createsuperuser" % (DEMO_PASSWORD, DEMO_DOMAIN, DEMO_DOMAIN, DEMO_DOMAIN)
        ))
