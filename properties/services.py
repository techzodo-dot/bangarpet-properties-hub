"""Property business logic shared by public, partner and admin views."""
from datetime import date
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.db.models import Case, F, IntegerField, Q, Value, When
from django.utils import timezone

from core.images import process_upload
from properties.models import Location, Property, PropertyDailyStat, PropertyImage, PropertyRevision, RecentlyViewed


def apply_search_filters(qs, data):
    """Filter a Property queryset with cleaned PropertySearchForm data (all real DB queries)."""
    q = (data.get("q") or "").strip()
    if q:
        ref = q.upper().replace("-", "").replace(" ", "")
        qs = qs.filter(
            Q(title__icontains=q) | Q(locality__icontains=q) | Q(description__icontains=q)
            | Q(area__name__icontains=q) | Q(town__name__icontains=q) | Q(reference__iexact=ref)
        )
    if data.get("location"):
        loc = Location.objects.filter(slug=data["location"], is_active=True).first()
        if loc:
            qs = qs.filter(town=loc) if loc.kind == Location.Kind.TOWN else qs.filter(area=loc)
    if data.get("category"):
        qs = qs.filter(category__slug=data["category"])
    if data.get("purpose"):
        qs = qs.filter(purpose=data["purpose"])
    if data.get("min_price") is not None:
        qs = qs.filter(price__gte=data["min_price"])
    if data.get("max_price") is not None:
        qs = qs.filter(price__lte=data["max_price"])
    if data.get("bedrooms"):
        qs = qs.filter(bedrooms__gte=int(data["bedrooms"]))
    if data.get("bathrooms"):
        qs = qs.filter(bathrooms__gte=int(data["bathrooms"]))
    if data.get("furnishing"):
        qs = qs.filter(furnishing=data["furnishing"])
    if data.get("parking"):
        qs = qs.filter(parking__in=[Property.Parking.BIKE, Property.Parking.CAR, Property.Parking.BOTH])
    if data.get("min_area") is not None:
        qs = qs.filter(built_up_area__gte=data["min_area"])
    if data.get("max_area") is not None:
        qs = qs.filter(built_up_area__lte=data["max_area"])
    amenities = data.get("amenities")
    if amenities:
        for amenity in amenities:
            qs = qs.filter(amenities=amenity)
    if data.get("verified"):
        qs = qs.filter(
            Q(owner__owner_profile__verification_status="verified")
            | Q(owner__broker_profile__verification_status="verified")
        )
    if data.get("listed_by") in ("owner", "broker"):
        qs = qs.filter(owner__role=data["listed_by"])
    if data.get("available_by"):
        qs = qs.filter(
            Q(availability=Property.Availability.IMMEDIATE)
            | Q(availability=Property.Availability.FROM_DATE, available_from__lte=data["available_by"])
        )
    return qs.distinct()


def apply_sort(qs, sort):
    if sort == "price_asc":
        return qs.order_by(F("price").asc(nulls_last=True), "-published_at")
    if sort == "price_desc":
        return qs.order_by(F("price").desc(nulls_last=True), "-published_at")
    if sort == "newest":
        return qs.order_by("-published_at", "-pk")
    now = timezone.now()
    return qs.annotate(
        featured_rank=Case(When(featured_until__gt=now, then=Value(1)), default=Value(0), output_field=IntegerField())
    ).order_by("-featured_rank", "-priority", "-published_at", "-pk")


def search_properties(data, base_qs=None):
    qs = base_qs if base_qs is not None else Property.objects.public()
    qs = apply_search_filters(qs, data)
    return apply_sort(qs, data.get("sort") or "recommended")


def similar_properties(prop, limit=4):
    qs = Property.objects.public().filter(category=prop.category, purpose=prop.purpose).exclude(pk=prop.pk)
    if prop.price:
        qs = qs.filter(price__gte=prop.price * Decimal("0.6"), price__lte=prop.price * Decimal("1.5"))
    results = list(qs.with_card_data().order_by("-published_at")[:limit])
    if len(results) < limit:
        more = (
            Property.objects.public().filter(purpose=prop.purpose, town=prop.town)
            .exclude(pk__in=[prop.pk] + [r.pk for r in results]).with_card_data().order_by("-published_at")
        )
        results += list(more[: limit - len(results)])
    return results


def record_view(request, prop):
    """Count one view per session per listing; update daily stats and recently viewed."""
    seen = request.session.get("viewed_props", [])
    if prop.pk not in seen and not (request.user.is_authenticated and request.user.pk == prop.owner_id):
        Property.objects.filter(pk=prop.pk).update(view_count=F("view_count") + 1)
        today = date.today()
        updated = PropertyDailyStat.objects.filter(property=prop, date=today).update(views=F("views") + 1)
        if not updated:
            try:
                with transaction.atomic():
                    PropertyDailyStat.objects.create(property=prop, date=today, views=1)
            except IntegrityError:
                PropertyDailyStat.objects.filter(property=prop, date=today).update(views=F("views") + 1)
        seen = (seen + [prop.pk])[-200:]
        request.session["viewed_props"] = seen
    if request.user.is_authenticated:
        RecentlyViewed.objects.update_or_create(user=request.user, property=prop, defaults={"viewed_at": timezone.now()})


@transaction.atomic
def add_images(prop, files):
    """Process (strip metadata, resize) and attach uploaded images."""
    created = []
    has_primary = prop.images.filter(is_primary=True).exists()
    start = (prop.images.order_by("-order").values_list("order", flat=True).first() or 0) + 1
    for i, upload in enumerate(files):
        full, thumb, _size = process_upload(upload)
        img = PropertyImage(property=prop, order=start + i, is_primary=not has_primary and i == 0)
        img.image.save(full.name, full, save=False)
        img.thumbnail.save(thumb.name, thumb, save=False)
        img.save()
        created.append(img)
    return created


@transaction.atomic
def set_primary_image(prop, image_id):
    image = prop.images.get(pk=image_id)
    prop.images.filter(is_primary=True).update(is_primary=False)
    image.is_primary = True
    image.save(update_fields=["is_primary"])
    return image


@transaction.atomic
def remove_image(prop, image_id):
    image = prop.images.get(pk=image_id)
    was_primary = image.is_primary
    image.delete()
    if was_primary:
        nxt = prop.images.order_by("order", "id").first()
        if nxt:
            nxt.is_primary = True
            nxt.save(update_fields=["is_primary"])


def snapshot(prop):
    data = {f: getattr(prop, f) for f in Property.MODERATED_FIELDS}
    data.update({
        "bedrooms": prop.bedrooms, "bathrooms": prop.bathrooms, "built_up_area": prop.built_up_area,
        "furnishing": prop.furnishing, "maintenance_charge": prop.maintenance_charge,
        "contact_phone": prop.contact_phone, "whatsapp_number": prop.whatsapp_number,
    })
    return data


def _plain(value):
    if value is None:
        return None
    return str(value)


def record_changes(prop, before, user, extra_changes=None, extra_moderated=True):
    """Store a revision and send live listings back to moderation when key fields change.

    Returns True when the listing was moved back to pending review.
    """
    after = snapshot(prop)
    changes = {k: [_plain(before.get(k)), _plain(v)] for k, v in after.items() if _plain(before.get(k)) != _plain(v)}
    if extra_changes:
        changes.update(extra_changes)
    if not changes:
        return False
    moderated = bool(set(changes) & set(Property.MODERATED_FIELDS)) or bool(extra_changes and extra_moderated)
    # Edits by platform admins are trusted and stay live.
    needs_review = (moderated and prop.status in (Property.Status.ACTIVE, Property.Status.PAUSED)
                    and not getattr(user, "is_platform_admin", False))
    PropertyRevision.objects.create(property=prop, user=user, changes=changes, requires_moderation=needs_review)
    if needs_review:
        from core.models import PlatformSetting

        if PlatformSetting.load().require_listing_approval:
            prop.status = Property.Status.PENDING
            prop.submitted_at = timezone.now()
            prop.save(update_fields=["status", "submitted_at", "updated_at"])
            return True
    return False
