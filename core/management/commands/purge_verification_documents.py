from django.core.management.base import BaseCommand
from django.utils import timezone

from accounts.models import VerificationDocument


class Command(BaseCommand):
    help = "Delete verification document files whose retention period has ended."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        qs = VerificationDocument.objects.filter(retain_until__lte=timezone.now(), file_purged_at__isnull=True).exclude(file="")
        count = qs.count()
        if options["dry_run"]:
            self.stdout.write(f"{count} document(s) would be purged.")
            return
        for doc in qs:
            doc.purge_file()
        self.stdout.write(self.style.SUCCESS(f"Purged {count} document file(s)."))
