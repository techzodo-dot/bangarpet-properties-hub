"""Payment state machine. Subscriptions activate only after server-side verification."""
import logging

from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from core.audit import log_action
from notifications.models import Notification
from notifications.services import notify
from payments.models import Invoice, Payment
from subscriptions.services import activate_subscription

logger = logging.getLogger("bph")


@transaction.atomic
def mark_paid(payment_id, gateway_payment_id=None, signature="", raw=None, verified_by=None, source="checkout"):
    """Idempotently mark a payment as paid and activate the subscription.

    Returns (payment, newly_processed).
    """
    payment = Payment.objects.select_for_update().select_related("plan", "user").get(pk=payment_id)
    if payment.status == Payment.Status.PAID:
        return payment, False
    if payment.status not in (Payment.Status.CREATED, Payment.Status.FAILED, Payment.Status.PENDING_VERIFICATION):
        raise ValueError(f"Payment {payment.pk} cannot be marked paid from status {payment.status}.")
    now = timezone.now()
    if gateway_payment_id:
        payment.gateway_payment_id = gateway_payment_id
    if signature:
        payment.gateway_signature = signature
    if raw:
        payment.raw_response = raw
    payment.status = Payment.Status.PAID
    payment.paid_at = now
    payment.failure_reason = ""
    payment.verified_by = verified_by
    sub = activate_subscription(payment.user, payment.plan, amount_paid=payment.amount)
    payment.subscription = sub
    payment.save()
    user = payment.user
    profile_phone = user.phone
    Invoice.objects.create(
        payment=payment,
        number=f"BPH-INV-{now:%Y}-{payment.pk:06d}",
        billed_name=user.display_name,
        billed_email=user.email,
        billed_phone=profile_phone or "",
        description=f"{payment.plan.name} plan - {payment.plan.billing_period_days} days",
        amount=payment.amount,
        period_start=sub.starts_at,
        period_end=sub.ends_at,
    )
    log_action(verified_by or user, "payment.paid", payment, source=source, amount=str(payment.amount))
    plan = payment.plan
    notify(user, Notification.Event.PAYMENT_CONFIRMED, "Payment received",
           f"We received your payment of ₹{payment.amount} for the {plan.name} plan. Thank you!",
           plan_payments_url(plan))
    if plan.unlimited_contacts:
        benefit = "You can now see owners' phone numbers and WhatsApp on every listing."
    else:
        benefit = f"You can now have up to {plan.listing_limit} active listings."
    notify(user, Notification.Event.SUBSCRIPTION_PURCHASED, f"{plan.name} active",
           f"Your {plan.name} is active until {timezone.localtime(sub.ends_at):%d %b %Y}. {benefit}",
           plan_home_url(plan))
    return payment, True


def plan_home_url(plan):
    return reverse("payments:contact_pass") if plan.unlimited_contacts else reverse("dashboard:partner_subscription")


def plan_payments_url(plan):
    return reverse("payments:contact_pass") if plan.unlimited_contacts else reverse("dashboard:partner_payments")


@transaction.atomic
def mark_failed(payment_id, reason="", raw=None):
    payment = Payment.objects.select_for_update().get(pk=payment_id)
    if payment.status != Payment.Status.CREATED:
        return payment
    payment.status = Payment.Status.FAILED
    payment.failure_reason = (reason or "Payment failed")[:255]
    if raw:
        payment.raw_response = raw
    payment.save(update_fields=["status", "failure_reason", "raw_response", "updated_at"])
    notify(payment.user, Notification.Event.PAYMENT_FAILED, "Payment not completed",
           f"Your payment for the {payment.plan.name} plan was not completed. No plan change was made. You can try again anytime.",
           plan_home_url(payment.plan))
    return payment


# ---------------------------------------------------------------------------
# Auto-renewing plans (Razorpay Subscriptions), used by the customer Contact Pass
# ---------------------------------------------------------------------------
def razorpay_plan_for(plan):
    """Razorpay plan ID for ``plan`` at its current price, created on first use.

    Stored as "<mode>:<amount_paise>:<plan_id>" so a price change or a switch
    between test and live keys creates a fresh Razorpay plan.
    """
    from payments import razorpay

    amount = int(plan.effective_price * 100)
    mode = "test" if razorpay.is_test_mode() else "live"
    parts = (plan.razorpay_plan_id or "").split(":")
    if len(parts) == 3 and parts[0] == mode and parts[1] == str(amount):
        return parts[2]
    plan_id = razorpay.create_plan(f"{plan.name} - Bangarpet Property Hub", amount, plan.billing_period_days)
    plan.razorpay_plan_id = f"{mode}:{amount}:{plan_id}"
    plan.save(update_fields=["razorpay_plan_id", "updated_at"])
    return plan_id


def link_auto_renewal(sub, gateway_subscription_id):
    """Attach the Razorpay subscription to the plan period it pays for."""
    from subscriptions.models import Subscription

    Subscription.objects.filter(gateway_subscription_id=gateway_subscription_id).exclude(pk=sub.pk).update(
        gateway_subscription_id=None, auto_renew=False
    )
    sub.gateway_subscription_id = gateway_subscription_id
    sub.auto_renew = True
    sub.save(update_fields=["gateway_subscription_id", "auto_renew", "updated_at"])
    return sub


@transaction.atomic
def record_subscription_charge(gateway_subscription_id, payment_entity, source="webhook"):
    """Record one successful charge of an auto-renewing subscription (idempotent).

    The first charge normally arrives through checkout; later charges extend the
    plan by one billing period each.
    """
    from decimal import Decimal

    from subscriptions.models import Subscription

    gateway_payment_id = payment_entity.get("id")
    if not gateway_payment_id:
        return "no payment id"
    if Payment.objects.filter(gateway_payment_id=gateway_payment_id, status=Payment.Status.PAID).exists():
        return "already recorded"
    pending = (
        Payment.objects.select_for_update()
        .filter(gateway_subscription_id=gateway_subscription_id, status__in=[Payment.Status.CREATED, Payment.Status.FAILED])
        .order_by("created_at").first()
    )
    if pending is None:
        current = Subscription.objects.filter(gateway_subscription_id=gateway_subscription_id).select_related("plan", "user").first()
        if current is None:
            return "no matching subscription"
        amount = Decimal(payment_entity.get("amount") or int(current.plan.effective_price * 100)) / 100
        pending = Payment.objects.create(
            user=current.user, plan=current.plan, amount=amount, gateway=Payment.Gateway.RAZORPAY,
            gateway_subscription_id=gateway_subscription_id,
        )
    payment, _ = mark_paid(pending.pk, gateway_payment_id=gateway_payment_id, raw=payment_entity, source=source)
    link_auto_renewal(payment.subscription, gateway_subscription_id)
    return "renewed"


def stop_auto_renewal(gateway_subscription_id):
    from subscriptions.models import Subscription

    return Subscription.objects.filter(gateway_subscription_id=gateway_subscription_id, auto_renew=True).update(auto_renew=False)


def sync_auto_renewals():
    """Pick up renewal charges straight from Razorpay (a backup for missed webhooks).

    Checks auto-renewing plans that end within a day or ended in the last 3 days.
    """
    from datetime import timedelta

    from payments import razorpay
    from subscriptions.models import Subscription

    if not razorpay.is_configured():
        return "razorpay not configured"
    now = timezone.now()
    subs = Subscription.objects.filter(
        auto_renew=True, gateway_subscription_id__isnull=False,
        ends_at__lte=now + timedelta(days=1), ends_at__gte=now - timedelta(days=3),
    )
    renewed = stopped = 0
    for sub in subs:
        gid = sub.gateway_subscription_id
        try:
            for invoice in razorpay.subscription_invoices(gid):
                if invoice.get("status") == "paid" and invoice.get("payment_id"):
                    entity = {"id": invoice["payment_id"], "amount": invoice.get("amount_paid") or invoice.get("amount")}
                    if record_subscription_charge(gid, entity, source="sync") == "renewed":
                        renewed += 1
            status = razorpay.fetch_subscription(gid).get("status")
        except razorpay.RazorpayError as exc:
            logger.warning("Could not sync Razorpay subscription %s: %s", gid, exc)
            continue
        if status in ("cancelled", "completed", "expired", "halted"):
            stopped += stop_auto_renewal(gid)
    return f"{renewed} renewed, {stopped} stopped"
