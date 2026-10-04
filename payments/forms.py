from django import forms

from core.forms import BootstrapFormMixin


class ManualPaymentForm(BootstrapFormMixin, forms.Form):
    manual_reference = forms.CharField(
        max_length=80, min_length=6, label="UPI transaction ID / bank reference number",
        help_text="We'll verify this against our bank statement before activating your plan.",
    )
