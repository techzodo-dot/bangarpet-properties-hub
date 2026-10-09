"""Subscription rules: plan entitlements, listing limits and activation."""
from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from subscriptions.models import Subscription, SubscriptionPlan


def default_plan():
    plan = SubscriptionPlan.objects.filter(is_default=True, is_active=True).first()
    if plan is None:
        plan = SubscriptionPlan.objects.filter(price=0, is_active=True).order_by("display_order").first()
    return plan


def contact_pass_plan():
    """The customer plan with unlimited owner contacts (cheapest active one)."""
    return (
        SubscriptionPlan.objects.filter(is_active=True, unlimited_contacts=True)
        .order_by("display_order", "price").first()
    )


def current_subscription(user):
    """The user's current listing-plan subscription (the customer Contact Pass is separate, see contacts.active_pass)."""
    return (
        user.subscriptions.current().filter(plan__unlimited_contacts=False)
        .select_related("plan").order_by("-ends_at").first()
    )


def current_plan(user):
    sub = current_subscription(user)
    return sub.plan if sub else default_plan()


ADMIN_LISTING_LIMIT = 10_000  # platform admins are not limited by plans
ADMIN_LISTING_DAYS = 90


def listing_limit(user):
    if user.is_platform_admin:
        return ADMIN_LISTING_LIMIT
    plan = current_plan(user)
    return plan.listing_limit if plan else 1


def used_listing_slots(user, exclude=None):
    from properties.models import Property

    qs = Property.objects.filter(owner=user).occupying_slots()
    if exclude is not None:
        qs = qs.exclude(pk=exclude.pk)
    return qs.count()


def can_occupy_slot(user, prop=None):
    """True when the user can submit/publish one more listing (or ``prop`` already holds a slot)."""
    if prop is not None and prop.status in prop.SLOT_STATUSES:
        return True
    return used_listing_slots(user, exclude=prop) < listing_limit(user)


def listing_terms(user):
    """(duration_days, priority) applied when a listing is approved or renewed."""
    if user.is_platform_admin:
        return ADMIN_LISTING_DAYS, 0
    plan = current_plan(user)
    if plan is None:
        return 30, 0
    priority = plan.visibility_priority if plan.has_priority_visibility else 0
    return plan.listing_duration_days, priority


def _carry_over_days(current, new_plan, now):
    """Days of ``new_plan`` worth the unused, paid-for part of ``current`` (0 for free or granted plans)."""
    total = (current.ends_at - current.starts_at).total_seconds() if current.starts_at else 0
    if total <= 0 or not current.amount_paid or new_plan.price <= 0:
        return 0
    unused_value = Decimal(current.amount_paid) * Decimal((current.ends_at - now).total_seconds() / total)
    new_daily = Decimal(new_plan.price) / Decimal(new_plan.billing_period_days)
    return int(unused_value / new_daily)


@transaction.atomic
def activate_subscription(user, plan, amount_paid=0, granted_by=None, notes=""):
    """Activate (or extend) a subscription.

    Listing plans and the customer Contact Pass are tracked separately, so one
    never replaces the other. Buying the current plan again extends it; buying
    a different plan starts it now, and the unused paid days of the old plan are
    credited as extra days on the new one.

    The returned subscription carries ``period_start``/``period_end``: the
    period this activation paid for (used on the invoice).
    """
    now = timezone.now()
    same_kind = Subscription.objects.select_for_update().filter(
        user=user, status=Subscription.Status.ACTIVE, plan__unlimited_contacts=plan.unlimited_contacts,
    )
    current = same_kind.filter(ends_at__gt=now).select_related("plan").order_by("-ends_at").first()
    if current and current.plan_id == plan.pk:
        start = current.ends_at
        current.ends_at = current.ends_at + timedelta(days=plan.billing_period_days)
        current.amount_paid = (current.amount_paid or 0) + amount_paid
        current.expiry_reminder_sent_at = None
        current.save(update_fields=["ends_at", "amount_paid", "expiry_reminder_sent_at", "updated_at"])
        current.period_start, current.period_end = start, current.ends_at
        return current
    extra_days = 0
    if current:
        extra_days = _carry_over_days(current, plan, now)
        if extra_days:
            note = f"Includes {extra_days} day{'s' if extra_days != 1 else ''} credited from the unused {current.plan.name} plan."
            notes = f"{notes} {note}".strip()[:255]
        same_kind.update(status=Subscription.Status.SUPERSEDED, updated_at=now)
    sub = Subscription.objects.create(
        user=user, plan=plan, status=Subscription.Status.ACTIVE, starts_at=now,
        ends_at=now + timedelta(days=plan.billing_period_days + extra_days), amount_paid=amount_paid,
        granted_by=granted_by, notes=notes,
    )
    sub.period_start, sub.period_end = sub.starts_at, sub.ends_at
    sub.carried_over_days = extra_days
    return sub
