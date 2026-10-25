from decimal import Decimal
from django.db import migrations


def enable_gst(apps, schema_editor):
    Setting = apps.get_model("core", "PlatformSetting")
    Setting.objects.using(schema_editor.connection.alias).update_or_create(
        pk=1, defaults={"gst_percent": Decimal("18.00")},
    )
    from django.core.cache import cache
    cache.delete("bph:platform-settings")


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0008_alter_platformsetting_gst_percent"),
        ("payments", "0004_payment_gst_amount_payment_gst_percent_and_more"),
    ]
    operations = [migrations.RunPython(enable_gst, migrations.RunPython.noop)]
