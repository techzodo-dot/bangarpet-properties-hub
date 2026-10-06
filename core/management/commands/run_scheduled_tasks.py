"""Periodic maintenance. Schedule with cron, e.g. every hour:

    15 * * * * cd /path/to/app && venv/bin/python manage.py run_scheduled_tasks

Every task is idempotent and safe to run repeatedly.
"""
from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.http import QueryDict
from django.urls import reverse
from django.utils import timezone

from accounts.models import BrokerProfile, OwnerProfile, VerificationDocument, VerificationStatus
from core.models import PlatformSetting
from notifications.models import Notification
from notifications.services import notify
from properties.forms import PropertySearchForm
from properties.models import Property, SavedSearch
from properties.services import search_properties
from payments.services import plan_home_url, sync_auto_renewals
from subscriptions.models import Subscription

TASKS = ["listings", "subscriptions", "verifications", "documents", "saved_searches"]


class Command(BaseCommand):
    help = "Expire listings/subscriptions, send reminders, process saved-search alerts and purge old documents."

    def add_arguments(self, parser):
        parser.add_argument("--only", choices=TASKS, action="append", help="Run only the given task(s).")

    def handle(self, *args, **options):
        tasks = options.get("only") or TASKS
        for task in tasks:
            result = getattr(self, f"task_{task}")()
            self.stdout.write(f"{task}: {result}")

    # -- listings -----------------------------------------------------------
    def task_listings(self):
        now = timezone.now()
        site = PlatformSetting.load()
        expired = 0
        for prop in Property.objects.filter(status=Property.Status.ACTIVE, expires_at__lte=now).select_related("owner"):
            prop.status = Property.Status.EXPIRED
            prop.save(update_fields=["status", "updated_at"])
            expired += 1
            notify(prop.owner, Notification.Event.LISTING_EXPIRED, f"Listing expired: {prop.reference}",
                   f"\"{prop.title}\" has expired and is no longer visible. Renew it from your dashboard.",
                   reverse("dashboard:partner_property_manage", args=[prop.pk]))
        reminded = 0
        window = now + timedelta(days=site.expiry_reminder_days)
        for prop in Property.objects.filter(status=Property.Status.ACTIVE, expires_at__gt=now, expires_at__lte=window,
                                            expiry_reminder_sent_at__isnull=True).select_related("owner"):
            notify(prop.owner, Notification.Event.LISTING_EXPIRING, f"Listing expiring soon: {prop.reference}",
                   f"\"{prop.title}\" expires on {timezone.localtime(prop.expires_at):%d %b %Y}. Renew it to keep it live.",
                   reverse("dashboard:partner_property_manage", args=[prop.pk]))
            prop.expiry_reminder_sent_at = now
            prop.save(update_fields=["expiry_reminder_sent_at"])
            reminded += 1
        return f"{expired} expired, {reminded} reminders"

    # -- subscriptions ------------------------------------------------------
    def task_subscriptions(self):
        # Record auto-renewal charges first so renewed plans are not expired.
        renewals = sync_auto_renewals()
        now = timezone.now()
        expired = 0
        for sub in Subscription.objects.filter(status=Subscription.Status.ACTIVE, ends_at__lte=now).select_related("user", "plan"):
            sub.status = Subscription.Status.EXPIRED
            sub.save(update_fields=["status", "updated_at"])
            expired += 1
            if sub.plan.unlimited_contacts:
                body = ("Your Contact Pass has ended. You still get free owner contacts every month; "
                        "get the pass again for unlimited contacts.")
            else:
                body = ("Your plan has expired and your account is back on the free plan. Existing live listings stay "
                        "live until they expire; renew your plan to add more.")
            notify(sub.user, Notification.Event.SUBSCRIPTION_EXPIRED, f"{sub.plan.name} expired", body,
                   plan_home_url(sub.plan))
        reminded = 0
        for sub in Subscription.objects.filter(status=Subscription.Status.ACTIVE, ends_at__gt=now,
                                               ends_at__lte=now + timedelta(days=5), auto_renew=False,
                                               expiry_reminder_sent_at__isnull=True).select_related("user", "plan"):
            keep = "unlimited owner contacts" if sub.plan.unlimited_contacts else "your listing limit"
            notify(sub.user, Notification.Event.SUBSCRIPTION_EXPIRING, f"{sub.plan.name} ends soon",
                   f"Your {sub.plan.name} ends on {timezone.localtime(sub.ends_at):%d %b %Y}. Renew to keep {keep}.",
                   plan_home_url(sub.plan))
            sub.expiry_reminder_sent_at = now
            sub.save(update_fields=["expiry_reminder_sent_at"])
            reminded += 1
        return f"{expired} expired, {reminded} reminders; renewals: {renewals}"

    # -- verifications ------------------------------------------------------
    def task_verifications(self):
        now = timezone.now()
        count = 0
        for model in (OwnerProfile, BrokerProfile):
            for profile in model.objects.filter(verification_status=VerificationStatus.VERIFIED,
                                                verification_expires_at__lte=now).select_related("user"):
                profile.verification_status = VerificationStatus.EXPIRED
                profile.save(update_fields=["verification_status", "updated_at"])
                notify(profile.user, Notification.Event.VERIFICATION_UPDATE, "Verification expired",
                       "Your verification has expired. Upload current documents to get verified again.",
                       reverse("dashboard:partner_profile"))
                count += 1
        return f"{count} verifications expired"

    # -- document retention -------------------------------------------------
    def task_documents(self):
        now = timezone.now()
        purged = 0
        for doc in VerificationDocument.objects.filter(retain_until__lte=now, file_purged_at__isnull=True).exclude(file=""):
            doc.purge_file()
            purged += 1
        return f"{purged} document files purged (retention {settings.VERIFICATION_DOC_RETENTION_DAYS} days)"

    # -- saved search alerts --------------------------------------------------
    def task_saved_searches(self):
        now = timezone.now()
        sent = 0
        for search in SavedSearch.objects.filter(notify=True, user__is_active=True).select_related("user"):
            since = search.last_notified_at or search.created_at
            if now - since < timedelta(hours=20):
                continue
            form = PropertySearchForm(QueryDict(search.query_string))
            form.is_valid()
            matches = search_properties(form.cleaned_data).filter(published_at__gt=since).count()
            search.last_notified_at = now
            search.save(update_fields=["last_notified_at"])
            if matches:
                notify(search.user, Notification.Event.ADMIN_NOTICE, f"{matches} new listing{'s' if matches > 1 else ''} for \"{search.name}\"",
                       f"New properties matching your saved search \"{search.name}\" were published.",
                       search.get_absolute_url())
                sent += 1
        return f"{sent} alerts sent"
