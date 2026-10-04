from django import forms
from django.utils.translation import get_language, gettext

from core.models import ContactMessage


def _tr(text):
    return gettext(str(text)) if text not in (None, "") else text


def _tr_choices(choices):
    out = []
    for value, label in choices:
        if isinstance(label, (list, tuple)):  # option group
            out.append((_tr(value), _tr_choices(label)))
        else:
            out.append((value, _tr(label)))
    return out
from core.validators import normalize_indian_phone


class BootstrapFormMixin:
    """Adds Bootstrap 5 classes to widgets so templates stay simple."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            widget = field.widget
            css = widget.attrs.get("class", "")
            if isinstance(widget, (forms.CheckboxInput,)):
                base = "form-check-input"
            elif isinstance(widget, (forms.CheckboxSelectMultiple, forms.RadioSelect)):
                base = ""
            elif isinstance(widget, (forms.Select, forms.SelectMultiple)):
                base = "form-select"
            else:
                base = "form-control"
            if base and base not in css:
                widget.attrs["class"] = f"{css} {base}".strip()
            if field.required and not isinstance(widget, (forms.CheckboxSelectMultiple, forms.RadioSelect)):
                widget.attrs.setdefault("required", "required")
        self.translate_fields()

    def translate_fields(self):
        """Show labels, help texts, placeholders and choices in the visitor's language (e.g. Kannada).

        Forms keep plain English strings; the translations live in locale/<lang>/LC_MESSAGES.
        """
        if (get_language() or "en").startswith("en"):
            return
        for field in self.fields.values():
            field.label = _tr(field.label)
            field.help_text = _tr(field.help_text)
            placeholder = field.widget.attrs.get("placeholder")
            if placeholder:
                field.widget.attrs["placeholder"] = _tr(placeholder)
            if isinstance(field, forms.ChoiceField) and not isinstance(field, forms.ModelChoiceField):
                field.choices = _tr_choices(field.choices)

    def add_invalid_classes(self):
        for name in self.errors:
            if name in self.fields:
                w = self.fields[name].widget
                w.attrs["class"] = (w.attrs.get("class", "") + " is-invalid").strip()

    def full_clean(self):
        super().full_clean()
        if self.is_bound:
            self.add_invalid_classes()


class PhoneField(forms.CharField):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("max_length", 16)
        super().__init__(*args, **kwargs)
        self.widget.attrs.update({"inputmode": "tel", "autocomplete": "tel", "placeholder": "10-digit mobile number"})

    def clean(self, value):
        value = super().clean(value)
        if not value:
            return value
        return normalize_indian_phone(value)


class ContactForm(BootstrapFormMixin, forms.ModelForm):
    phone = PhoneField(required=False)
    website = forms.CharField(required=False, widget=forms.HiddenInput, label="")  # honeypot

    class Meta:
        model = ContactMessage
        fields = ["name", "email", "phone", "subject", "message"]
        widgets = {"message": forms.Textarea(attrs={"rows": 5})}

    def clean_website(self):
        if self.cleaned_data.get("website"):
            raise forms.ValidationError("Invalid submission.")
        return ""
