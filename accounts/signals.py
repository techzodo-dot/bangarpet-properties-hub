from django.db.models.signals import post_save
from django.dispatch import receiver

from accounts.models import BrokerProfile, OwnerProfile, Role, User, UserProfile


@receiver(post_save, sender=User)
def ensure_profiles(sender, instance, created, raw=False, **kwargs):
    if raw:
        return
    UserProfile.objects.get_or_create(user=instance)
    if instance.role == Role.OWNER:
        OwnerProfile.objects.get_or_create(user=instance)
    elif instance.role == Role.BROKER and not BrokerProfile.objects.filter(user=instance).exists():
        BrokerProfile.objects.create(user=instance, agency_name=instance.full_name or "")
