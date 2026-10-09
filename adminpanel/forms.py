from django import forms
from django.db import transaction
from django.contrib.auth import get_user_model, password_validation
from django.utils.text import slugify

from accounts.models import Role
from accounts.staff import AREA_CHOICES, AREAS
from adminpanel.models import Expense
from core.forms import BootstrapFormMixin, PhoneField
from core.models import Advertisement, Banner, PlatformSetting, Video
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
            "features", "for_roles", "unlimited_contacts", "discount_percent", "discount_label", "discount_ends_at", "is_default",
            "is_active", "display_order",
        ]
        widgets = {"features": forms.Textarea(attrs={"rows": 4}), "discount_ends_at": DT_WIDGET}

    def clean_for_roles(self):
        roles = [r.strip().lower() for r in self.cleaned_data["for_roles"].split(",") if r.strip()]
        if not roles or any(r not in (Role.OWNER, Role.BROKER, Role.CUSTOMER) for r in roles):
            raise forms.ValidationError("Use 'owner', 'broker', 'owner,broker' (listing plans) or 'customer' (Contact Pass).")
        return ",".join(dict.fromkeys(roles))

    def clean(self):
        data = super().clean()
        roles = set((data.get("for_roles") or "").split(",")) - {""}
        if data.get("unlimited_contacts"):
            if roles and roles != {Role.CUSTOMER}:
                self.add_error("for_roles", "A Contact Pass plan is for customers: set this to 'customer'.")
            if data.get("is_default"):
                self.add_error("is_default", "The Contact Pass cannot be the default plan.")
            if data.get("price") is not None and data["price"] <= 0:
                self.add_error("price", "The Contact Pass needs a price.")
        elif Role.CUSTOMER in roles:
            self.add_error("for_roles", "Listing plans are for owners and brokers. Tick 'Unlimited contacts' for a customer plan.")
        if data.get("is_default"):
            if data.get("price") and data["price"] > 0:
                self.add_error("is_default", "The default plan must be free.")
            if data.get("is_active") is False:
                self.add_error("is_active", "The default plan must stay active.")
        return data

    def save(self, commit=True):
        plan = super().save(commit=False)
        if commit:
            with transaction.atomic():
                if plan.is_default:
                    SubscriptionPlan.objects.filter(is_default=True).exclude(pk=plan.pk).update(is_default=False)
                plan.save()
                self.save_m2m()
        return plan


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


class VideoForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = Video
        fields = ["title", "youtube_url", "description", "show_on_home", "display_order", "is_active"]
        widgets = {"youtube_url": forms.URLInput(attrs={"placeholder": "https://youtu.be/..."})}


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
    email = forms.EmailField(label="Member email", help_text="Owners and brokers get listing plans; customers the Contact Pass.")
    plan = forms.ModelChoiceField(queryset=SubscriptionPlan.objects.filter(is_active=True, price__gt=0))
    notes = forms.CharField(max_length=255, label="Reason / reference")

    def clean_email(self):
        user = get_user_model().objects.filter(
            email__iexact=self.cleaned_data["email"], role__in=[Role.OWNER, Role.BROKER, Role.CUSTOMER], is_active=True,
        ).first()
        if not user:
            raise forms.ValidationError("No active owner, broker or customer account with this email.")
        self.user = user
        return user.email

    def clean(self):
        data = super().clean()
        plan, user = data.get("plan"), getattr(self, "user", None)
        if plan and user and not plan.available_for(user):
            self.add_error("plan", f"The {plan.name} plan is not available for a {user.get_role_display().lower()} account.")
        return data


class BroadcastForm(BootstrapFormMixin, forms.Form):
    AUDIENCE = [("all", "All members"), ("customer", "Customers"), ("partners", "Owners and brokers"),
                ("owner", "Owners"), ("broker", "Brokers")]
    audience = forms.ChoiceField(choices=AUDIENCE)
    title = forms.CharField(max_length=160)
    body = forms.CharField(max_length=2000, widget=forms.Textarea(attrs={"rows": 4}))
    send_external = forms.BooleanField(required=False, label="Also send by email/WhatsApp (respects user preferences)")


# ---------------------------------------------------------------------------
# Staff accounts and expenses
# ---------------------------------------------------------------------------
class StaffForm(BootstrapFormMixin, forms.Form):
    full_name = forms.CharField(max_length=120)
    email = forms.EmailField(help_text="Staff sign in with this email. Their 2-step sign-in codes are sent here.")
    phone = PhoneField(required=False)
    permissions = forms.MultipleChoiceField(
        choices=AREA_CHOICES, widget=forms.CheckboxSelectMultiple, required=False,
        label="What this staff member can do",
    )
    is_active = forms.BooleanField(required=False, initial=True, label="Account active",
                                   help_text="Untick to stop this person signing in. Their history is kept.")
    password = forms.CharField(
        required=False, widget=forms.PasswordInput(render_value=False, attrs={"autocomplete": "new-password"}),
        label="Password", help_text="At least 10 characters. Leave empty to keep the current password.",
    )

    def __init__(self, *args, staff=None, **kwargs):
        self.staff = staff
        if staff is not None:
            kwargs.setdefault("initial", {
                "full_name": staff.full_name, "email": staff.email, "phone": staff.phone,
                "permissions": staff.staff_permissions, "is_active": staff.is_active,
            })
        super().__init__(*args, **kwargs)
        if staff is None:
            self.fields["password"].required = True
            self.fields["password"].help_text = "At least 10 characters. Share it with the staff member privately."

    def clean_email(self):
        email = get_user_model().objects.normalize_email(self.cleaned_data["email"]).lower()
        clash = get_user_model().objects.filter(email__iexact=email)
        if self.staff is not None:
            clash = clash.exclude(pk=self.staff.pk)
        if clash.exists():
            raise forms.ValidationError("Another account already uses this email.")
        return email

    def clean_password(self):
        password = self.cleaned_data.get("password") or ""
        if password:
            user = self.staff or get_user_model()(email=self.data.get("email", ""), full_name=self.data.get("full_name", ""))
            password_validation.validate_password(password, user)
        return password

    def save(self):
        User = get_user_model()
        data = self.cleaned_data
        user = self.staff or User(role=Role.STAFF)
        user.full_name = data["full_name"]
        user.email = data["email"]
        user.phone = data.get("phone") or ""
        user.staff_permissions = [key for key in AREAS if key in data["permissions"]]
        user.is_active = data["is_active"] if self.staff is not None else True
        user.email_verified = True
        if data.get("password"):
            user.set_password(data["password"])
        user.save()
        return user


class ExpenseForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = Expense
        fields = ["spent_on", "category", "description", "amount", "payment_method", "paid_to", "reference",
                  "receipt", "notes"]
        widgets = {
            "spent_on": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "amount": forms.NumberInput(attrs={"inputmode": "decimal", "step": "0.01", "min": "1"}),
            "notes": forms.Textarea(attrs={"rows": 2}),
            "receipt": forms.ClearableFileInput(attrs={"accept": "image/*,application/pdf"}),
        }


class ExpenseFilterForm(forms.Form):
    month = forms.CharField(required=False, widget=forms.TextInput(attrs={"type": "month", "class": "form-control"}))
    category = forms.ChoiceField(required=False, choices=[("", "All categories"), *Expense.Category.choices],
                                 widget=forms.Select(attrs={"class": "form-select"}))
    q = forms.CharField(required=False, max_length=80, widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "Search what for, paid to, reference"}))
