from core.models import AuditLog
from core.ratelimit import get_client_ip


def log_action(request_or_user, action, target=None, **details):
    """Record a sensitive action in the audit log.

    ``request_or_user`` may be an HttpRequest (actor and IP are taken from it)
    or a User / None for system actions.
    """
    actor = None
    ip = None
    if hasattr(request_or_user, "META"):
        user = getattr(request_or_user, "user", None)
        actor = user if user is not None and user.is_authenticated else None
        ip = get_client_ip(request_or_user)
    elif request_or_user is not None:
        actor = request_or_user
    target_type = target_id = target_repr = ""
    if target is not None:
        target_type = target._meta.label_lower
        target_id = str(target.pk)
        target_repr = str(target)[:255]
    clean = {k: (v if isinstance(v, (str, int, float, bool, type(None), list, dict)) else str(v)) for k, v in details.items()}
    return AuditLog.objects.create(
        actor=actor, action=action, target_type=target_type, target_id=target_id,
        target_repr=target_repr, details=clean, ip_address=ip,
    )
