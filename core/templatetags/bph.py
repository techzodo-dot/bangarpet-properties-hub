from decimal import Decimal, InvalidOperation

from django import template
from django.utils.http import urlencode

register = template.Library()


@register.filter
def inr(value):
    """Format a number with Indian digit grouping: 1234567 -> 12,34,567."""
    if value in (None, ""):
        return ""
    try:
        number = Decimal(value)
    except (InvalidOperation, TypeError, ValueError):
        return value
    negative = number < 0
    number = abs(number)
    whole = int(number)
    frac = number - whole
    s = str(whole)
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        s = ",".join(groups + [tail])
    if frac:
        s += f"{frac:.2f}"[1:]
    return ("-" if negative else "") + "₹" + s


@register.filter
def inr_short(value):
    """Compact price: 4500000 -> 45 L, 12000000 -> 1.2 Cr."""
    if value in (None, ""):
        return ""
    try:
        number = Decimal(value)
    except (InvalidOperation, TypeError, ValueError):
        return value
    if number >= 10_000_000:
        return f"₹{(number / 10_000_000):.2f}".rstrip("0").rstrip(".") + " Cr"
    if number >= 100_000:
        return f"₹{(number / 100_000):.2f}".rstrip("0").rstrip(".") + " L"
    return inr(number)


@register.simple_tag
def status_badge(obj, field="status"):
    from django.utils.html import format_html

    value = getattr(obj, field)
    label = getattr(obj, f"get_{field}_display")()
    return format_html('<span class="badge badge-status status-{}">{}</span>', value, label)


@register.simple_tag(takes_context=True)
def query_replace(context, **kwargs):
    """Return the current querystring with some keys replaced/removed (None removes)."""
    params = context["request"].GET.copy()
    for key, value in kwargs.items():
        params.pop(key, None)
        if value not in (None, ""):
            params[key] = value
    params.pop("page", None) if "page" not in kwargs else None
    return params.urlencode()


@register.filter
def initials(user):
    name = (getattr(user, "display_name", None) or str(user) or "?").strip()
    parts = [p for p in name.split() if p]
    return ("".join(p[0] for p in parts[:2]) or "?").upper()


@register.filter
def get_item(mapping, key):
    try:
        return mapping.get(key)
    except AttributeError:
        return None


@register.simple_tag
def qs(**kwargs):
    return urlencode({k: v for k, v in kwargs.items() if v not in (None, "")})
