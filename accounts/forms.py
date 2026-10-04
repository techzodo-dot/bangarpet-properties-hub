from django import forms
from django.contrib.auth import authenticate, password_validation
from django.contrib.auth.forms import (
    AuthenticationForm,
    PasswordChangeForm,
    PasswordResetForm,
    SetPasswordForm,
)

from accounts.models import BrokerProfile, OwnerProfile, Role, User, UserProfile, VerificationDocument
from core import ratelimit
from core.forms import BootstrapFormMixin, PhoneField


class RegistrationForm(BootstrapFormMixin, forms.Form):
    ACCOUNT_TYPES = [
        (Role.CUSTOMER, "I'm looking for a property"),
        (Role.OWNER, "I own property to rent or sell"),
        (Role.BROKER, "I'm a real estate broker / agent"),
    ]

    account_type = forms.ChoiceField(choices=ACCOUNT_TYPES, widget=forms.RadioSelect, initial=Role.CUSTOMER)
    full_name = forms.CharField(max_length=120, widget=forms.TextInput(attrs={"autocomplete": "name"}))
    email = forms.EmailField(widget=forms.EmailInput(attrs={"autocomplete": "email"}))
    phone = PhoneField(label="Mobile number")
    agency_name = forms.CharField(
        max_length=160, required=False, label="Agency / business name", help_text="Required for brokers."
    )
    password1 = forms.CharField(
        label="Password", strip=False, widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
        help_text="At least 8 characters. Avoid common or purely numeric passwords.",
    )
    password2 = forms.CharField(
        label="Confirm password", strip=False, widget=forms.PasswordInput(attrs={"autocomplete": "new-password"})
    )
    accept_terms = forms.BooleanField(label="I agree to the Terms of Use and Privacy Policy")
    website = forms.CharField(required=False, widget=forms.HiddenInput, label="")  # honeypot

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("An account with this email already exists. Try signing in or resetting your password.")
        return email

    def clean_website(self):
        if self.cleaned_data.get("website"):
            raise forms.ValidationError("Invalid submission.")
        return ""

    def clean(self):
        data = super().clean()
        if data.get("account_type") == Role.BROKER and not data.get("agency_name"):
            self.add_error("agency_name", "Enter your agency or business name.")
        p1, p2 = data.get("password1"), data.get("password2")
        if p1 and p2 and p1 != p2:
            self.add_error("password2", "The two passwords do not match.")
        if p1:
            temp_user = User(email=data.get("email", ""), full_name=data.get("full_name", ""))
            try:
                password_validation.validate_password(p1, temp_user)
            except forms.ValidationError as exc:
                self.add_error("password1", exc)
        return data

    def save(self):
        data = self.cleaned_data
        user = User.objects.create_user(
            email=data["email"], password=data["password1"], full_name=data["full_name"].strip(),
            phone=data["phone"], role=data["account_type"],
        )
        if user.role == Role.BROKER:
            BrokerProfile.objects.filter(user=user).update(agency_name=data["agency_name"].strip())
        return user


class LoginForm(BootstrapFormMixin, AuthenticationForm):
    """Email + password login with throttling per account and per IP."""

    username = forms.EmailField(label="Email", widget=forms.EmailInput(attrs={"autofocus": True, "autocomplete": "email"}))
    remember_me = forms.BooleanField(required=False, initial=True, label="Keep me signed in")

    error_messages = {
        "invalid_login": "Incorrect email or password.",
        "inactive": "This account is not active. Contact support if you believe this is a mistake.",
        "throttled": "Too many failed sign-in attempts. Please wait 15 minutes and try again, or reset your password.",
    }

    def clean(self):
        email = (self.cleaned_data.get("username") or "").strip().lower()
        password = self.cleaned_data.get("password")
        ip = ratelimit.get_client_ip(self.request) if self.request else "unknown"
        keys = [f"user:{email}", f"ip:{ip}"]
        if any(ratelimit.is_limited("login", k) for k in keys):
            raise forms.ValidationError(self.error_messages["throttled"], code="throttled")
        if email and password:
            self.user_cache = authenticate(self.request, username=email, password=password)
            if self.user_cache is None:
                for k in keys:
                    ratelimit.hit("login", k)
                from accounts.models import User as _User

                inactive = _User.objects.filter(email__iexact=email, is_active=False).first()
                if inactive and inactive.check_password(password):
                    raise forms.ValidationError(self.error_messages["inactive"], code="inactive")
                raise self.get_invalid_login_error()
            self.confirm_login_allowed(self.user_cache)
            ratelimit.reset("login", f"user:{email}")
        return self.cleaned_data


class ThrottledPasswordResetForm(BootstrapFormMixin, PasswordResetForm):
    pass


class StyledSetPasswordForm(BootstrapFormMixin, SetPasswordForm):
    pass


class StyledPasswordChangeForm(BootstrapFormMixin, PasswordChangeForm):
    pass


class AccountForm(BootstrapFormMixin, forms.ModelForm):
    phone = PhoneField(label="Mobile number")

    class Meta:
        model = User
        fields = ["full_name", "phone"]


class ProfileForm(BootstrapFormMixin, forms.ModelForm):
    whatsapp_number = PhoneField(required=False, label="WhatsApp number")

    class Meta:
        model = UserProfile
        fields = ["avatar", "whatsapp_number", "city", "preferred_contact", "about"]
        widgets = {"about": forms.Textarea(attrs={"rows": 3}), "avatar": forms.ClearableFileInput(attrs={"accept": "image/*"})}


class NotificationPreferencesForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = UserProfile
        fields = ["notify_email", "notify_whatsapp", "notify_marketing"]


class OwnerProfileForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = OwnerProfile
        fields = ["address"]
        widgets = {"address": forms.Textarea(attrs={"rows": 3})}


class BrokerProfileForm(BootstrapFormMixin, forms.ModelForm):
    business_phone = PhoneField(required=False)

    class Meta:
        model = BrokerProfile
        fields = [
            "agency_name", "business_phone", "business_email", "office_address", "rera_number",
            "years_of_experience", "website",
        ]
        widgets = {"office_address": forms.Textarea(attrs={"rows": 3})}


class VerificationDocumentForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = VerificationDocument
        fields = ["doc_type", "file", "note"]
        widgets = {"file": forms.ClearableFileInput(attrs={"accept": ".pdf,.jpg,.jpeg,.png"})}
        help_texts = {"file": "PDF, JPG or PNG up to 5 MB. Mask Aadhaar numbers except the last 4 digits."}

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        Doc = VerificationDocument.DocType
        if user is not None and user.role == Role.OWNER:
            allowed = [Doc.AADHAAR, Doc.PAN, Doc.VOTER_ID, Doc.PASSPORT, Doc.DRIVING_LICENCE, Doc.OWNERSHIP, Doc.AUTHORIZATION, Doc.OTHER]
        else:
            allowed = [Doc.AADHAAR, Doc.PAN, Doc.VOTER_ID, Doc.PASSPORT, Doc.DRIVING_LICENCE, Doc.BUSINESS, Doc.RERA, Doc.AUTHORIZATION, Doc.OTHER]
        self.fields["doc_type"].choices = [(c.value, c.label) for c in allowed]
