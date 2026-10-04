"""Create or update the platform super admin from environment settings.

Reads ADMIN_USERNAME and ADMIN_PASSWORD (optionally ADMIN_NAME) from the
environment / .env file, or from --username / --password. Safe to run on every
deploy: an existing account with that login is updated, not duplicated.

    python manage.py ensure_admin
    python manage.py ensure_admin --skip-if-missing      # in release scripts
"""
import os

from django.contrib.auth import get_user_model, password_validation
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from accounts.models import Role


class Command(BaseCommand):
    help = "Create or update the super admin login from ADMIN_USERNAME / ADMIN_PASSWORD."

    def add_arguments(self, parser):
        parser.add_argument("--username", help="Login name (defaults to the ADMIN_USERNAME setting).")
        parser.add_argument("--password", help="Password (defaults to the ADMIN_PASSWORD setting).")
        parser.add_argument("--name", help="Display name (defaults to ADMIN_NAME or 'Site Admin').")
        parser.add_argument("--keep-password", action="store_true",
                            help="Do not reset the password if the account already exists.")
        parser.add_argument("--skip-if-missing", action="store_true",
                            help="Exit quietly when no username/password is configured.")

    def handle(self, *args, **opts):
        username = (opts["username"] or os.environ.get("ADMIN_USERNAME", "")).strip().lower()
        password = opts["password"] or os.environ.get("ADMIN_PASSWORD", "")
        name = (opts["name"] or os.environ.get("ADMIN_NAME", "")).strip() or "Site Admin"

        if not username or not password:
            if opts["skip_if_missing"]:
                self.stdout.write("ensure_admin: ADMIN_USERNAME / ADMIN_PASSWORD not set; skipped.")
                return
            raise CommandError("Set ADMIN_USERNAME and ADMIN_PASSWORD (in .env or the host's environment), "
                               "or pass --username and --password.")

        User = get_user_model()
        user = User.objects.filter(email__iexact=username).first()
        created = user is None
        if created:
            user = User(email=username, full_name=name)

        try:
            password_validation.validate_password(password, user)
        except ValidationError as exc:
            self.stderr.write(self.style.WARNING(
                "Warning: this password is weak (" + " ".join(exc.messages) + "). "
                "It is set as requested; change it from Management > Change password after first login."))

        user.full_name = user.full_name or name
        user.role = Role.ADMIN
        user.is_staff = True
        user.is_superuser = True
        user.is_active = True
        user.email_verified = True
        user.suspended_at = None
        if created or not opts["keep_password"]:
            user.set_password(password)
        user.save()

        action = "Created" if created else "Updated"
        self.stdout.write(self.style.SUCCESS(f"{action} super admin '{username}'. Sign in at /login/ then open /management/."))
