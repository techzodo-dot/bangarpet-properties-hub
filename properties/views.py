from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from accounts.models import Role
from core import ratelimit
from core.models import Advertisement, Banner
from core.permissions import member_required
from enquiries.forms import EnquiryForm
from moderation.models import Report
from properties.forms import PropertySearchForm, ReportForm, SaveSearchForm
from properties.models import Category, Favourite, Property, SavedSearch
from properties.services import record_view, search_properties, similar_properties

PAGE_SIZE = 12

PRESETS = {
    "rent": {"purpose": "rent"},
    "buy": {"purpose": "sale"},
    "pg_rooms": {"category": "pg-rooms"},
    "commercial": {"category": "commercial"},
}
PRESET_TITLES = {
    "rent": ("Properties for rent in Bangarpet", "Houses, apartments, rooms and shops available for rent in Bangarpet and nearby towns."),
    "buy": ("Properties for sale in Bangarpet", "Independent houses, apartments, plots and land for sale in Bangarpet, Kolar district."),
    "pg_rooms": ("PG and rooms in Bangarpet", "Paying guest accommodation and rooms for rent in Bangarpet."),
    "commercial": ("Commercial properties in Bangarpet", "Shops, offices and commercial spaces for rent or sale in Bangarpet."),
}


def favourite_ids(user):
    if not user.is_authenticated:
        return set()
    return set(Favourite.objects.filter(user=user).values_list("property_id", flat=True))


def property_search(request, preset=None, category_slug=None):
    params = request.GET.copy()
    fixed = dict(PRESETS.get(preset, {}))
    category = None
    if category_slug:
        category = get_object_or_404(Category, slug=category_slug, is_active=True)
        fixed["category"] = category.slug
    for key, value in fixed.items():
        params.setdefault(key, value)

    form = PropertySearchForm(params)
    form.is_valid()
    data = form.cleaned_data  # invalid fields are simply ignored
    results = search_properties(data).with_card_data()
    paginator = Paginator(results, PAGE_SIZE)
    page_obj = paginator.get_page(request.GET.get("page"))

    view_mode = request.GET.get("view") if request.GET.get("view") in ("grid", "list", "map") else "grid"
    markers = []
    if view_mode == "map":
        for prop in results[:200]:
            coords = prop.map_coordinates()
            if coords:
                markers.append({
                    "lat": coords[0], "lng": coords[1], "title": prop.title, "url": prop.get_absolute_url(),
                    "price": str(prop.price or ""), "purpose": prop.purpose,
                })

    if category:
        title, description = f"{category.name} in Bangarpet", category.description or f"{category.name} for rent and sale in Bangarpet."
    elif preset:
        title, description = PRESET_TITLES[preset]
    else:
        title, description = "Search properties in Bangarpet", "Search verified rental and sale listings in Bangarpet, Kolar district."

    filter_keys = [k for k in request.GET.keys() if k not in ("page", "view", "sort") and request.GET.get(k)]
    active_filters = []
    for key in filter_keys:
        if key in form.fields and key not in fixed:
            field = form[key]
            value = request.GET.getlist(key) if key == "amenities" else request.GET.get(key)
            label = field.label
            display = value
            if hasattr(form.fields[key], "choices") and key != "amenities":
                display = dict((str(k), v) for k, v in _flatten_choices(form.fields[key].choices)).get(str(value), value)
            elif key == "amenities":
                display = f"{len(value)} selected"
            elif key in ("verified", "parking"):
                display = "Yes"
            params_without = request.GET.copy()
            params_without.pop(key, None)
            params_without.pop("page", None)
            active_filters.append({"label": label, "value": display, "remove_qs": params_without.urlencode()})

    querystring = request.GET.copy()
    querystring.pop("page", None)
    context = {
        "form": form,
        "page_obj": page_obj,
        "results_count": paginator.count,
        "view_mode": view_mode,
        "markers": markers,
        "page_title": title,
        "meta_description": description,
        "preset": preset,
        "category": category,
        "fixed": fixed,
        "active_filters": active_filters,
        "querystring": querystring.urlencode(),
        "favourite_ids": favourite_ids(request.user),
        "sponsored": Advertisement.objects.live().filter(placement=Advertisement.Placement.SEARCH_TOP)
        .filter(property__in=Property.objects.public()).select_related("property", "property__town", "property__area", "property__category")[:2]
        if page_obj.number == 1 else [],
        "banners": Banner.objects.live().filter(placement=Banner.Placement.SEARCH)[:1],
        "save_search_form": SaveSearchForm(),
        "noindex": bool(filter_keys) or page_obj.number > 1,
    }
    return render(request, "properties/search.html", context)


def _flatten_choices(choices):
    for key, value in choices:
        if isinstance(value, (list, tuple)):
            yield from value
        else:
            yield key, value


def property_detail(request, slug):
    prop = get_object_or_404(
        Property.objects.select_related("category", "town", "area", "owner", "owner__profile")
        .prefetch_related("images", "amenities"),
        slug=slug,
    )
    user = request.user
    is_owner = user.is_authenticated and user.pk == prop.owner_id
    is_admin = user.is_authenticated and user.is_platform_admin
    if not prop.is_public and not (is_owner or is_admin):
        if prop.status in (Property.Status.RENTED, Property.Status.SOLD):
            return render(request, "properties/unavailable.html", {"prop": prop}, status=410)
        raise Http404("Property not found")
    if prop.is_public:
        record_view(request, prop)

    can_see_phone = prop.contact_visibility == Property.ContactVisibility.PUBLIC or (
        prop.contact_visibility == Property.ContactVisibility.REGISTERED and user.is_authenticated
    ) or is_owner or is_admin
    enquiry_form = None
    existing_enquiry = None
    if user.is_authenticated and not is_owner and user.role != Role.ADMIN:
        existing_enquiry = prop.enquiries.filter(customer=user, status__in=["new", "contacted", "visit_requested", "visit_scheduled"]).first()
        enquiry_form = EnquiryForm(user=user, prop=prop)
    images = list(prop.images.all())
    context = {
        "prop": prop,
        "images": images,
        "is_owner": is_owner,
        "is_admin": is_admin,
        "can_see_phone": can_see_phone,
        "enquiry_form": enquiry_form,
        "existing_enquiry": existing_enquiry,
        "report_form": ReportForm(),
        "similar": similar_properties(prop) if prop.is_public else [],
        "favourite_ids": favourite_ids(user),
        "owner_listing_count": Property.objects.public().filter(owner=prop.owner).count(),
        "map_coords": prop.map_coordinates(),
        "absolute_url": request.build_absolute_uri(prop.get_absolute_url()),
    }
    return render(request, "properties/detail.html", context)


@require_POST
def toggle_favourite(request, pk):
    if not request.user.is_authenticated:
        if request.headers.get("x-requested-with") == "XMLHttpRequest":
            return JsonResponse({"error": "login_required"}, status=401)
        return redirect(f"{reverse('accounts:login')}?next={request.POST.get('next', '/')}")
    prop = get_object_or_404(Property.objects.public(), pk=pk)
    fav = Favourite.objects.filter(user=request.user, property=prop).first()
    if fav:
        fav.delete()
        favourited = False
    else:
        try:
            with transaction.atomic():
                Favourite.objects.create(user=request.user, property=prop)
        except IntegrityError:
            pass
        favourited = True
    if request.headers.get("x-requested-with") == "XMLHttpRequest":
        return JsonResponse({"favourited": favourited})
    messages.success(request, "Saved to your favourites." if favourited else "Removed from your favourites.")
    from core.utils import safe_next_url

    return redirect(safe_next_url(request, prop.get_absolute_url()))


@member_required
@require_POST
def save_search(request):
    form = SaveSearchForm(request.POST)
    query = request.POST.get("query_string", "")[:1000]
    if form.is_valid():
        if request.user.saved_searches.count() >= 20:
            messages.error(request, "You can keep up to 20 saved searches. Delete one to add another.")
        else:
            SavedSearch.objects.create(user=request.user, query_string=query, **form.cleaned_data)
            messages.success(request, "Search saved. Find it under Saved searches in your dashboard.")
    else:
        messages.error(request, "Please give your search a name.")
    return redirect(f"{reverse('properties:search')}?{query}")


@login_required
@require_POST
def report_property(request, pk):
    prop = get_object_or_404(Property.objects.public(), pk=pk)
    if prop.owner_id == request.user.pk:
        messages.error(request, "You cannot report your own listing.")
        return redirect(prop.get_absolute_url())
    if not ratelimit.check_and_hit("report", ratelimit.request_ident(request)):
        messages.error(request, "You've sent several reports recently. Please try again later.")
        return redirect(prop.get_absolute_url())
    form = ReportForm(request.POST)
    if form.is_valid():
        try:
            with transaction.atomic():
                Report.objects.create(property=prop, reporter=request.user, **form.cleaned_data)
            messages.success(request, "Thank you. Our team will review this listing.")
        except IntegrityError:
            messages.info(request, "You have already reported this listing. Our team is reviewing it.")
    else:
        messages.error(request, "Please choose a reason for the report.")
    return redirect(prop.get_absolute_url())


def partner_profile(request, pk):
    User = get_user_model()
    partner = get_object_or_404(User, pk=pk, role__in=[Role.OWNER, Role.BROKER], is_active=True)
    listings = Property.objects.public().filter(owner=partner).with_card_data().order_by("-published_at")
    page_obj = Paginator(listings, PAGE_SIZE).get_page(request.GET.get("page"))
    return render(request, "properties/partner_profile.html", {
        "partner": partner, "page_obj": page_obj, "favourite_ids": favourite_ids(request.user),
    })
