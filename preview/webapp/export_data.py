"""Export public demo data from the Django database to JSON for the web app demo.

Run from the project root:  python manage.py shell < preview/webapp/export_data.py
Writes preview/webapp/data.json (images embedded as data: URIs).
"""
import base64
import json
from pathlib import Path

from django.db.models import Count, Q
from django.utils import timezone

from accounts.models import Role, User, VerificationStatus
from core.faq import FAQS
from properties.models import Category, Location, Property
from subscriptions.models import SubscriptionPlan

OUT = Path("preview/webapp/data.json")


def uri(field):
    if not field:
        return ""
    data = Path(field.path).read_bytes()
    ext = field.name.rsplit(".", 1)[-1].lower()
    mime = {"webp": "image/webp", "jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png"}.get(ext, "image/jpeg")
    return f"data:{mime};base64,{base64.b64encode(data).decode()}"


now = timezone.now()
public = Property.objects.public().with_card_data().prefetch_related("amenities").order_by("-published_at")
listings = []
for p in public:
    listings.append({
        "id": p.pk, "ref": p.reference, "title": p.title, "purpose": p.purpose,
        "category": p.category.slug, "categoryName": p.category.name,
        "town": p.town.name if p.town_id else "Bangarpet", "area": p.area.name if p.area_id else "",
        "areaSlug": p.area.slug if p.area_id else "", "locality": p.locality,
        "price": float(p.price or 0), "deposit": float(p.security_deposit or 0),
        "maintenance": float(p.maintenance_charge or 0), "negotiable": p.is_negotiable,
        "beds": p.bedrooms, "baths": p.bathrooms, "sqft": p.built_up_area,
        "land": float(p.land_area) if p.land_area else None, "landUnit": p.get_land_area_unit_display(),
        "furnishing": p.get_furnishing_display() if p.furnishing else "", "furnishingKey": p.furnishing,
        "parking": p.get_parking_display() if p.parking else "", "parkingKey": p.parking,
        "availability": p.get_availability_display(),
        "amenities": [{"name": a.name, "icon": a.icon} for a in p.amenities.all()],
        "description": p.description, "daysAgo": (now - p.published_at).days if p.published_at else 0,
        "featured": p.is_featured, "verified": p.is_owner_verified,
        "listedBy": "broker" if p.listed_by_broker else "owner",
        "ownerName": p.owner.display_name, "views": p.view_count,
        "lat": float(p.latitude) if p.latitude is not None else None,
        "lng": float(p.longitude) if p.longitude is not None else None,
        "images": [{"full": uri(i.image), "thumb": uri(i.thumbnail), "caption": i.caption} for i in p.images.all()],
    })

areas = []
for loc in Location.objects.filter(is_active=True, is_popular=True).select_related("parent").order_by("kind", "display_order"):
    count = sum(1 for x in listings if x["areaSlug"] == loc.slug or (loc.kind == "town" and x["town"] == loc.name))
    areas.append({"slug": loc.slug, "name": loc.name, "parent": loc.parent.name if loc.parent_id else "", "count": count,
                  "kind": loc.kind})

agents = []
for u in (User.objects.filter(role=Role.BROKER, is_active=True, broker_profile__verification_status=VerificationStatus.VERIFIED)
          .select_related("broker_profile")):
    agents.append({"name": u.full_name, "agency": u.broker_profile.agency_name,
                   "years": u.broker_profile.years_of_experience, "joined": u.date_joined.year,
                   "listings": sum(1 for x in listings if x["ownerName"] == u.display_name)})

data = {
    "listings": listings,
    "categories": [{"slug": c.slug, "name": c.name, "icon": c.icon, "rent": c.allows_rent, "sale": c.allows_sale,
                    "rooms": c.has_rooms} for c in Category.objects.filter(is_active=True)],
    "locations": [{"slug": l.slug, "name": str(l), "kind": l.kind} for l in Location.objects.filter(is_active=True).select_related("parent").order_by("kind", "display_order")],
    "areas": areas,
    "agents": agents,
    "plans": [{"name": pl.name, "price": float(pl.effective_price), "days": pl.billing_period_days, "limit": pl.listing_limit,
               "duration": pl.listing_duration_days, "description": pl.description, "features": pl.feature_list,
               "roles": pl.for_roles} for pl in SubscriptionPlan.objects.filter(is_active=True)],
    "faqs": [{"q": q, "a": a} for q, a in FAQS],
    "amenityOptions": [{"name": a.name, "icon": a.icon} for a in __import__("properties.models", fromlist=["Amenity"]).Amenity.objects.filter(is_active=True)],
}
OUT.write_text(json.dumps(data, ensure_ascii=False))
print("wrote", OUT, len(listings), "listings", round(OUT.stat().st_size / 1024), "KB")
