"""Customer Contact Pass: unlimited owner contacts for one billing period, paid through PayU."""
import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from accounts.models import Role
from core import ratelimit
from core.audit import log_action
from core.models import PlatformSetting
from payments import payu
from payments.models import Payment
from payments.services import start_payu_payment
from properties import contacts
from properties.models import ContactUnlock
from subscriptions.services import contact_pass_plan

logger = logging.getLogger("bph")


def _customer_only(request):
    """Return a redirect for non-customers, else None."""
    if request.user.role != Role.CUSTOMER:
        messages.info(request, _("The Contact Pass is for tenants and buyers. Owner and broker accounts can always see contacts."))
        return redirect(request.user.dashboard_url_name())
    return None


def _next(request):
    from core.utils import safe_next_url

    return safe_next_url(request, None) or ""


@login_required
def contact_pass(request):
    blocked = _customer_only(request)
    if blocked:
        return blocked
    user = request.user
    recent = ContactUnlock.objects.filter(user=user).select_related("property")[:10]
    payments = Payment.objects.filter(user=user, plan__unlimited_contacts=True).exclude(status=Payment.Status.CREATED)[:12]
    return render(request, "payments/contact_pass.html", {
        "plan": contact_pass_plan(), "current": contacts.active_pass(user), "free_left": contacts.free_left(user),
        "free_limit": contacts.free_limit(), "metering": contacts.metering_enabled(), "recent_unlocks": recent,
        "payments": payments, "online": payu.is_configured(), "next": _next(request), "active": "contact_pass",
    })


@login_required
@require_POST
def subscribe(request):
    """Buy (or extend) the Contact Pass for one billing period."""
    blocked = _customer_only(request)
    if blocked:
        return blocked
    user = request.user
    plan = contact_pass_plan()
    here = reverse("payments:contact_pass")
    if plan is None:
        messages.error(request, _("The Contact Pass is not available right now."))
        return redirect(here)
    if not payu.is_configured():
        if PlatformSetting.load().allow_manual_payments:
            return redirect("payments:manual", slug=plan.slug)
        messages.info(request, _("Online payment is not available yet. Please contact us on WhatsApp to get the Contact Pass."))
        return redirect(here)
    if not ratelimit.check_and_hit("payment", f"user:{user.pk}"):
        messages.error(request, _("Too many payment attempts. Please try again later."))
        return redirect(here)
    payment = start_payu_payment(user, plan, next_url=_next(request))
    log_action(request, "payment.started", payment, gateway="payu")
    return redirect("payments:pay", uid=payment.uid)
