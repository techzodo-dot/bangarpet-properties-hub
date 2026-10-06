import hashlib
import json
import logging

from django.conf import settings
from django.contrib import messages
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
from payments import razorpay
from payments.forms import ManualPaymentForm, PlanRequestForm
from payments.models import Invoice, Payment, WebhookEvent
from payments.services import mark_failed, mark_paid, record_subscription_charge, stop_auto_renewal
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
    if not razorpay.is_configured():
        # No online gateway yet: fall back to UPI/bank transfer or a plan request.
        if PlatformSetting.load().allow_manual_payments:
            return redirect("payments:manual", slug=plan.slug)
        return redirect("payments:request", slug=plan.slug)
    if not ratelimit.check_and_hit("payment", f"user:{request.user.pk}"):
        messages.error(request, "Too many payment attempts. Please try again later.")
        return redirect("dashboard:partner_subscription")
    payment = Payment.objects.create(user=request.user, plan=plan, amount=plan.effective_price, gateway=Payment.Gateway.RAZORPAY)
    try:
        order = razorpay.create_order(
            payment.amount_paise, receipt=f"bph-{payment.uid.hex[:24]}",
            notes={"payment_uid": payment.uid.hex, "user_id": str(request.user.pk), "plan": plan.slug},
        )
    except razorpay.RazorpayError as exc:
        logger.warning("Razorpay order creation failed: %s", exc)
        payment.status = Payment.Status.FAILED
        payment.failure_reason = "Could not create payment order"
        payment.save(update_fields=["status", "failure_reason", "updated_at"])
        messages.error(request, "We couldn't start the payment. Please try again in a few minutes.")
        return redirect("dashboard:partner_subscription")
    payment.gateway_order_id = order["id"]
    payment.save(update_fields=["gateway_order_id", "updated_at"])
    return redirect("payments:pay", uid=payment.uid)


@partner_required
def pay(request, uid):
    payment = get_object_or_404(Payment.objects.select_related("plan"), uid=uid, user=request.user)
    if payment.status == Payment.Status.PAID:
        return redirect("payments:receipt", uid=payment.uid)
    if payment.status != Payment.Status.CREATED or not payment.gateway_order_id:
        messages.info(request, "This payment session has ended. Please start again.")
        return redirect("dashboard:partner_subscription")
    return render(request, "payments/pay.html", {
        "payment": payment, "key_id": settings.RAZORPAY_KEY_ID, "test_mode": razorpay.is_test_mode(),
        "base_template": "dashboard/partner_base.html", "active": "subscription",
    })


@partner_required
@require_POST
def verify(request):
    order_id = request.POST.get("razorpay_order_id", "")
    payment_id = request.POST.get("razorpay_payment_id", "")
    signature = request.POST.get("razorpay_signature", "")
    payment = get_object_or_404(Payment, gateway_order_id=order_id, user=request.user)
    if not razorpay.verify_payment_signature(order_id, payment_id, signature):
        log_action(request, "payment.signature_invalid", payment)
        messages.error(request, "We could not verify this payment. If money was deducted, it will be confirmed automatically or refunded by Razorpay. Contact support with your payment ID.")
        return redirect("dashboard:partner_payments")
    try:
        mark_paid(payment.pk, gateway_payment_id=payment_id, signature=signature, source="checkout")
    except IntegrityError:
        messages.error(request, "This payment has already been recorded.")
        return redirect("dashboard:partner_payments")
    messages.success(request, f"Payment successful. Your {payment.plan.name} plan is now active.")
    return redirect("payments:receipt", uid=payment.uid)


@partner_required
@require_POST
def failed(request):
    order_id = request.POST.get("razorpay_order_id", "")
    payment = Payment.objects.filter(gateway_order_id=order_id, user=request.user).first()
    if payment:
        reason = request.POST.get("description", "")[:200] or "Payment was not completed"
        mark_failed(payment.pk, reason)
    messages.error(request, "The payment was not completed. No money was taken for a plan change. You can try again.")
    return redirect("dashboard:partner_subscription")


@partner_required
def manual_payment(request, slug):
    site = PlatformSetting.load()
    if not site.allow_manual_payments:
        raise PermissionDenied("Manual payments are not enabled.")
    plan = _get_plan(request.user, slug)
    form = ManualPaymentForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        if Payment.objects.filter(user=request.user, status=Payment.Status.PENDING_VERIFICATION).count() >= 3:
            messages.error(request, "You already have payments awaiting verification.")
            return redirect("dashboard:partner_payments")
        payment = Payment.objects.create(
            user=request.user, plan=plan, amount=plan.effective_price, gateway=Payment.Gateway.MANUAL,
            status=Payment.Status.PENDING_VERIFICATION, manual_reference=form.cleaned_data["manual_reference"],
        )
        log_action(request, "payment.manual_submitted", payment)
        notify_admins(Notification.Event.ADMIN_NOTICE, "Manual payment to verify",
                      f"{request.user.display_name} submitted reference {payment.manual_reference} for {plan.name}.",
                      reverse("adminpanel:payments") + "?status=pending_verification")
        messages.success(request, "Thank you. Your plan will be activated once our team verifies the payment.")
        return redirect("dashboard:partner_payments")
    return render(request, "payments/manual.html", {
        "form": form, "plan": plan, "site": site, "base_template": "dashboard/partner_base.html", "active": "subscription",
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


@csrf_exempt
@require_POST
def razorpay_webhook(request):
    """Server-to-server confirmation from Razorpay. Signature-verified and idempotent."""
    body = request.body
    if not razorpay.verify_webhook_signature(body, request.headers.get("X-Razorpay-Signature", "")):
        return HttpResponseBadRequest("invalid signature")
    try:
        data = json.loads(body)
    except ValueError:
        return HttpResponseBadRequest("invalid json")
    event_type = data.get("event", "")
    event_id = request.headers.get("X-Razorpay-Event-Id") or hashlib.sha256(body).hexdigest()
    try:
        with transaction.atomic():
            record = WebhookEvent.objects.create(gateway="razorpay", event_id=event_id[:100], event_type=event_type[:60], payload=data)
    except IntegrityError:
        return HttpResponse("duplicate")

    entity = ((data.get("payload") or {}).get("payment") or {}).get("entity") or {}
    if event_type.startswith("subscription."):
        sub_entity = ((data.get("payload") or {}).get("subscription") or {}).get("entity") or {}
        sub_id = sub_entity.get("id", "")
        if event_type == "subscription.charged" and sub_id:
            record.result = record_subscription_charge(sub_id, entity, source="webhook")
        elif event_type in ("subscription.cancelled", "subscription.completed", "subscription.halted") and sub_id:
            record.result = f"auto-renew stopped ({stop_auto_renewal(sub_id)})"
        else:
            record.result = "ignored"
        record.save(update_fields=["result"])
        return HttpResponse("ok")
    order_id = entity.get("order_id")
    payment = Payment.objects.filter(gateway_order_id=order_id).first() if order_id else None
    result = "ignored"
    if payment is None:
        result = "no matching payment"
    elif event_type in ("payment.captured", "order.paid"):
        if entity.get("amount") != payment.amount_paise or entity.get("currency") != "INR":
            result = "amount mismatch"
            logger.error("Webhook amount mismatch for payment %s", payment.pk)
        elif entity.get("status") not in ("captured", None) and event_type == "payment.captured":
            result = f"status {entity.get('status')}"
        else:
            _, processed = mark_paid(payment.pk, gateway_payment_id=entity.get("id"), raw=entity, source="webhook")
            result = "paid" if processed else "already paid"
    elif event_type == "payment.failed":
        mark_failed(payment.pk, (entity.get("error_description") or "Payment failed"), raw=entity)
        result = "failed"
    record.result = result
    record.save(update_fields=["result"])
    return HttpResponse("ok")
