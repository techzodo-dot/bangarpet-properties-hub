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
# PayU hosted checkout
# ---------------------------------------------------------------------------
def start_payu_payment(user, plan, next_url=""):
    """Create a payment and the PayU transaction ID it will be paid under."""
    payment = Payment.objects.create(
        user=user, plan=plan, amount=plan.effective_price, gateway=Payment.Gateway.PAYU,
        raw_response={"next": next_url} if next_url else {},
    )
    payment.gateway_order_id = f"BPH{payment.uid.hex[:20].upper()}"  # PayU allows up to 25 characters
    payment.save(update_fields=["gateway_order_id", "updated_at"])
    return payment


def payu_checkout_fields(payment):
    from django.conf import settings

    from payments import payu

    user = payment.user
    return_url = settings.SITE_URL + reverse("payments:payu_return")
    return payu.checkout_fields(
        txnid=payment.gateway_order_id, amount=payment.amount, productinfo=f"{payment.plan.name} plan",
        firstname=user.get_short_name(), email=user.email, phone=user.phone,
        surl=return_url, furl=return_url, udf1=payment.uid.hex,
    )


def process_payu_result(data, source="return"):
    """Apply a result posted by PayU (return URL or webhook). Returns (payment or None, result).

    Nothing changes unless PayU's reverse hash is valid and the amount matches.
    """
    from decimal import Decimal, InvalidOperation

    from payments import payu

    if not payu.response_hash_valid(data):
        logger.warning("PayU %s with an invalid hash for txnid %s", source, data.get("txnid"))
        return None, "invalid hash"
    payment = Payment.objects.filter(gateway_order_id=data.get("txnid"), gateway=Payment.Gateway.PAYU).select_related("plan").first()
    if payment is None:
        return None, "no matching payment"
    status = (data.get("status") or "").lower()
    if status == "success":
        try:
            amount = Decimal(data.get("amount") or "0")
        except InvalidOperation:
            amount = Decimal("0")
        if amount != payment.amount:
            logger.error("PayU amount mismatch for payment %s: %s != %s", payment.pk, amount, payment.amount)
            return payment, "amount mismatch"
        payment, processed = mark_paid(payment.pk, gateway_payment_id=data.get("mihpayid") or None,
                                       raw=_merged_raw(payment, data), source=source)
        return payment, "paid" if processed else "already paid"
    if status in ("failure", "failed", "usercancelled", "dropped", "bounced"):
        reason = data.get("error_Message") or data.get("field9") or "Payment was not completed"
        return mark_failed(payment.pk, reason[:200], raw=_merged_raw(payment, data)), "failed"
    return payment, "pending"


def _merged_raw(payment, data):
    """Keep what we stored at checkout (e.g. where to send the customer back) next to PayU's reply."""
    return {**(payment.raw_response or {}), "payu": {k: v for k, v in data.items() if k != "hash"}}


def reconcile_payu_payments():
    """Ask PayU about recent unfinished payments (covers a closed browser or a missed webhook)."""
    from datetime import timedelta
    from decimal import Decimal

    from payments import payu

    if not payu.is_configured():
        return "payu not configured"
    now = timezone.now()
    pending = Payment.objects.filter(
        gateway=Payment.Gateway.PAYU, status=Payment.Status.CREATED,
        created_at__lte=now - timedelta(minutes=30), created_at__gte=now - timedelta(days=3),
    ).exclude(gateway_order_id__isnull=True)
    paid = failed = 0
    for payment in pending:
        try:
            details = payu.verify_payment(payment.gateway_order_id)
        except payu.PayUError as exc:
            logger.info("PayU verify for %s: %s", payment.gateway_order_id, exc)
            continue
        status = (details.get("status") or "").lower()
        if status == "success" and Decimal(str(details.get("amt") or details.get("amount") or "0")) == payment.amount:
            mark_paid(payment.pk, gateway_payment_id=details.get("mihpayid") or None,
                      raw=_merged_raw(payment, details), source="reconcile")
            paid += 1
        elif status in ("failure", "failed", "usercancelled", "dropped", "bounced"):
            mark_failed(payment.pk, details.get("error_Message") or "Payment was not completed", raw=_merged_raw(payment, details))
            failed += 1
    return f"{paid} paid, {failed} failed"
