import json
from datetime import date

from django import forms
from django.conf import settings
from django.core.exceptions import ValidationError

from core.forms import BootstrapFormMixin, PhoneField
from core.validators import validate_image_file
from moderation.models import Report
from properties.models import Amenity, Category, Location, Property, SavedSearch
from properties.utils import youtube_video_id

MAX_RENT = 10_00_000
MAX_SALE = 500_00_00_000


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------
class PropertySearchForm(BootstrapFormMixin, forms.Form):
    SORT_CHOICES = [
        ("recommended", "Recommended"),
        ("newest", "Newest first"),
        ("price_asc", "Price: low to high"),
        ("price_desc", "Price: high to low"),
    ]
    BEDROOM_CHOICES = [("", "Any"), ("1", "1+"), ("2", "2+"), ("3", "3+"), ("4", "4+")]
    BATHROOM_CHOICES = [("", "Any"), ("1", "1+"), ("2", "2+"), ("3", "3+")]
    LISTED_BY_CHOICES = [("", "Anyone"), ("owner", "Owners"), ("broker", "Brokers")]

    q = forms.CharField(required=False, max_length=100, label="Keyword",
                        widget=forms.TextInput(attrs={"placeholder": "Area, landmark or property ID"}))
    location = forms.ChoiceField(required=False, label="Location")
    category = forms.ChoiceField(required=False, label="Property type")
    purpose = forms.ChoiceField(required=False, label="Rent or buy",
                                choices=[("", "Rent or buy"), ("rent", "Rent"), ("sale", "Buy")])
    min_price = forms.IntegerField(required=False, min_value=0, label="Min price (₹)",
                                   widget=forms.NumberInput(attrs={"placeholder": "No min", "inputmode": "numeric"}))
    max_price = forms.IntegerField(required=False, min_value=0, label="Max price (₹)",
                                   widget=forms.NumberInput(attrs={"placeholder": "No max", "inputmode": "numeric"}))
    bedrooms = forms.ChoiceField(required=False, choices=BEDROOM_CHOICES, label="Bedrooms")
    bathrooms = forms.ChoiceField(required=False, choices=BATHROOM_CHOICES, label="Bathrooms")
    furnishing = forms.ChoiceField(required=False, label="Furnishing",
                                   choices=[("", "Any")] + list(Property.Furnishing.choices))
    parking = forms.BooleanField(required=False, label="Parking available")
    min_area = forms.IntegerField(required=False, min_value=0, label="Min area (sq. ft)")
    max_area = forms.IntegerField(required=False, min_value=0, label="Max area (sq. ft)")
    amenities = forms.ModelMultipleChoiceField(
        required=False, queryset=Amenity.objects.none(), widget=forms.CheckboxSelectMultiple, label="Amenities"
    )
    verified = forms.BooleanField(required=False, label="Verified owners/brokers only")
    listed_by = forms.ChoiceField(required=False, choices=LISTED_BY_CHOICES, label="Listed by")
    available_by = forms.DateField(required=False, label="Available by",
                                   widget=forms.DateInput(attrs={"type": "date"}))
    sort = forms.ChoiceField(required=False, choices=SORT_CHOICES, label="Sort by")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        locations = Location.objects.filter(is_active=True).select_related("parent").order_by("kind", "display_order", "name")
        towns = [(loc.slug, loc.name) for loc in locations if loc.kind == Location.Kind.TOWN]
        areas = [(loc.slug, str(loc)) for loc in locations if loc.kind == Location.Kind.AREA]
        self.fields["location"].choices = [("", "All locations"), ("Towns", towns), ("Areas", areas)]
        self.fields["category"].choices = [("", "All property types")] + [
            (c.slug, c.name) for c in Category.objects.filter(is_active=True)
        ]
        self.fields["amenities"].queryset = Amenity.objects.filter(is_active=True)
        self.translate_fields()  # location and category names were added after the base translation pass

    def clean(self):
        data = super().clean()
        lo, hi = data.get("min_price"), data.get("max_price")
        if lo is not None and hi is not None and lo > hi:
            data["min_price"], data["max_price"] = hi, lo
        lo, hi = data.get("min_area"), data.get("max_area")
        if lo is not None and hi is not None and lo > hi:
            data["min_area"], data["max_area"] = hi, lo
        return data


class SaveSearchForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = SavedSearch
        fields = ["name", "notify"]
        labels = {"name": "Name this search"}


class ReportForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = Report
        fields = ["reason", "details"]
        widgets = {"details": forms.Textarea(attrs={"rows": 3, "placeholder": "Tell us what's wrong (optional)"})}


# ---------------------------------------------------------------------------
# Listing wizard
# ---------------------------------------------------------------------------
WIZARD_STEPS = [
    (1, "Basics", "info-circle"),
    (2, "Location", "geo-alt"),
    (3, "Details", "rulers"),
    (4, "Pricing", "currency-rupee"),
    (5, "Photos", "images"),
    (6, "Contact", "telephone"),
    (7, "Review", "check2-square"),
]


class BasicsForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = Property
        fields = ["title", "category", "purpose", "description", "availability", "available_from"]
        widgets = {
            "purpose": forms.RadioSelect,
            "description": forms.Textarea(attrs={"rows": 6, "placeholder": "Describe the property, nearby landmarks, water supply, transport, etc."}),
            "available_from": forms.DateInput(attrs={"type": "date"}),
            "title": forms.TextInput(attrs={"placeholder": "e.g. 2 BHK independent house near Bangarpet railway station"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        cats = Category.objects.filter(is_active=True)
        self.fields["category"].queryset = cats
        self.fields["category"].empty_label = "Select property type"
        self.fields["purpose"].choices = list(Property.Purpose.choices)
        rules = {str(c.pk): {"rent": c.allows_rent, "sale": c.allows_sale} for c in cats}
        self.fields["category"].widget.attrs["data-category-rules"] = json.dumps(rules)

    def clean_title(self):
        title = " ".join(self.cleaned_data["title"].split())
        if len(title) < 10:
            raise ValidationError("Use a descriptive title of at least 10 characters.")
        if any(ch.isdigit() for ch in title) and sum(ch.isdigit() for ch in title) >= 10:
            raise ValidationError("Please don't put phone numbers in the title.")
        return title

    def clean_description(self):
        desc = self.cleaned_data["description"].strip()
        if len(desc) < 30:
            raise ValidationError("Add a few more details (at least 30 characters).")
        return desc

    def clean(self):
        data = super().clean()
        cat, purpose = data.get("category"), data.get("purpose")
        if cat and purpose:
            if purpose == Property.Purpose.RENT and not cat.allows_rent:
                self.add_error("purpose", f"{cat.name} can only be listed for sale.")
            if purpose == Property.Purpose.SALE and not cat.allows_sale:
                self.add_error("purpose", f"{cat.name} can only be listed for rent.")
        if data.get("availability") == Property.Availability.FROM_DATE:
            if not data.get("available_from"):
                self.add_error("available_from", "Choose the date from which the property is available.")
        else:
            data["available_from"] = None
        return data


class LocationForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = Property
        fields = [
            "state", "district", "town", "area", "locality", "street_address", "pin_code",
            "latitude", "longitude", "show_exact_address",
        ]
        widgets = {
            "latitude": forms.NumberInput(attrs={"step": "0.000001", "placeholder": "Optional, e.g. 12.991100"}),
            "longitude": forms.NumberInput(attrs={"step": "0.000001", "placeholder": "Optional, e.g. 78.177400"}),
        }
        help_texts = {"street_address": "Shown publicly only if you enable 'show exact address'."}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["town"].queryset = Location.objects.filter(kind=Location.Kind.TOWN, is_active=True)
        self.fields["town"].required = True
        self.fields["town"].empty_label = "Select town"
        self.fields["area"].queryset = Location.objects.filter(kind=Location.Kind.AREA, is_active=True).select_related("parent")
        self.fields["area"].empty_label = "Other / not listed"
        self.fields["area"].label = "Area / locality"
        self.fields["pin_code"].widget.attrs.update({"inputmode": "numeric", "maxlength": "6"})
        self.fields["state"].widget.attrs["readonly"] = "readonly"

    def clean_state(self):
        return "Karnataka"

    def clean(self):
        data = super().clean()
        town, area = data.get("town"), data.get("area")
        if town and area and area.parent_id != town.pk:
            self.add_error("area", f"{area.name} is not in {town.name}. Pick an area in the selected town.")
        if not area and not data.get("locality"):
            self.add_error("locality", "Enter the locality or a nearby landmark.")
        if (data.get("latitude") is None) != (data.get("longitude") is None):
            self.add_error("longitude", "Enter both latitude and longitude, or leave both empty.")
        if data.get("show_exact_address") and not data.get("street_address"):
            self.add_error("street_address", "Enter the street address to show it publicly.")
        return data


class DetailsForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = Property
        fields = [
            "bedrooms", "bathrooms", "balconies", "built_up_area", "land_area", "land_area_unit",
            "furnishing", "parking", "floor_number", "total_floors", "property_age_years", "amenities",
        ]
        widgets = {"amenities": forms.CheckboxSelectMultiple}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["amenities"].queryset = Amenity.objects.filter(is_active=True)
        self.fields["furnishing"].choices = [("", "Select")] + list(Property.Furnishing.choices)
        self.fields["parking"].choices = [("", "Select")] + list(Property.Parking.choices)
        cat = self.instance.category if self.instance.category_id else None
        self.category = cat
        if cat and cat.has_rooms:
            for name in ("bedrooms", "bathrooms"):
                self.fields[name].required = True
        if cat and cat.pk and not cat.has_rooms and not cat.is_commercial:
            self.fields["land_area"].required = True
        if cat and (cat.has_rooms or cat.is_commercial):
            self.fields["built_up_area"].required = True
        if cat and cat.slug == "pg-rooms":
            self.fields["bedrooms"].label = "Rooms / beds available"

    def clean(self):
        data = super().clean()
        floor, total = data.get("floor_number"), data.get("total_floors")
        if floor is not None and total is not None and floor > total:
            self.add_error("floor_number", "Floor number cannot be higher than the total floors.")
        area = data.get("built_up_area")
        if area is not None and (area < 50 or area > 1_000_000):
            self.add_error("built_up_area", "Enter a realistic built-up area in sq. ft.")
        return data


class PricingForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = Property
        fields = ["monthly_rent", "security_deposit", "maintenance_charge", "sale_price", "is_negotiable"]
        widgets = {f: forms.NumberInput(attrs={"inputmode": "numeric", "min": 0}) for f in
                   ["monthly_rent", "security_deposit", "maintenance_charge", "sale_price"]}
        labels = {"monthly_rent": "Monthly rent (₹)", "security_deposit": "Security deposit (₹)",
                  "sale_price": "Expected sale price (₹)", "maintenance_charge": "Monthly maintenance (₹)",
                  "is_negotiable": "Price is negotiable"}

    def clean(self):
        data = super().clean()
        purpose = self.instance.purpose
        if purpose == Property.Purpose.RENT:
            rent = data.get("monthly_rent")
            if not rent:
                self.add_error("monthly_rent", "Enter the monthly rent.")
            elif rent > MAX_RENT:
                self.add_error("monthly_rent", "Monthly rent looks too high. Please check the amount.")
            data["sale_price"] = None
        else:
            price = data.get("sale_price")
            if not price:
                self.add_error("sale_price", "Enter the expected sale price.")
            elif price > MAX_SALE:
                self.add_error("sale_price", "Sale price looks too high. Please check the amount.")
            elif price < 10000:
                self.add_error("sale_price", "Enter the full price in rupees (e.g. 4500000 for 45 lakh).")
            data["monthly_rent"] = None
            data["security_deposit"] = None
        return data


class MultipleFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class MultipleImageField(forms.FileField):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("widget", MultipleFileInput(attrs={"accept": "image/jpeg,image/png,image/webp"}))
        super().__init__(*args, **kwargs)

    def clean(self, data, initial=None):
        single = super().clean
        if isinstance(data, (list, tuple)):
            files = [single(d, initial) for d in data if d]
        else:
            files = [single(data, initial)] if data else []
        errors = []
        for f in files:
            try:
                validate_image_file(f)
            except ValidationError as exc:
                errors.append(f"{f.name}: {exc.messages[0]}")
        if errors:
            raise ValidationError(errors)
        return files


class ImageUploadForm(BootstrapFormMixin, forms.Form):
    images = MultipleImageField(required=False, label="Add photos")

    def __init__(self, *args, property_obj=None, **kwargs):
        self.property_obj = property_obj
        super().__init__(*args, **kwargs)
        self.fields["images"].widget.attrs.update({"data-preview": "#upload-preview", "data-max-mb": settings.MAX_IMAGE_UPLOAD_MB, "data-resize": "1"})

    def clean_images(self):
        files = self.cleaned_data["images"]
        existing = self.property_obj.images.count() if self.property_obj else 0
        if existing + len(files) > settings.MAX_IMAGES_PER_PROPERTY:
            raise ValidationError(f"A listing can have at most {settings.MAX_IMAGES_PER_PROPERTY} photos.")
        return files


class MediaForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = Property
        fields = ["video_url"]
        widgets = {"video_url": forms.URLInput(attrs={"placeholder": "https://www.youtube.com/watch?v=..."})}

    def clean_video_url(self):
        url = self.cleaned_data.get("video_url", "").strip()
        if url and not youtube_video_id(url):
            raise ValidationError("Enter a valid YouTube video link.")
        return url


class ContactDetailsForm(BootstrapFormMixin, forms.ModelForm):
    contact_phone = PhoneField(label="Contact phone")
    whatsapp_number = PhoneField(required=False, label="WhatsApp number")

    class Meta:
        model = Property
        fields = ["contact_name", "contact_phone", "whatsapp_number", "preferred_contact", "contact_visibility"]
        widgets = {"contact_visibility": forms.RadioSelect}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["contact_name"].required = True

    def clean(self):
        data = super().clean()
        if data.get("preferred_contact") == Property.ContactMethod.WHATSAPP and not data.get("whatsapp_number"):
            self.add_error("whatsapp_number", "Enter a WhatsApp number or choose another contact method.")
        return data


class SubmitListingForm(forms.Form):
    accept_policy = forms.BooleanField(
        label="I confirm the details are accurate, I am authorised to list this property, and I accept the listing policy."
    )
    accept_policy.widget.attrs["class"] = "form-check-input"


STEP_FORMS = {1: BasicsForm, 2: LocationForm, 3: DetailsForm, 4: PricingForm, 6: ContactDetailsForm}


def listing_completeness(prop):
    """Return a list of human-readable problems that block submission."""
    problems = []
    checks = [
        (1, BasicsForm), (2, LocationForm), (3, DetailsForm), (4, PricingForm), (6, ContactDetailsForm),
    ]
    for step, form_class in checks:
        form = form_class(instance=prop)
        data = {}
        for name, field in form.fields.items():
            value = form.initial.get(name)
            if name == "amenities":
                data[name] = [a.pk for a in prop.amenities.all()] if prop.pk else []
                continue
            if isinstance(value, date):
                value = value.isoformat()
            if isinstance(field, forms.BooleanField):
                if value:
                    data[name] = "on"
                continue
            if hasattr(value, "pk"):
                value = value.pk
            data[name] = "" if value is None else value
        bound = form_class(data=data, instance=Property.objects.get(pk=prop.pk) if prop.pk else prop)
        if not bound.is_valid():
            title = dict((s, t) for s, t, _ in WIZARD_STEPS)[step]
            problems.append((step, f"{title}: " + "; ".join(
                f"{bound.fields[k].label if k in bound.fields else k} - {v[0]}" for k, v in bound.errors.items()
            )))
    if not prop.images.exists():
        problems.append((5, "Photos: add at least one photo of the property."))
    return problems
