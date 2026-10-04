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
    notify(user, Notification.Event.PAYMENT_CONFIRMED, "Payment received",
           f"We received your payment of ₹{payment.amount} for the {payment.plan.name} plan. Thank you!",
           reverse("dashboard:partner_payments"))
    notify(user, Notification.Event.SUBSCRIPTION_PURCHASED, f"{payment.plan.name} plan active",
           f"Your {payment.plan.name} plan is active until {timezone.localtime(sub.ends_at):%d %b %Y}. "
           f"You can now have up to {payment.plan.listing_limit} active listings.",
           reverse("dashboard:partner_subscription"))
    return payment, True


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
           reverse("dashboard:partner_subscription"))
    return payment
