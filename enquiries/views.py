from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from core import ratelimit
from core.permissions import member_required
from enquiries.forms import EnquiryForm
from enquiries.services import EnquiryError, submit_enquiry
from properties.models import Property


@member_required
@require_POST
def create_enquiry(request, pk):
    prop = get_object_or_404(Property.objects.public().select_related("owner"), pk=pk)
    form = EnquiryForm(request.POST, user=request.user, prop=prop)
    if not ratelimit.check_and_hit("enquiry", ratelimit.request_ident(request)):
        messages.error(request, _("You've sent many enquiries in a short time. Please try again later."))
        return redirect(prop.get_absolute_url())
    if form.is_valid():
        try:
            enquiry, visit = submit_enquiry(request.user, prop, form.cleaned_data)
        except EnquiryError as exc:
            messages.error(request, _(str(exc)))
            return redirect(prop.get_absolute_url())
        if visit:
            messages.success(request, _("Your visit request has been sent. You'll be notified when the owner confirms a time."))
        else:
            messages.success(request, _("Your enquiry has been sent. The owner/broker will contact you soon."))
        return redirect("dashboard:customer_enquiries")
    return render(request, "enquiries/enquiry_form_page.html", {"form": form, "prop": prop})
