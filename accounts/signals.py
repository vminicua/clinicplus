from django.contrib.auth import get_user_model
from django.db.models.signals import post_migrate
from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import UserProfile
from .utils import sync_default_roles


User = get_user_model()


@receiver(post_save, sender=User)
def ensure_user_profile(sender, instance, **kwargs):
    UserProfile.objects.get_or_create(user=instance)


@receiver(post_migrate)
def create_default_roles(sender, **kwargs):
    if sender.name != "clinic":
        return
    sync_default_roles()
