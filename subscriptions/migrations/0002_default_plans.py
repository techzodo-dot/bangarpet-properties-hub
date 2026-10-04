"""Initial subscription plans. Prices are examples and editable in /management/."""
from django.db import migrations

PLANS = [
    dict(name="Free", slug="free", price=0, listing_limit=1, listing_duration_days=30, is_default=True,
         description="Get started with one listing.", display_order=0, for_roles="owner,broker",
         features="1 active property listing\nStandard visibility\nEnquiry inbox"),
    dict(name="Basic", slug="basic", price=299, listing_limit=5, listing_duration_days=30,
         description="For owners with a few properties.", display_order=1, for_roles="owner,broker",
         features="Up to 5 active listings\nStandard enquiry management\nVisit scheduling"),
    dict(name="Pro", slug="pro", price=599, listing_limit=15, listing_duration_days=45,
         has_performance_stats=True, has_priority_visibility=True, visibility_priority=1,
         description="Performance insights and priority placement.", display_order=2, for_roles="owner,broker",
         features="Up to 15 active listings\nListing performance statistics\nPriority visibility in default sorting"),
    dict(name="Broker", slug="broker", price=999, listing_limit=50, listing_duration_days=60,
         has_performance_stats=True, has_priority_visibility=True, has_advanced_enquiry_tools=True,
         visibility_priority=2, description="Manage a full portfolio of listings.", display_order=3,
         for_roles="broker",
         features="Up to 50 active listings\nMultiple listing management\nAdvanced enquiry management\nListing performance statistics"),
]


def load(apps, schema_editor):
    Plan = apps.get_model("subscriptions", "SubscriptionPlan")
    for data in PLANS:
        slug = data.pop("slug")
        Plan.objects.get_or_create(slug=slug, defaults=data)


class Migration(migrations.Migration):
    dependencies = [("subscriptions", "0001_initial")]
    operations = [migrations.RunPython(load, migrations.RunPython.noop)]
