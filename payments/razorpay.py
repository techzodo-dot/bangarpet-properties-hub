"""Minimal Razorpay client: order creation and signature verification.

Secrets never leave the server. Only the public key id is sent to the browser.
Docs: https://razorpay.com/docs/payments/server-integration/
"""
import hashlib
import hmac

import requests
from django.conf import settings


class RazorpayError(Exception):
    pass


def is_configured():
    return bool(settings.RAZORPAY_KEY_ID and settings.RAZORPAY_KEY_SECRET)


def is_test_mode():
    return settings.RAZORPAY_KEY_ID.startswith("rzp_test_")


def create_order(amount_paise, receipt, notes=None):
    if not is_configured():
        raise RazorpayError("Razorpay is not configured.")
    try:
        resp = requests.post(
            f"{settings.RAZORPAY_API_BASE}/orders",
            auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET),
            json={"amount": amount_paise, "currency": "INR", "receipt": receipt[:40], "notes": notes or {}},
            timeout=10,
        )
    except requests.RequestException as exc:
        raise RazorpayError(f"Could not reach Razorpay: {exc}") from exc
    if resp.status_code >= 400:
        raise RazorpayError(f"Razorpay rejected the order ({resp.status_code}).")
    data = resp.json()
    if data.get("amount") != amount_paise or not data.get("id"):
        raise RazorpayError("Unexpected response from Razorpay.")
    return data


def _hmac(secret, message):
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def verify_payment_signature(order_id, payment_id, signature):
    if not (is_configured() and order_id and payment_id and signature):
        return False
    expected = _hmac(settings.RAZORPAY_KEY_SECRET, f"{order_id}|{payment_id}".encode())
    return hmac.compare_digest(expected, signature)


def verify_webhook_signature(body, signature):
    secret = settings.RAZORPAY_WEBHOOK_SECRET
    if not (secret and signature):
        return False
    return hmac.compare_digest(_hmac(secret, body), signature)
