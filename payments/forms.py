from django import forms

from core.forms import BootstrapFormMixin, PhoneField


class ManualPaymentForm(BootstrapFormMixin, forms.Form):
    manual_reference = forms.CharField(
        max_length=80, min_length=6, label="UPI transaction ID / bank reference number",
        help_text="We'll verify this against our bank statement before activating your plan.",
    )


class PlanRequestForm(BootstrapFormMixin, forms.Form):
    phone = PhoneField(label="Phone number to call you on")
    note = forms.CharField(
        required=False, max_length=300, label="Message (optional)",
        widget=forms.Textarea(attrs={"rows": 3, "placeholder": "Best time to call, preferred payment method..."}),
    )
