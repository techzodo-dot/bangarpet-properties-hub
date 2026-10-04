"""Initial reference data: categories, amenities and locations around Bangarpet.

These are configuration records, not sample listings. Admins can edit or
deactivate them from /management/. Verify the locality list with local
knowledge before launch.
"""
from django.db import migrations

CATEGORIES = [
    # name, slug, icon, rent, sale, rooms, commercial
    ("Independent House", "houses", "house-door", True, True, True, False),
    ("Apartment / Flat", "apartments", "building", True, True, True, False),
    ("PG & Rooms", "pg-rooms", "door-open", True, False, True, False),
    ("Shops & Offices", "commercial", "shop", True, True, False, True),
    ("Residential Plot", "plots", "bounding-box", False, True, False, False),
    ("Agricultural Land", "agricultural-land", "tree", False, True, False, False),
]

AMENITIES = [
    ("Borewell water", "droplet"), ("Municipal water supply", "droplet-half"), ("Power backup", "lightning-charge"),
    ("Lift", "arrow-down-up"), ("Security guard", "shield-check"), ("CCTV", "camera-video"),
    ("Gated community", "door-closed"), ("Covered parking", "p-square"), ("Garden", "flower1"),
    ("Children's play area", "balloon"), ("Gym", "heart-pulse"), ("Wi-Fi", "wifi"),
    ("Air conditioning", "snow"), ("Modular kitchen", "egg-fried"), ("Wardrobes", "archive"),
    ("Geyser", "thermometer-sun"), ("Rainwater harvesting", "cloud-rain"), ("Meals included", "cup-hot"),
    ("Laundry service", "basket"), ("Pet friendly", "emoji-smile"),
]

TOWNS = [
    # name, slug, pin, lat, lng, popular
    ("Bangarpet", "bangarpet", "563114", "12.991100", "78.177400", True),
    ("Kolar Gold Fields (KGF)", "kgf", "", None, None, False),
    ("Kolar", "kolar", "563101", None, None, False),
    ("Malur", "malur", "", None, None, False),
    ("Budikote", "budikote", "", None, None, False),
    ("Kamasamudra", "kamasamudra", "", None, None, False),
    ("Bethamangala", "bethamangala", "", None, None, False),
]

BANGARPET_AREAS = [
    ("Town Centre", "bangarpet-town-centre"),
    ("Railway Station Area", "bangarpet-railway-station-area"),
    ("Kolar Road", "bangarpet-kolar-road"),
    ("KGF Road", "bangarpet-kgf-road"),
    ("Budikote Road", "bangarpet-budikote-road"),
]


def load(apps, schema_editor):
    Category = apps.get_model("properties", "Category")
    Amenity = apps.get_model("properties", "Amenity")
    Location = apps.get_model("properties", "Location")
    for i, (name, slug, icon, rent, sale, rooms, commercial) in enumerate(CATEGORIES):
        Category.objects.get_or_create(
            slug=slug,
            defaults=dict(name=name, icon=icon, allows_rent=rent, allows_sale=sale, has_rooms=rooms,
                          is_commercial=commercial, display_order=i),
        )
    for i, (name, icon) in enumerate(AMENITIES):
        Amenity.objects.get_or_create(name=name, defaults=dict(icon=icon, display_order=i))
    towns = {}
    for i, (name, slug, pin, lat, lng, popular) in enumerate(TOWNS):
        towns[slug], _ = Location.objects.get_or_create(
            slug=slug,
            defaults=dict(name=name, kind="town", pin_code=pin, latitude=lat, longitude=lng,
                          is_popular=popular, display_order=i),
        )
    for i, (name, slug) in enumerate(BANGARPET_AREAS):
        Location.objects.get_or_create(
            slug=slug,
            defaults=dict(name=name, kind="area", parent=towns["bangarpet"], pin_code="563114",
                          is_popular=True, display_order=i),
        )
    PlatformSetting = apps.get_model("core", "PlatformSetting")
    site, _ = PlatformSetting.objects.get_or_create(pk=1)
    if site.default_town_id is None:
        site.default_town = towns["bangarpet"]
        site.save()


def unload(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("properties", "0001_initial"),
        ("core", "0001_initial"),
    ]
    operations = [migrations.RunPython(load, unload)]
