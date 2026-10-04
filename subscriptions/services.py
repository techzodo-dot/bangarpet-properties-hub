"""Subscription rules: plan entitlements, listing limits and activation."""
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from subscriptions.models import Subscription, SubscriptionPlan


def default_plan():
    plan = SubscriptionPlan.objects.filter(is_default=True, is_active=True).first()
    if plan is None:
        plan = SubscriptionPlan.objects.filter(price=0, is_active=True).order_by("display_order").first()
    return plan


def current_subscription(user):
    return user.subscriptions.current().select_related("plan").order_by("-ends_at").first()


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


@transaction.atomic
def activate_subscription(user, plan, amount_paid=0, granted_by=None, notes=""):
    """Activate (or extend) a subscription. Buying the current plan again extends it;
    buying a different plan starts immediately and supersedes the current one."""
    now = timezone.now()
    current = (
        Subscription.objects.select_for_update()
        .filter(user=user, status=Subscription.Status.ACTIVE, ends_at__gt=now)
        .order_by("-ends_at")
        .first()
    )
    if current and current.plan_id == plan.pk:
        current.ends_at = current.ends_at + timedelta(days=plan.billing_period_days)
        current.amount_paid = (current.amount_paid or 0) + amount_paid
        current.expiry_reminder_sent_at = None
        current.save(update_fields=["ends_at", "amount_paid", "expiry_reminder_sent_at", "updated_at"])
        return current
    if current:
        Subscription.objects.filter(user=user, status=Subscription.Status.ACTIVE).update(
            status=Subscription.Status.SUPERSEDED
        )
    return Subscription.objects.create(
        user=user, plan=plan, status=Subscription.Status.ACTIVE, starts_at=now,
        ends_at=now + timedelta(days=plan.billing_period_days), amount_paid=amount_paid,
        granted_by=granted_by, notes=notes,
    )
