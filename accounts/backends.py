from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend


class EmailBackend(ModelBackend):
    """Authenticate with a case-insensitive email address."""

    def authenticate(self, request, username=None, password=None, email=None, **kwargs):
        identifier = (email or username or "").strip().lower()
        if not identifier or password is None:
            return None
        User = get_user_model()
        try:
            user = User.objects.get(email__iexact=identifier)
        except User.DoesNotExist:
            User().set_password(password)  # equalise timing for unknown users
            return None
        if user.check_password(password) and self.user_can_authenticate(user):
            return user
        return None
