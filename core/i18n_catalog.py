"""Collects every customer-facing string that should be translated.

Used by the translation build script and by the test that guarantees the
Kannada catalogue covers the public site. (gettext/xgettext are not needed.)
"""
import re
from pathlib import Path

from django.conf import settings
from django.utils.translation.template import templatize

# Public, customer-facing templates. Dashboards and Management stay English.
PUBLIC_TEMPLATES = [
    "base.html",
    "partials/header.html", "partials/footer.html", "partials/mobile_tabbar.html", "partials/lang_switch.html",
    "partials/property_card.html", "partials/fav_button.html", "partials/pagination.html",
    "partials/form_errors.html", "partials/field.html", "partials/whatsapp_fab.html", "partials/install_app.html", "partials/video_card.html", "partials/video_player.html", "core/videos.html",
    "core/home.html", "core/page_base.html", "core/contact.html", "core/faq.html",
    "properties/search.html", "properties/detail.html", "properties/unavailable.html", "properties/partner_profile.html",
    "accounts/auth_base.html", "accounts/login.html", "accounts/register.html",
    "accounts/login_code.html", "accounts/otp_verify.html",
    "accounts/password_reset_form.html", "accounts/password_reset_complete.html",
    "enquiries/_form.html",
]
PYTHON_FILES = [
    "properties/views.py", "enquiries/views.py", "core/views.py",
    "accounts/views.py", "accounts/otp.py", "accounts/forms.py",
]

_GETTEXT = re.compile(r"""\b(?:gettext|_)\(\s*u?(?P<q>['"])(?P<msg>(?:\\.|(?!(?P=q)).)*)(?P=q)\s*\)""")
_NGETTEXT = re.compile(
    r"""\bngettext\(\s*u?(?P<q1>['"])(?P<one>(?:\\.|(?!(?P=q1)).)*)(?P=q1)\s*,\s*u?(?P<q2>['"])(?P<many>(?:\\.|(?!(?P=q2)).)*)(?P=q2)"""
)


def _unescape(text):
    return bytes(text, "utf-8").decode("unicode_escape").encode("latin-1").decode("utf-8")


def template_messages():
    singular, plural = set(), set()
    base = Path(settings.BASE_DIR) / "templates"
    for name in PUBLIC_TEMPLATES:
        code = templatize((base / name).read_text(encoding="utf-8"), origin=str(base / name))
        for m in _NGETTEXT.finditer(code):
            plural.add((_unescape(m["one"]), _unescape(m["many"])))
        code = _NGETTEXT.sub("", code)
        for m in _GETTEXT.finditer(code):
            singular.add(_unescape(m["msg"]))
    return singular, plural


def python_messages():
    found = set()
    for name in PYTHON_FILES:
        code = (Path(settings.BASE_DIR) / name).read_text(encoding="utf-8")
        found.update(m["msg"].replace("\\'", "'") for m in _GETTEXT.finditer(code))
    return found


def form_messages():
    """Labels, help texts, placeholders and choices of the public forms."""
    from accounts.forms import (
        EmailCodeRequestForm,
        LoginForm,
        OTPCodeForm,
        PasswordResetCodeForm,
        RegistrationForm,
        ThrottledPasswordResetForm,
    )
    from core.forms import ContactForm
    from enquiries.forms import EnquiryForm
    from properties.forms import PropertySearchForm, ReportForm, SaveSearchForm

    found = set()
    forms = [PropertySearchForm(), SaveSearchForm(), ReportForm(), ContactForm(), LoginForm(), RegistrationForm(),
             OTPCodeForm(), EmailCodeRequestForm(), PasswordResetCodeForm(), ThrottledPasswordResetForm()]
    try:
        forms.append(EnquiryForm())
    except TypeError:
        pass
    for form in forms:
        for field in form.fields.values():
            for text in (field.label, field.help_text, field.widget.attrs.get("placeholder")):
                if text:
                    found.add(str(text))
            choices = getattr(field, "choices", None)
            if choices is not None and not hasattr(choices, "queryset"):
                stack = list(choices)
                while stack:
                    value, label = stack.pop()
                    if isinstance(label, (list, tuple)):
                        found.add(str(value))
                        stack.extend(label)
                    elif label:
                        found.add(str(label))
    return found


def data_messages():
    """Model choice labels and reference data (categories, amenities, places) shown to customers."""
    from enquiries.models import Enquiry
    from properties.models import Amenity, Category, Location, Property

    found = set()
    for choices in (Property.Purpose, Property.Furnishing, Property.Parking, Property.Availability,
                    Property.ContactMethod, Property.AreaUnit, Enquiry.Status):
        found.update(str(label) for label in choices.labels)
    for cat in Category.objects.all():
        found.add(cat.name)
        if cat.description:
            found.add(cat.description)
    found.update(Amenity.objects.values_list("name", flat=True))
    found.update(Location.objects.values_list("name", flat=True))
    from core.faq import FAQS
    for q, a in FAQS:
        found.update((q, a))
    return found


def all_messages():
    singular, plural = template_messages()
    singular |= python_messages() | form_messages() | data_messages()
    return {m for m in singular if m.strip()}, plural
