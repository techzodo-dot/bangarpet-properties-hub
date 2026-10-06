"""Owner contact metering for customers.

Customers can unlock a limited number of owners' phone/WhatsApp contacts for
free each calendar month (``PlatformSetting.free_contacts_per_month``). After
that they need the Contact Pass, a customer plan with ``unlimited_contacts``.
A listing that has been unlocked once stays unlocked for that customer.
Owners, brokers and admins are not metered.
"""
from django.db import transaction
from django.utils import timezone

from accounts.models import Role
from core.models import PlatformSetting
from properties.models import ContactUnlock, Property


def metering_enabled():
    return PlatformSetting.load().contact_limit_enabled


def free_limit():
    return PlatformSetting.load().free_contacts_per_month


def is_metered(user):
    return (
        metering_enabled() and user.is_authenticated and user.role == Role.CUSTOMER and not user.is_platform_admin
    )


def month_start():
    return timezone.localtime().replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def free_used(user):
    return ContactUnlock.objects.filter(
        user=user, via=ContactUnlock.Via.FREE, created_at__gte=month_start()
    ).count()


def free_left(user):
    return max(free_limit() - free_used(user), 0)


def active_pass(user):
    if not user.is_authenticated:
        return None
    return (
        user.subscriptions.current().filter(plan__unlimited_contacts=True)
        .select_related("plan").order_by("-ends_at").first()
    )


def has_contact_details(prop):
    return prop.contact_visibility != Property.ContactVisibility.HIDDEN and bool(prop.contact_phone or prop.whatsapp_number)


def contact_state(user, prop, *, allowed):
    """How the contact box on a listing should behave for this visitor.

    ``allowed`` is the listing's own rule (owner's visibility choice, owner/admin).
    """
    state = {"visible": allowed, "metered": False, "needs_login": False, "needs_pass": False,
             "free_left": None, "free_limit": None, "has_pass": False}
    if not has_contact_details(prop):
        return state
    is_owner = user.is_authenticated and user.pk == prop.owner_id
    if not metering_enabled() or is_owner or (user.is_authenticated and user.is_platform_admin):
        return state
    if not user.is_authenticated:
        # Anyone could otherwise sign out to skip the limit, so sign-in is needed first.
        return {**state, "visible": False, "needs_login": True}
    if not is_metered(user) or not allowed:
        return state
    pass_sub = active_pass(user)
    unlocked = ContactUnlock.objects.filter(user=user, property=prop).exists()
    left = free_left(user)
    return {**state, "metered": True, "visible": bool(pass_sub or unlocked), "has_pass": bool(pass_sub),
            "free_left": left, "free_limit": free_limit(), "needs_pass": not (pass_sub or unlocked) and left == 0}


@transaction.atomic
def unlock(user, prop):
    """Unlock a listing's contact for a metered customer.

    Returns "already", "pass" or "free" on success, or None when the free
    contacts for this month are used up and there is no Contact Pass.
    """
    from accounts.models import User

    User.objects.select_for_update().filter(pk=user.pk).first()  # serialise a user's unlocks
    if ContactUnlock.objects.filter(user=user, property=prop).exists():
        return "already"
    if active_pass(user):
        ContactUnlock.objects.create(user=user, property=prop, via=ContactUnlock.Via.PASS)
        return "pass"
    if free_left(user) > 0:
        ContactUnlock.objects.create(user=user, property=prop, via=ContactUnlock.Via.FREE)
        return "free"
    return None
