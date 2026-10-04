from datetime import date, timedelta

from django import forms
from django.utils import timezone

from core.forms import BootstrapFormMixin, PhoneField
from enquiries.models import Enquiry, PropertyVisit


class EnquiryForm(BootstrapFormMixin, forms.Form):
    contact_name = forms.CharField(max_length=120, label="Your name")
    contact_phone = PhoneField(label="Mobile number")
    contact_email = forms.EmailField(required=False, label="Email")
    preferred_contact = forms.ChoiceField(choices=Enquiry.ContactMethod.choices, label="Preferred contact method")
    message = forms.CharField(
        max_length=2000, widget=forms.Textarea(attrs={"rows": 3}),
        initial="Hi, I'm interested in this property. Is it still available?",
    )
    request_visit = forms.BooleanField(required=False, label="I'd like to visit this property")
    preferred_date = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}), label="Preferred visit date")
    preferred_slot = forms.ChoiceField(required=False, choices=PropertyVisit.TimeSlot.choices, label="Preferred time")
    visit_note = forms.CharField(required=False, max_length=500, label="Note for the visit (optional)")

    def __init__(self, *args, user=None, prop=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.prop = prop
        if user is not None and not self.is_bound:
            self.initial.update({
                "contact_name": user.full_name,
                "contact_phone": user.phone,
                "contact_email": user.email,
                "preferred_contact": getattr(getattr(user, "profile", None), "preferred_contact", "phone")
                if getattr(getattr(user, "profile", None), "preferred_contact", "phone") in Enquiry.ContactMethod.values else "phone",
            })
        today = timezone.localdate()
        self.fields["preferred_date"].widget.attrs.update({
            "min": (today + timedelta(days=1)).isoformat(), "max": (today + timedelta(days=60)).isoformat(),
        })

    def clean_message(self):
        message = self.cleaned_data["message"].strip()
        if len(message) < 5:
            raise forms.ValidationError("Please write a short message.")
        return message

    def clean(self):
        data = super().clean()
        if data.get("request_visit"):
            d = data.get("preferred_date")
            today = timezone.localdate()
            if not d:
                self.add_error("preferred_date", "Choose a preferred date for the visit.")
            elif d <= today:
                self.add_error("preferred_date", "Choose a date from tomorrow onwards.")
            elif d > today + timedelta(days=60):
                self.add_error("preferred_date", "Choose a date within the next 60 days.")
            if not data.get("preferred_slot"):
                self.add_error("preferred_slot", "Choose a preferred time.")
        if data.get("preferred_contact") == "email" and not data.get("contact_email"):
            self.add_error("contact_email", "Enter your email or choose another contact method.")
        return data


class EnquiryStatusForm(BootstrapFormMixin, forms.Form):
    status = forms.ChoiceField(choices=[(s.value, s.label) for s in Enquiry.PARTNER_SETTABLE])
    note = forms.CharField(required=False, max_length=255, label="Message to customer (optional)")


class PartnerNoteForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = Enquiry
        fields = ["partner_note"]
        widgets = {"partner_note": forms.Textarea(attrs={"rows": 3})}
        labels = {"partner_note": "Private notes"}


class ScheduleVisitForm(BootstrapFormMixin, forms.Form):
    scheduled_date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}), label="Visit date")
    scheduled_time = forms.TimeField(widget=forms.TimeInput(attrs={"type": "time"}), label="Visit time")
    partner_note = forms.CharField(required=False, max_length=500, label="Message to customer (e.g. meeting point)")

    def clean(self):
        data = super().clean()
        d = data.get("scheduled_date")
        if d and d < date.today():
            self.add_error("scheduled_date", "Choose today or a future date.")
        if d and data.get("scheduled_time"):
            from datetime import datetime

            data["scheduled_at"] = timezone.make_aware(datetime.combine(d, data["scheduled_time"]))
        return data
