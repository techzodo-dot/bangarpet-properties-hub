"""Create clearly-marked DEMO data for local development and testing.

    python manage.py seed_demo            # create demo accounts, listings and activity
    python manage.py seed_demo --reset    # remove existing demo data, then recreate it
    python manage.py seed_demo --clear    # remove all demo data

Demo accounts use the domain demo.bph.local. Every listing has is_demo=True,
a "[DEMO]" title prefix and generated illustrations labelled "SAMPLE IMAGE"
(not real photos). The command refuses to run when DEBUG=False unless --force.
"""
import io
import random
from datetime import timedelta

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from PIL import Image, ImageDraw, ImageFont

from accounts.models import BrokerProfile, OwnerProfile, Role, User, VerificationStatus
from enquiries.services import schedule_visit, submit_enquiry
from moderation.models import Report
from payments.models import Invoice, Payment
from properties.models import Amenity, Category, Favourite, Location, Property, RecentlyViewed
from properties.services import add_images
from subscriptions.models import SubscriptionPlan
from subscriptions.services import activate_subscription, current_subscription

DEMO_DOMAIN = "demo.bph.local"
DEMO_PASSWORD = "DemoPass#2024"

# title, category, purpose, beds, baths, built-up sqft, price, furnishing, parking, area slug, amenity names
LISTINGS = [
    ("2 BHK independent house near the railway station", "houses", "rent", 2, 2, 950, 9000, "semi", "bike",
     "bangarpet-railway-station-area", ["Borewell water", "Power backup", "Covered parking"]),
    ("3 BHK house with terrace on Kolar Road", "houses", "sale", 3, 2, 1450, 5200000, "unfurnished", "car",
     "bangarpet-kolar-road", ["Municipal water supply", "Garden", "Rainwater harvesting"]),
    ("1 BHK first-floor apartment in the town centre", "apartments", "rent", 1, 1, 600, 6500, "semi", "bike",
     "bangarpet-town-centre", ["Municipal water supply", "Geyser", "CCTV"]),
    ("Spacious 2 BHK apartment with lift", "apartments", "sale", 2, 2, 1100, 3800000, "semi", "car",
     "bangarpet-kgf-road", ["Lift", "Security guard", "Power backup", "Covered parking"]),
    ("PG for working men with meals", "pg-rooms", "rent", 4, 2, 1200, 5500, "fully", "bike",
     "bangarpet-town-centre", ["Meals included", "Wi-Fi", "Laundry service"]),
    ("Single room for students near the bus stand", "pg-rooms", "rent", 1, 1, 180, 3000, "semi", "none",
     "bangarpet-railway-station-area", ["Wi-Fi", "Geyser"]),
    ("Ground floor shop on the main road", "commercial", "rent", None, 1, 320, 12000, "unfurnished", "bike",
     "bangarpet-kolar-road", ["Power backup", "CCTV"]),
    ("30x40 residential plot with clear title documents", "plots", "sale", None, None, None, 1800000, "", "",
     "bangarpet-budikote-road", []),
    ("Small office space above a bank", "commercial", "sale", None, 1, 540, 2600000, "unfurnished", "car",
     "bangarpet-town-centre", ["Lift", "Power backup", "Security guard"]),
    ("3 BHK duplex house with garden", "houses", "rent", 3, 3, 1800, 18000, "fully", "both",
     "bangarpet-kgf-road", ["Garden", "Covered parking", "Modular kitchen", "Wardrobes"]),
]
# Waits in the admin approval queue.
PENDING_LISTING = ("2 BHK house near Bangarpet bus stand", "houses", "rent", 2, 1, 820, 7500, "semi", "bike",
                   "bangarpet-town-centre", ["Borewell water"])

NAVY, YELLOW = (23, 43, 77), (255, 214, 0)
WALLS = [(246, 242, 233), (236, 225, 205), (225, 232, 240), (240, 228, 218)]
ROOFS = [(23, 43, 77), (128, 52, 40), (60, 70, 90), (95, 60, 45)]


# ---------------------------------------------------------------------------
# Illustrations - clearly labelled samples, never presented as real photos
# ---------------------------------------------------------------------------
def _font(size):
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow < 10.1
        return ImageFont.load_default()


def _sky_and_ground(d, w, h, horizon, rnd):
    top = (150 + rnd.randint(0, 30), 185 + rnd.randint(0, 20), 240)
    for y in range(horizon):
        t = y / horizon
        d.line([(0, y), (w, y)], fill=tuple(int(top[i] + (248 - top[i]) * t) for i in range(3)))
    d.rectangle([0, horizon, w, h], fill=(126, 168, 104))
    d.rectangle([0, horizon, w, horizon + 14], fill=(108, 150, 88))


def _tree(d, x, base, s=1.0):
    d.rectangle([x - 8 * s, base - 70 * s, x + 8 * s, base], fill=(110, 80, 55))
    d.ellipse([x - 55 * s, base - 190 * s, x + 55 * s, base - 60 * s], fill=(70, 130, 75))
    d.ellipse([x - 35 * s, base - 215 * s, x + 40 * s, base - 120 * s], fill=(85, 148, 88))


def _house(d, w, h, rnd):
    horizon = int(h * 0.66)
    _sky_and_ground(d, w, h, horizon, rnd)
    wall, roof = rnd.choice(WALLS), rnd.choice(ROOFS)
    x0, bw, bh = 300, 600, 300
    y0 = horizon - bh + 30
    d.rectangle([x0, y0, x0 + bw, y0 + bh], fill=wall, outline=(200, 195, 185), width=3)
    d.polygon([(x0 - 50, y0 + 6), (x0 + bw // 2, y0 - 190), (x0 + bw + 50, y0 + 6)], fill=roof)
    door_x = x0 + bw // 2 - 55
    d.rectangle([door_x, y0 + bh - 190, door_x + 110, y0 + bh], fill=NAVY)
    d.ellipse([door_x + 82, y0 + bh - 100, door_x + 94, y0 + bh - 88], fill=YELLOW)
    for wx in (x0 + 60, x0 + bw - 190):
        d.rectangle([wx, y0 + 70, wx + 130, y0 + 175], fill=(180, 210, 235), outline=(255, 255, 255), width=8)
        d.line([(wx + 65, y0 + 70), (wx + 65, y0 + 175)], fill=(255, 255, 255), width=5)
    d.polygon([(door_x + 10, y0 + bh), (door_x + 100, y0 + bh), (door_x + 160, h), (door_x - 50, h)], fill=(205, 190, 165))
    _tree(d, 160, horizon + 40, 1.1)
    _tree(d, 1060, horizon + 50, 0.9)


def _apartment(d, w, h, rnd):
    horizon = int(h * 0.74)
    _sky_and_ground(d, w, h, horizon, rnd)
    x0, bw, top = 330, 540, 120
    d.rectangle([x0, top, x0 + bw, horizon + 10], fill=rnd.choice(WALLS), outline=(190, 185, 175), width=3)
    d.rectangle([x0 - 15, top - 25, x0 + bw + 15, top], fill=rnd.choice(ROOFS))
    floors, cols = 5, 4
    fh = (horizon - top - 40) // floors
    for f in range(floors):
        fy = top + 25 + f * fh
        for c in range(cols):
            cx = x0 + 35 + c * ((bw - 70) // cols)
            d.rectangle([cx, fy, cx + 85, fy + fh - 45], fill=(175, 205, 232), outline=(255, 255, 255), width=5)
        d.rectangle([x0 + 20, fy + fh - 40, x0 + bw - 20, fy + fh - 30], fill=(150, 150, 150))
    d.rectangle([x0 + bw // 2 - 50, horizon - 110, x0 + bw // 2 + 50, horizon + 10], fill=NAVY)
    _tree(d, 210, horizon + 30)
    _tree(d, 1010, horizon + 40, 1.2)


def _shop(d, w, h, rnd):
    horizon = int(h * 0.78)
    _sky_and_ground(d, w, h, horizon, rnd)
    d.rectangle([0, horizon, w, h], fill=(150, 150, 150))
    d.rectangle([0, horizon, w, horizon + 25], fill=(190, 190, 190))
    d.rectangle([180, 180, 1020, horizon], fill=rnd.choice(WALLS), outline=(190, 185, 175), width=3)
    d.rectangle([220, 220, 980, 320], fill=NAVY)
    d.text((600, 270), "SHOP SPACE", fill=YELLOW, font=_font(54), anchor="mm")
    for i in range(10):
        color = YELLOW if i % 2 == 0 else (255, 255, 255)
        d.rectangle([220 + i * 76, 340, 296 + i * 76, 390], fill=color)
    d.rectangle([260, 400, 940, horizon], fill=(170, 175, 182))
    for y in range(410, horizon, 18):
        d.line([(260, y), (940, y)], fill=(140, 145, 152), width=3)


def _plot(d, w, h, rnd):
    horizon = int(h * 0.45)
    _sky_and_ground(d, w, h, horizon, rnd)
    d.rectangle([0, horizon, w, h], fill=(176, 140, 100))
    for _ in range(140):
        x, y = rnd.randint(0, w), rnd.randint(horizon + 10, h)
        d.ellipse([x, y, x + 6, y + 4], fill=(150, 115, 80))
    pts = [(150, h - 60), (1050, h - 60), (900, horizon + 60), (300, horizon + 60)]
    d.line(pts + [pts[0]], fill=(255, 255, 255), width=6)
    for px, py in pts:
        d.rectangle([px - 12, py - 40, px + 12, py], fill=(230, 230, 230), outline=(120, 120, 120))
    d.rectangle([540, horizon + 120, 560, horizon + 260], fill=(110, 80, 55))
    d.rectangle([440, horizon + 60, 660, horizon + 140], fill=NAVY)
    d.text((550, horizon + 100), "PLOT 30x40", fill=YELLOW, font=_font(34), anchor="mm")
    _tree(d, 100, horizon + 20, 0.8)
    _tree(d, 1120, horizon + 30, 0.9)


def _room(d, w, h, rnd, kind):
    floor_y = int(h * 0.68)
    d.rectangle([0, 0, w, floor_y], fill=rnd.choice([(235, 228, 214), (220, 230, 238), (238, 226, 214)]))
    for x in range(0, w, 80):
        d.rectangle([x, floor_y, x + 78, h], fill=(196, 160, 120) if (x // 80) % 2 else (186, 150, 110))
    d.rectangle([760, 120, 1060, 380], fill=(185, 215, 240), outline=(255, 255, 255), width=12)
    d.line([(910, 120), (910, 380)], fill=(255, 255, 255), width=8)
    if kind == "bedroom":
        d.rectangle([120, floor_y - 260, 160, floor_y + 20], fill=(120, 85, 60))
        d.rectangle([140, floor_y - 170, 620, floor_y - 40], fill=(250, 250, 250))
        d.rectangle([140, floor_y - 60, 640, floor_y + 20], fill=(120, 85, 60))
        d.rectangle([170, floor_y - 200, 330, floor_y - 150], fill=YELLOW)
        d.rectangle([380, floor_y - 160, 620, floor_y - 60], fill=NAVY)
    elif kind == "kitchen":
        d.rectangle([80, floor_y - 210, 700, floor_y + 30], fill=(240, 240, 240))
        d.rectangle([80, floor_y - 230, 700, floor_y - 200], fill=(70, 70, 75))
        for i in range(5):
            d.rectangle([95 + i * 122, floor_y - 180, 200 + i * 122, floor_y + 15], outline=(190, 190, 190), width=4)
        d.rectangle([80, 120, 700, 260], fill=NAVY)
        for i in range(1, 4):
            d.line([(80 + i * 155, 120), (80 + i * 155, 260)], fill=(50, 70, 110), width=4)
    else:  # living room / office hall
        d.rounded_rectangle([150, floor_y - 230, 590, floor_y - 120], radius=25, fill=(40, 62, 100))
        d.rounded_rectangle([120, floor_y - 170, 620, floor_y + 10], radius=30, fill=NAVY)
        d.ellipse([250, floor_y - 200, 330, floor_y - 140], fill=YELLOW)
        d.rectangle([680, floor_y - 40, 860, floor_y + 60], fill=(120, 85, 60))
    d.line([(0, floor_y), (w, floor_y)], fill=(160, 130, 100), width=4)


SCENES = {
    "houses": [_house, lambda d, w, h, r: _room(d, w, h, r, "living"), lambda d, w, h, r: _room(d, w, h, r, "kitchen")],
    "apartments": [_apartment, lambda d, w, h, r: _room(d, w, h, r, "living"), lambda d, w, h, r: _room(d, w, h, r, "bedroom")],
    "pg-rooms": [lambda d, w, h, r: _room(d, w, h, r, "bedroom"), _apartment, lambda d, w, h, r: _room(d, w, h, r, "kitchen")],
    "commercial": [_shop, lambda d, w, h, r: _room(d, w, h, r, "living")],
    "plots": [_plot, _plot],
}


def demo_image(category_slug, index, seed):
    rnd = random.Random(seed)
    w, h = 1200, 900
    img = Image.new("RGB", (w, h), (240, 240, 240))
    d = ImageDraw.Draw(img)
    scenes = SCENES.get(category_slug, SCENES["houses"])
    scenes[index % len(scenes)](d, w, h, rnd)
    # Unmissable label so nobody mistakes the illustration for a real photo.
    # Bottom-right keeps it clear of the listing-card chips (top-left), price (bottom-left) and save button.
    d.rounded_rectangle([w - 420, h - 100, w - 30, h - 34], radius=33, fill=(13, 27, 51))
    d.text((w - 225, h - 67), "SAMPLE IMAGE · DEMO", fill=YELLOW, font=_font(30), anchor="mm")
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=88)
    return ContentFile(buf.getvalue(), name=f"demo-{seed}.jpg")


class Command(BaseCommand):
    help = "Create, reset or clear clearly marked demo data for development."

    def add_arguments(self, parser):
        parser.add_argument("--clear", action="store_true", help="Delete all demo accounts and listings.")
        parser.add_argument("--reset", action="store_true", help="Delete existing demo data, then recreate it.")
        parser.add_argument("--force", action="store_true", help="Allow running when DEBUG is False (staging only).")

    def handle(self, *args, **options):
        if not settings.DEBUG and not options["force"]:
            raise CommandError("Refusing to touch demo data with DEBUG=False. Use --force only on a staging copy.")
        if options["clear"] or options["reset"]:
            self.clear()
            if options["clear"]:
                return
        if Property.objects.filter(is_demo=True).exists():
            self.stdout.write("Demo data already exists. Use --reset to recreate it.")
        else:
            with transaction.atomic():
                self.seed()
        self.print_accounts()

    @transaction.atomic
    def clear(self):
        users = User.objects.filter(email__endswith=f"@{DEMO_DOMAIN}")
        payments = Payment.objects.filter(user__in=users)
        Invoice.objects.filter(payment__in=payments).delete()
        payments.delete()
        props = Property.objects.filter(pk__in=(Property.objects.filter(is_demo=True) | Property.objects.filter(owner__in=users)).values("pk"))
        for prop in props:
            for img in prop.images.all():
                img.delete()  # also removes the files from storage
        count = props.count()
        props.delete()
        ucount = users.count()
        users.delete()
        self.stdout.write(self.style.SUCCESS(f"Removed {count} demo listings and {ucount} demo accounts."))

    def _user(self, email, name, role, phone):
        user = User.objects.filter(email=email).first()
        if user:
            return user
        if role == Role.ADMIN:
            return User.objects.create_superuser(email=email, password=DEMO_PASSWORD, full_name=name, phone=phone)
        return User.objects.create_user(email=email, password=DEMO_PASSWORD, full_name=name, phone=phone,
                                        role=role, email_verified=True)

    def _listing(self, owner, spec, i, now, status=Property.Status.ACTIVE):
        title, cat, purpose, beds, baths, area, price, furn, parking, area_slug, amenity_names = spec
        rnd = random.Random(i)
        prop = Property(
            owner=owner, title=f"[DEMO] {title}", category=Category.objects.get(slug=cat), purpose=purpose,
            description=(
                "DEMO LISTING - sample data for testing the platform. This property does not exist.\n\n"
                f"{title}. Located in Bangarpet with easy access to schools, shops and public transport. "
                "In a real listing the owner describes water supply, power backup, the neighbourhood and terms here."
            ),
            town=Location.objects.get(slug="bangarpet"), area=Location.objects.filter(slug=area_slug).first(),
            locality="Near main road", pin_code="563114", bedrooms=beds, bathrooms=baths, built_up_area=area,
            land_area=1200 if cat == "plots" else None, furnishing=furn, parking=parking,
            monthly_rent=price if purpose == "rent" else None,
            security_deposit=price * 5 if purpose == "rent" else None,
            sale_price=price if purpose == "sale" else None, is_negotiable=i % 3 == 0,
            latitude=round(12.9911 + rnd.uniform(-0.012, 0.012), 6),
            longitude=round(78.1774 + rnd.uniform(-0.012, 0.012), 6),
            contact_name=owner.display_name, contact_phone=owner.phone, whatsapp_number=owner.phone,
            contact_visibility=Property.ContactVisibility.REGISTERED, status=status,
            submitted_at=now - timedelta(hours=i + 1), policy_accepted_at=now, wizard_step=7, is_demo=True,
        )
        if status == Property.Status.ACTIVE:
            prop.published_at = now - timedelta(days=i)
            prop.expires_at = now + timedelta(days=30 - i)
            prop.featured_until = now + timedelta(days=7) if i in (0, 3, 6) else None
        prop.save()
        prop.amenities.set(Amenity.objects.filter(name__in=amenity_names))
        scenes = SCENES.get(cat, SCENES["houses"])
        add_images(prop, [demo_image(cat, n, seed=i * 10 + n) for n in range(len(scenes))])
        return prop

    def seed(self):
        now = timezone.now()
        admin = self._user(f"admin@{DEMO_DOMAIN}", "Demo Admin", Role.ADMIN, "+919000000000")
        owner = self._user(f"owner@{DEMO_DOMAIN}", "Demo Owner", Role.OWNER, "+919000000001")
        broker = self._user(f"broker@{DEMO_DOMAIN}", "Demo Broker", Role.BROKER, "+919000000002")
        customer = self._user(f"customer@{DEMO_DOMAIN}", "Demo Customer", Role.CUSTOMER, "+919000000003")
        BrokerProfile.objects.filter(user=broker).update(
            agency_name="Demo Realty (sample agency)", verification_status=VerificationStatus.VERIFIED,
            verified_at=now, verified_by=admin, years_of_experience=8)
        OwnerProfile.objects.filter(user=owner).update(verification_status=VerificationStatus.NOT_SUBMITTED)
        # Enough listing slots for the samples, recorded as an admin grant.
        for partner, slug in ((owner, "pro"), (broker, "broker")):
            plan = SubscriptionPlan.objects.filter(slug=slug, is_active=True).first()
            if plan and not current_subscription(partner):
                activate_subscription(partner, plan, granted_by=admin, notes="Demo data grant")

        props = [self._listing(broker if i % 2 else owner, spec, i, now) for i, spec in enumerate(LISTINGS)]
        self._listing(owner, PENDING_LISTING, len(LISTINGS), now, status=Property.Status.PENDING)

        # Customer activity so every dashboard has something to show.
        contact = {"contact_name": customer.full_name, "contact_phone": customer.phone,
                   "contact_email": customer.email, "preferred_contact": "phone"}
        today = timezone.localdate()
        submit_enquiry(customer, props[0], {
            **contact, "message": "Hi, is this house still available? Is borewell water available all year?",
            "request_visit": True, "preferred_date": today + timedelta(days=2), "preferred_slot": "evening",
            "visit_note": "I can come after 5 pm."})
        _, visit = submit_enquiry(customer, props[2], {
            **contact, "message": "I'd like to see this apartment this weekend.",
            "request_visit": True, "preferred_date": today + timedelta(days=3), "preferred_slot": "morning"})
        schedule_visit(visit, now + timedelta(days=3, hours=2), owner, "Please call when you reach the main road.")
        submit_enquiry(customer, props[1], {**contact, "message": "Is the price negotiable? I'm looking to buy within 2 months."})
        submit_enquiry(customer, props[4], {**contact, "message": "Are meals included in the rent? Is there Wi-Fi?"})
        for prop in props[:5]:
            Favourite.objects.get_or_create(user=customer, property=prop)
        for n, prop in enumerate(props[:6]):
            RecentlyViewed.objects.update_or_create(user=customer, property=prop, defaults={"viewed_at": now - timedelta(minutes=n)})
            Property.objects.filter(pk=prop.pk).update(view_count=12 + n * 7)
        Report.objects.get_or_create(
            property=props[6], reporter=customer, reason=Report.Reason.WRONG_INFO,
            defaults={"details": "DEMO report: the rent shown looks different from what was quoted on the phone."})

    def print_accounts(self):
        self.stdout.write(self.style.SUCCESS(
            f"Demo accounts (password for all: {DEMO_PASSWORD}):\n"
            f"  admin@{DEMO_DOMAIN}     super admin -> /management/\n"
            f"  owner@{DEMO_DOMAIN}     owner on the Pro plan -> /partner/dashboard/\n"
            f"  broker@{DEMO_DOMAIN}    verified broker on the Broker plan -> /partner/dashboard/\n"
            f"  customer@{DEMO_DOMAIN}  customer with enquiries, visits and saved homes -> /dashboard/\n"
            "Remove everything with: python manage.py seed_demo --clear"
        ))
