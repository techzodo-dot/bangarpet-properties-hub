"""Customer Contact Pass: unlimited owner contacts, paid monthly with auto-renewal.

The first payment goes through Razorpay Checkout with a ``subscription_id``;
later charges arrive by webhook (``subscription.charged``) or the daily sync.
"""
import logging

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import IntegrityError
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from accounts.models import Role
from core import ratelimit
from core.audit import log_action
from payments import razorpay
from payments.models import Payment
from payments.services import link_auto_renewal, mark_failed, mark_paid, razorpay_plan_for, stop_auto_renewal
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
    plan = contact_pass_plan()
    current = contacts.active_pass(user)
    recent = ContactUnlock.objects.filter(user=user).select_related("property")[:10]
    payments = Payment.objects.filter(user=user, plan__unlimited_contacts=True).exclude(status=Payment.Status.CREATED)[:12]
    return render(request, "payments/contact_pass.html", {
        "plan": plan, "current": current, "free_left": contacts.free_left(user), "free_limit": contacts.free_limit(),
        "metering": contacts.metering_enabled(), "recent_unlocks": recent, "payments": payments,
        "online": razorpay.is_configured(), "next": _next(request), "active": "contact_pass",
    })


@login_required
@require_POST
def subscribe(request):
    blocked = _customer_only(request)
    if blocked:
        return blocked
    user = request.user
    plan = contact_pass_plan()
    here = reverse("payments:contact_pass")
    if plan is None:
        messages.error(request, _("The Contact Pass is not available right now."))
        return redirect(here)
    current = contacts.active_pass(user)
    if current and current.auto_renew:
        messages.info(request, _("Your Contact Pass is already active and renews automatically."))
        return redirect(here)
    if not razorpay.is_configured():
        messages.info(request, _("Online payment is not available yet. Please contact us on WhatsApp to get the Contact Pass."))
        return redirect(here)
    if not ratelimit.check_and_hit("payment", f"user:{user.pk}"):
        messages.error(request, _("Too many payment attempts. Please try again later."))
        return redirect(here)
    try:
        rzp_plan = razorpay_plan_for(plan)
        rzp_sub = razorpay.create_subscription(
            rzp_plan, plan.billing_period_days, notes={"user_id": str(user.pk), "plan": plan.slug},
        )
    except razorpay.RazorpayError as exc:
        logger.warning("Razorpay subscription creation failed: %s", exc)
        messages.error(request, _("We couldn't start the payment. Please try again in a few minutes."))
        return redirect(here)
    payment = Payment.objects.create(
        user=user, plan=plan, amount=plan.effective_price, gateway=Payment.Gateway.RAZORPAY,
        gateway_subscription_id=rzp_sub["id"], raw_response={"subscription": rzp_sub},
    )
    log_action(request, "payment.subscription_started", payment, subscription=rzp_sub["id"])
    url = reverse("payments:contact_pass_pay", args=[payment.uid])
    nxt = _next(request)
    return redirect(f"{url}?next={nxt}" if nxt else url)


@login_required
def pay(request, uid):
    payment = get_object_or_404(Payment.objects.select_related("plan"), uid=uid, user=request.user)
    if payment.status == Payment.Status.PAID:
        return redirect("payments:receipt", uid=payment.uid)
    if payment.status != Payment.Status.CREATED or not payment.gateway_subscription_id:
        messages.info(request, _("This payment session has ended. Please start again."))
        return redirect("payments:contact_pass")
    return render(request, "payments/pay.html", {
        "payment": payment, "key_id": settings.RAZORPAY_KEY_ID, "test_mode": razorpay.is_test_mode(),
        "base_template": "dashboard/customer_base.html", "active": "contact_pass", "recurring": True,
        "verify_url": reverse("payments:contact_pass_verify"), "failed_url": reverse("payments:contact_pass_failed"),
        "next": _next(request),
    })


@login_required
@require_POST
def verify(request):
    subscription_id = request.POST.get("razorpay_subscription_id", "")
    payment_id = request.POST.get("razorpay_payment_id", "")
    signature = request.POST.get("razorpay_signature", "")
    payment = get_object_or_404(
        Payment, gateway_subscription_id=subscription_id, user=request.user, status__in=[Payment.Status.CREATED, Payment.Status.PAID]
    )
    if not razorpay.verify_subscription_signature(payment_id, subscription_id, signature):
        log_action(request, "payment.signature_invalid", payment)
        messages.error(request, _("We could not verify this payment. If money was deducted, it will be confirmed automatically or refunded by Razorpay."))
        return redirect("payments:contact_pass")
    try:
        payment, _created = mark_paid(payment.pk, gateway_payment_id=payment_id, signature=signature, source="checkout")
    except IntegrityError:
        payment = Payment.objects.get(gateway_payment_id=payment_id)
    if payment.subscription:
        link_auto_renewal(payment.subscription, subscription_id)
    messages.success(request, _("Payment successful. Your Contact Pass is active - you can now see every owner's phone and WhatsApp."))
    nxt = _next(request)
    return redirect(nxt or "payments:contact_pass")


@login_required
@require_POST
def failed(request):
    subscription_id = request.POST.get("razorpay_subscription_id", "")
    payment = Payment.objects.filter(gateway_subscription_id=subscription_id, user=request.user).first()
    if payment:
        mark_failed(payment.pk, request.POST.get("description", "")[:200] or "Payment was not completed")
    messages.error(request, _("The payment was not completed. No money was taken. You can try again."))
    return redirect("payments:contact_pass")


@login_required
@require_POST
def cancel(request):
    current = contacts.active_pass(request.user)
    if not current or not current.auto_renew or not current.gateway_subscription_id:
        messages.info(request, _("Auto-renewal is already off."))
        return redirect("payments:contact_pass")
    try:
        razorpay.cancel_subscription(current.gateway_subscription_id)
    except razorpay.RazorpayError as exc:
        logger.warning("Razorpay cancel failed for %s: %s", current.gateway_subscription_id, exc)
        messages.error(request, _("We couldn't turn off auto-renewal right now. Please try again in a few minutes."))
        return redirect("payments:contact_pass")
    stop_auto_renewal(current.gateway_subscription_id)
    log_action(request, "subscription.auto_renew_cancelled", current)
    messages.success(request, _("Auto-renewal is off. Your Contact Pass stays active until %(date)s.")
                     % {"date": f"{timezone.localtime(current.ends_at):%d %b %Y}"})
    return redirect("payments:contact_pass")
