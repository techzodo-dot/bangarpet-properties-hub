from django.shortcuts import render

from subscriptions import services as subs
from subscriptions.models import SubscriptionPlan


def pricing(request):
    user = request.user
    is_partner = user.is_authenticated and user.is_partner
    # The customer Contact Pass has its own page; this page lists the listing plans.
    plans = list(SubscriptionPlan.objects.filter(is_active=True, unlimited_contacts=False))
    for plan in plans:
        roles = {r.strip() for r in plan.for_roles.split(",") if r.strip()}
        # Visitors sign up as an owner unless the plan is only for brokers.
        plan.signup_type = "owner" if "owner" in roles or not roles else "broker"
        plan.can_choose = is_partner and plan.available_for(user)
    ctx = {"plans": plans}
    if is_partner:
        ctx.update(current=subs.current_subscription(user), current_plan=subs.current_plan(user))
    return render(request, "subscriptions/pricing.html", ctx)
