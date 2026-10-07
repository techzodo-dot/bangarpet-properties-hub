"""UPI pay links and QR codes for direct (manual) payments.

The customer pays straight to the site's UPI ID from any UPI app, then submits
the UPI reference; an admin checks it against the bank statement and approves
it. No gateway is involved, so nothing here can confirm a payment by itself.
Spec: NPCI "UPI Linking Specs" (upi://pay deep link).
"""
from urllib.parse import quote

import segno
from django.utils.safestring import mark_safe


def reference_note(user, plan):
    """Short note pre-filled in the UPI app so the payment can be matched to the account."""
    return f"BPH {plan.slug} U{user.pk}"[:50]


def pay_link(site, amount, note):
    """``upi://pay`` link that opens any UPI app with the payee, amount and note filled in."""
    if not site.upi_id:
        return ""
    params = {
        "pa": site.upi_id,
        "pn": site.upi_payee_name or site.site_name,
        "am": f"{amount:.2f}",
        "cu": "INR",
        "tn": note,
    }
    return "upi://pay?" + "&".join(f"{k}={quote(str(v), safe='@.-_')}" for k, v in params.items())


def qr_svg(link):
    """Inline SVG QR code of a UPI link, for scanning from a phone."""
    if not link:
        return ""
    svg = segno.make(link, error="m").svg_inline(scale=5, dark="#0b1b33", light="#ffffff", border=3)
    return mark_safe(svg.replace("<svg ", '<svg role="img" aria-label="UPI QR code" ', 1))
