import json
import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.http import HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from core import ratelimit
from core.audit import log_action
from core.models import PlatformSetting
from core.permissions import partner_required
from notifications.models import Notification
from notifications.services import notify_admins
from payments import payu, upi
from payments.forms import ManualPaymentForm, PlanRequestForm
from payments.models import Invoice, Payment, WebhookEvent
from payments.services import (
    payu_checkout_fields, plan_home_url, plan_payments_url, process_payu_result, start_payu_payment,
)
from subscriptions.models import SubscriptionPlan

logger = logging.getLogger("bph")


def _get_plan(user, slug):
    plan = get_object_or_404(SubscriptionPlan, slug=slug, is_active=True)
    if not plan.available_for(user) or plan.effective_price <= 0:
        raise PermissionDenied("This plan cannot be purchased.")
    return plan


@partner_required
@require_POST
def checkout(request, slug):
    plan = _get_plan(request.user, slug)
    if not payu.is_configured():
        # No online gateway yet: fall back to UPI/bank transfer or a plan request.
        if PlatformSetting.load().allow_manual_payments:
            return redirect("payments:manual", slug=plan.slug)
        return redirect("payments:request", slug=plan.slug)
    if not ratelimit.check_and_hit("payment", f"user:{request.user.pk}"):
        messages.error(request, "Too many payment attempts. Please try again later.")
        return redirect("dashboard:partner_subscription")
    payment = start_payu_payment(request.user, plan)
    log_action(request, "payment.started", payment, gateway="payu")
    return redirect("payments:pay", uid=payment.uid)


@login_required
def pay(request, uid):
    """Confirm the amount, then hand over to PayU's secure payment page."""
    payment = get_object_or_404(Payment.objects.select_related("plan", "user"), uid=uid, user=request.user)
    if payment.status == Payment.Status.PAID:
        return redirect("payments:receipt", uid=payment.uid)
    home = plan_home_url(payment.plan)
    if payment.status != Payment.Status.CREATED or payment.gateway != Payment.Gateway.PAYU or not payu.is_configured():
        messages.info(request, "This payment session has ended. Please start again.")
        return redirect(home)
    customer = payment.plan.unlimited_contacts
    return render(request, "payments/pay.html", {
        "payment": payment, "fields": payu_checkout_fields(payment), "payu_url": payu.payment_url(),
        "test_mode": payu.is_test_mode(), "cancel_url": home,
        "base_template": "dashboard/customer_base.html" if customer else "dashboard/partner_base.html",
        "active": "contact_pass" if customer else "subscription",
    })


def _result_redirect(request, payment, result):
    if payment is None:
        messages.error(request, "We could not confirm this payment. If money was deducted, it will be confirmed shortly or refunded by PayU.")
        return redirect("core:home")
    home = plan_home_url(payment.plan)
    if result in ("paid", "already paid"):
        if payment.plan.unlimited_contacts:
            messages.success(request, "Payment successful. Your Contact Pass is active - you can now see every owner's phone and WhatsApp.")
            return redirect((payment.raw_response or {}).get("next") or home)
        messages.success(request, f"Payment successful. Your {payment.plan.name} plan is now active.")
        return redirect("payments:receipt", uid=payment.uid)
    if result == "pending":
        messages.info(request, "Your payment is being confirmed by the bank. Your plan activates as soon as it is confirmed.")
        return redirect(home)
    messages.error(request, "The payment was not completed. No plan change was made. You can try again.")
    return redirect(home)


@csrf_exempt
@require_POST
def payu_return(request):
    """PayU sends the customer back here (success and failure). The result is trusted only after the hash check.

    This is a cross-site POST, so the visitor's session cookie may be missing: the
    payment is found by its transaction ID, never by the signed-in user.
    """
    payment, result = process_payu_result(request.POST.dict(), source="return")
    if payment is not None:
        log_action(payment.user, "payment.payu_return", payment, result=result)
    return _result_redirect(request, payment, result)


@csrf_exempt
@require_POST
def payu_webhook(request):
    """Server-to-server result from PayU (Dashboard -> Webhooks). Hash-verified and idempotent."""
    if request.content_type == "application/json":
        try:
            data = {k: str(v) for k, v in json.loads(request.body or b"{}").items()}
        except (ValueError, AttributeError):
            return HttpResponseBadRequest("invalid json")
    else:
        data = request.POST.dict()
    if not payu.response_hash_valid(data):
        return HttpResponseBadRequest("invalid hash")
    event_id = f"{data.get('txnid')}:{data.get('status')}:{data.get('mihpayid')}"
    try:
        with transaction.atomic():
            record = WebhookEvent.objects.create(gateway="payu", event_id=event_id[:100],
                                                 event_type=(data.get("status") or "")[:60],
                                                 payload={k: v for k, v in data.items() if k != "hash"})
    except IntegrityError:
        return HttpResponse("duplicate")
    _payment, result = process_payu_result(data, source="webhook")
    record.result = result
    record.save(update_fields=["result"])
    return HttpResponse("ok")


MAX_PENDING_MANUAL = 3


@login_required
def manual_payment(request, slug):
    """Pay straight to our UPI ID (or by bank transfer), then submit the UPI reference.

    Works for listing plans (owners/brokers) and the Contact Pass (customers).
    The plan activates when an admin approves the payment in Management -> Payments.
    """
    site = PlatformSetting.load()
    if not site.allow_manual_payments:
        raise PermissionDenied("Manual payments are not enabled.")
    plan = _get_plan(request.user, slug)
    customer = plan.unlimited_contacts
    payments_url = plan_payments_url(plan)
    form = ManualPaymentForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        pending = Payment.objects.filter(user=request.user, status=Payment.Status.PENDING_VERIFICATION)
        reference = form.cleaned_data["manual_reference"]
        if pending.filter(manual_reference__iexact=reference).exists():
            messages.info(request, "You have already submitted this reference. We'll activate your plan once it is verified.")
            return redirect(payments_url)
        if pending.count() >= MAX_PENDING_MANUAL:
            messages.error(request, "You already have payments awaiting verification.")
            return redirect(payments_url)
        payment = Payment.objects.create(
            user=request.user, plan=plan, amount=plan.effective_price, gateway=Payment.Gateway.MANUAL,
            status=Payment.Status.PENDING_VERIFICATION, manual_reference=reference,
        )
        log_action(request, "payment.manual_submitted", payment)
        notify_admins(Notification.Event.ADMIN_NOTICE, "UPI payment to verify",
                      f"{request.user.display_name} paid {plan.effective_price} INR for {plan.name}. "
                      f"UPI reference: {payment.manual_reference}. Check it in the bank/UPI app, then approve it.",
                      reverse("adminpanel:payments") + "?status=pending_verification")
        what = "Contact Pass" if customer else f"{plan.name} plan"
        messages.success(request, f"Thank you. Your {what} will be activated as soon as our team verifies the payment.")
        return redirect(payments_url)
    link = upi.pay_link(site, plan.effective_price, upi.reference_note(request.user, plan))
    return render(request, "payments/manual.html", {
        "form": form, "plan": plan, "site": site, "upi_link": link, "upi_qr": upi.qr_svg(link),
        "cancel_url": plan_home_url(plan),
        "base_template": "dashboard/customer_base.html" if customer else "dashboard/partner_base.html",
        "active": "contact_pass" if customer else "subscription",
    })


MAX_OPEN_REQUESTS = 3


@partner_required
def request_plan(request, slug):
    """Ask the team to activate a plan when online payment is not available.

    Creates a payment awaiting verification; an admin collects the payment
    (UPI, bank transfer or cash) and approves it, which activates the plan.
    """
    plan = _get_plan(request.user, slug)
    form = PlanRequestForm(request.POST or None, initial={"phone": request.user.phone})
    if request.method == "POST" and form.is_valid():
        open_requests = Payment.objects.filter(user=request.user, status=Payment.Status.PENDING_VERIFICATION)
        if open_requests.filter(plan=plan).exists():
            messages.info(request, f"You have already requested the {plan.name} plan. Our team will contact you shortly.")
            return redirect("dashboard:partner_payments")
        if open_requests.count() >= MAX_OPEN_REQUESTS:
            messages.error(request, "You already have requests awaiting our team. Please wait for them to be processed.")
            return redirect("dashboard:partner_payments")
        phone = form.cleaned_data["phone"]
        payment = Payment.objects.create(
            user=request.user, plan=plan, amount=plan.effective_price, gateway=Payment.Gateway.MANUAL,
            status=Payment.Status.PENDING_VERIFICATION, manual_reference=f"Plan request - call {phone}"[:80],
        )
        log_action(request, "payment.plan_requested", payment, phone=phone)
        note = form.cleaned_data["note"]
        notify_admins(Notification.Event.ADMIN_NOTICE, f"Plan request: {plan.name}",
                      f"{request.user.display_name} ({phone}) wants the {plan.name} plan ({plan.effective_price} INR)."
                      + (f" Note: {note}" if note else "") + " Collect the payment, then approve it to activate the plan.",
                      reverse("adminpanel:payments") + "?status=pending_verification")
        messages.success(request, f"Request sent. Our team will call you on {phone} to complete the payment and activate your {plan.name} plan.")
        return redirect("dashboard:partner_payments")
    return render(request, "payments/request.html", {
        "form": form, "plan": plan, "base_template": "dashboard/partner_base.html", "active": "subscription",
    })


def _can_view(user, payment):
    return user.is_authenticated and (user.pk == payment.user_id or user.is_platform_admin)


def receipt(request, uid):
    payment = get_object_or_404(Payment.objects.select_related("plan", "user", "subscription"), uid=uid)
    if not _can_view(request.user, payment):
        raise PermissionDenied
    invoice = Invoice.objects.filter(payment=payment).first()
    return render(request, "payments/receipt.html", {"payment": payment, "invoice": invoice, "site": PlatformSetting.load()})
