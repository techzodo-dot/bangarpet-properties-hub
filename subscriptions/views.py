from django.shortcuts import render

from subscriptions.models import SubscriptionPlan


def pricing(request):
    plans = SubscriptionPlan.objects.filter(is_active=True)
    return render(request, "subscriptions/pricing.html", {"plans": plans})
