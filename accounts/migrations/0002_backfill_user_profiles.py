from django.conf import settings
from django.db import migrations


def backfill_user_profiles(apps, schema_editor):
    app_label, model_name = settings.AUTH_USER_MODEL.split(".")
    user_model = apps.get_model(app_label, model_name)
    user_profile_model = apps.get_model("accounts", "UserProfile")

    for user in user_model.objects.all().iterator():
        user_profile_model.objects.get_or_create(
            user=user,
            defaults={"preferred_language": "pt"},
        )


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(backfill_user_profiles, migrations.RunPython.noop),
    ]

