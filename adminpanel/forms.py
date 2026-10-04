from django import forms
from django.contrib.auth import get_user_model
from django.utils.text import slugify

from accounts.models import Role
from core.forms import BootstrapFormMixin, PhoneField
from core.models import Advertisement, Banner, PlatformSetting
from properties.models import Amenity, Category, Location, Property
from subscriptions.models import SubscriptionPlan

DT_WIDGET = forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M")


class ReasonForm(BootstrapFormMixin, forms.Form):
    reason = forms.CharField(max_length=1000, widget=forms.Textarea(attrs={"rows": 3}), label="Reason (shown to the owner)")


class OptionalReasonForm(BootstrapFormMixin, forms.Form):
    reason = forms.CharField(max_length=1000, required=False, widget=forms.Textarea(attrs={"rows": 2}), label="Note")


class FeatureForm(BootstrapFormMixin, forms.Form):
    days = forms.IntegerField(min_value=0, max_value=365, initial=7, label="Feature for (days, 0 to remove)")


class SuspendForm(BootstrapFormMixin, forms.Form):
    reason = forms.CharField(max_length=255, label="Reason for suspension")


class RoleForm(BootstrapFormMixin, forms.Form):
    role = forms.ChoiceField(choices=[(Role.CUSTOMER, "Customer"), (Role.OWNER, "Owner"), (Role.BROKER, "Broker")])


class VerificationDecisionForm(BootstrapFormMixin, forms.Form):
    decision = forms.ChoiceField(choices=[("approve", "Verify"), ("reject", "Reject")], widget=forms.RadioSelect)
    note = forms.CharField(required=False, max_length=255, label="Note / reason")
    valid_days = forms.IntegerField(required=False, min_value=0, max_value=1825, initial=365,
                                    label="Verification valid for (days, 0 = no expiry)")

    def clean(self):
        data = super().clean()
        if data.get("decision") == "reject" and not data.get("note"):
            self.add_error("note", "Enter a reason for rejection.")
        return data


class PlanForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = SubscriptionPlan
        fields = [
            "name", "slug", "description", "price", "billing_period_days", "listing_limit", "listing_duration_days",
            "has_performance_stats", "has_priority_visibility", "has_advanced_enquiry_tools", "visibility_priority",
            "features", "for_roles", "discount_percent", "discount_label", "discount_ends_at", "is_default",
            "is_active", "display_order",
        ]
        widgets = {"features": forms.Textarea(attrs={"rows": 4}), "discount_ends_at": DT_WIDGET}

    def clean_for_roles(self):
        roles = [r.strip() for r in self.cleaned_data["for_roles"].split(",") if r.strip()]
        if not roles or any(r not in (Role.OWNER, Role.BROKER) for r in roles):
            raise forms.ValidationError("Use 'owner', 'broker' or 'owner,broker'.")
        return ",".join(roles)

    def clean(self):
        data = super().clean()
        if data.get("is_default"):
            if data.get("price") and data["price"] > 0:
                self.add_error("is_default", "The default plan must be free.")
            SubscriptionPlan.objects.filter(is_default=True).exclude(pk=self.instance.pk).update(is_default=False)
        return data


class BannerForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = Banner
        fields = ["title", "subtitle", "image", "alt_text", "link_url", "cta_label", "placement", "display_order",
                  "starts_at", "ends_at", "is_active"]
        widgets = {"starts_at": DT_WIDGET, "ends_at": DT_WIDGET, "image": forms.ClearableFileInput(attrs={"accept": "image/*", "data-resize": "1"})}

    def clean(self):
        data = super().clean()
        if data.get("starts_at") and data.get("ends_at") and data["ends_at"] <= data["starts_at"]:
            self.add_error("ends_at", "End must be after start.")
        return data


class AdvertisementForm(BootstrapFormMixin, forms.ModelForm):
    property_reference = forms.CharField(max_length=20, label="Property ID (e.g. BPH000123)")

    class Meta:
        model = Advertisement
        fields = ["placement", "label", "display_order", "starts_at", "ends_at", "is_active", "notes"]
        widgets = {"starts_at": DT_WIDGET, "ends_at": DT_WIDGET}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            self.fields["property_reference"].initial = self.instance.property.reference
        self.order_fields(["property_reference"] + list(self.Meta.fields))

    def clean_property_reference(self):
        ref = self.cleaned_data["property_reference"].strip().upper()
        prop = Property.objects.filter(reference=ref).first()
        if not prop:
            raise forms.ValidationError("No listing with this ID.")
        if not prop.is_public:
            raise forms.ValidationError("Only live listings can be sponsored.")
        self.instance.property = prop
        return ref


class LocationAdminForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = Location
        fields = ["name", "kind", "parent", "district", "state", "pin_code", "latitude", "longitude",
                  "is_popular", "is_active", "display_order"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["parent"].queryset = Location.objects.filter(kind=Location.Kind.TOWN)

    def clean(self):
        data = super().clean()
        if data.get("kind") == Location.Kind.AREA and not data.get("parent"):
            self.add_error("parent", "Choose the town this area belongs to.")
        if data.get("kind") == Location.Kind.TOWN:
            data["parent"] = None
        return data


class CategoryForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = Category
        fields = ["name", "slug", "icon", "description", "allows_rent", "allows_sale", "has_rooms", "is_commercial",
                  "is_active", "display_order"]

    def clean_slug(self):
        return slugify(self.cleaned_data["slug"])

    def clean(self):
        data = super().clean()
        if not data.get("allows_rent") and not data.get("allows_sale"):
            raise forms.ValidationError("A category must allow rent, sale or both.")
        return data


class AmenityForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = Amenity
        fields = ["name", "icon", "is_active", "display_order"]


class PlatformSettingForm(BootstrapFormMixin, forms.ModelForm):
    support_phone = PhoneField(required=False)
    whatsapp_number = PhoneField(required=False, label="WhatsApp number")

    class Meta:
        model = PlatformSetting
        exclude = ["updated_at"]
        widgets = {
            "listing_policy": forms.Textarea(attrs={"rows": 6}),
            "manual_payment_instructions": forms.Textarea(attrs={"rows": 3}),
            "office_address": forms.Textarea(attrs={"rows": 2}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["default_town"].queryset = Location.objects.filter(kind=Location.Kind.TOWN, is_active=True)


class GrantSubscriptionForm(BootstrapFormMixin, forms.Form):
    email = forms.EmailField(label="Partner email")
    plan = forms.ModelChoiceField(queryset=SubscriptionPlan.objects.filter(is_active=True))
    notes = forms.CharField(max_length=255, label="Reason / reference")

    def clean_email(self):
        user = get_user_model().objects.filter(email__iexact=self.cleaned_data["email"], role__in=[Role.OWNER, Role.BROKER]).first()
        if not user:
            raise forms.ValidationError("No owner or broker account with this email.")
        self.user = user
        return user.email


class BroadcastForm(BootstrapFormMixin, forms.Form):
    AUDIENCE = [("all", "All members"), ("customer", "Customers"), ("partners", "Owners and brokers"),
                ("owner", "Owners"), ("broker", "Brokers")]
    audience = forms.ChoiceField(choices=AUDIENCE)
    title = forms.CharField(max_length=160)
    body = forms.CharField(max_length=2000, widget=forms.Textarea(attrs={"rows": 4}))
    send_external = forms.BooleanField(required=False, label="Also send by email/WhatsApp (respects user preferences)")
