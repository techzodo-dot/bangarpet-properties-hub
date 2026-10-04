"""Role-based access control enforced on the server for every request."""
from functools import wraps

from django.contrib.auth.mixins import AccessMixin
from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied

from accounts.models import PARTNER_ROLES, Role


def _check(user, roles):
    if not user.is_authenticated or not user.is_active:
        return False
    if roles == "admin":
        return user.is_platform_admin
    return user.role in roles


class RoleRequiredMixin(AccessMixin):
    """Deny access unless the signed-in user has one of ``allowed_roles``."""

    allowed_roles = ()

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        if not _check(request.user, self.allowed_roles):
            raise PermissionDenied("You do not have access to this page.")
        return super().dispatch(request, *args, **kwargs)


class MemberRequiredMixin(RoleRequiredMixin):
    """Any signed-in non-admin member (customers, owners and brokers can all act as customers)."""

    allowed_roles = (Role.CUSTOMER, Role.OWNER, Role.BROKER)


class PartnerRequiredMixin(RoleRequiredMixin):
    allowed_roles = PARTNER_ROLES


class AdminRequiredMixin(RoleRequiredMixin):
    allowed_roles = "admin"


def role_required(roles):
    def decorator(view):
        @wraps(view)
        def wrapper(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect_to_login(request.get_full_path())
            if not _check(request.user, roles):
                raise PermissionDenied("You do not have access to this page.")
            return view(request, *args, **kwargs)

        return wrapper

    return decorator


member_required = role_required((Role.CUSTOMER, Role.OWNER, Role.BROKER))
partner_required = role_required(PARTNER_ROLES)
# Owners, brokers and platform admins can create and manage their own listings.
lister_required = role_required((*PARTNER_ROLES, Role.ADMIN))
admin_required = role_required("admin")
