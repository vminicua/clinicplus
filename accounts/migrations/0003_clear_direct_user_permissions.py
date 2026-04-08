from django.conf import settings
from django.db import migrations


def clear_direct_user_permissions(apps, schema_editor):
    app_label, model_name = settings.AUTH_USER_MODEL.split(".")
    user_model = apps.get_model(app_label, model_name)

    through_model = user_model.user_permissions.through
    through_model.objects.all().delete()


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0002_backfill_user_profiles"),
    ]

    operations = [
        migrations.RunPython(clear_direct_user_permissions, migrations.RunPython.noop),
    ]
