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


def _request(method, path, **kwargs):
    if not is_configured():
        raise RazorpayError("Razorpay is not configured.")
    try:
        resp = requests.request(
            method, f"{settings.RAZORPAY_API_BASE}{path}",
            auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET), timeout=10, **kwargs,
        )
    except requests.RequestException as exc:
        raise RazorpayError(f"Could not reach Razorpay: {exc}") from exc
    if resp.status_code >= 400:
        try:
            detail = resp.json().get("error", {}).get("description", "")
        except ValueError:
            detail = ""
        raise RazorpayError(f"Razorpay rejected the request ({resp.status_code}). {detail}".strip())
    return resp.json()


# -- Recurring payments (Razorpay Subscriptions) ---------------------------
# https://razorpay.com/docs/payments/subscriptions/

def billing_cycle(days):
    """Map a plan's billing period in days to a Razorpay (period, interval)."""
    if days % 365 == 0:
        return "yearly", days // 365
    if days % 30 == 0:
        return "monthly", days // 30
    if days % 7 == 0:
        return "weekly", days // 7
    return "daily", max(days, 7)  # Razorpay needs at least 7 days for daily plans


def create_plan(name, amount_paise, days):
    period, interval = billing_cycle(days)
    data = _request("post", "/plans", json={
        "period": period, "interval": interval,
        "item": {"name": name[:60], "amount": amount_paise, "currency": "INR"},
    })
    if not data.get("id"):
        raise RazorpayError("Unexpected response from Razorpay.")
    return data["id"]


def create_subscription(plan_id, days, notes=None):
    """Start an auto-renewing subscription for about five years of cycles (the customer can cancel anytime)."""
    cycles = max(1, (5 * 365) // max(days, 1))
    data = _request("post", "/subscriptions", json={
        "plan_id": plan_id, "total_count": cycles, "quantity": 1, "customer_notify": 1, "notes": notes or {},
    })
    if not data.get("id"):
        raise RazorpayError("Unexpected response from Razorpay.")
    return data


def fetch_subscription(subscription_id):
    return _request("get", f"/subscriptions/{subscription_id}")


def subscription_invoices(subscription_id):
    return _request("get", "/invoices", params={"subscription_id": subscription_id, "count": 100}).get("items", [])


def cancel_subscription(subscription_id):
    """Stop future charges now. Time already paid for stays active on our side."""
    return _request("post", f"/subscriptions/{subscription_id}/cancel", json={"cancel_at_cycle_end": 0})


def _hmac(secret, message):
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def verify_payment_signature(order_id, payment_id, signature):
    if not (is_configured() and order_id and payment_id and signature):
        return False
    expected = _hmac(settings.RAZORPAY_KEY_SECRET, f"{order_id}|{payment_id}".encode())
    return hmac.compare_digest(expected, signature)


def verify_subscription_signature(payment_id, subscription_id, signature):
    """Checkout signature for the first payment of a subscription: HMAC(payment_id|subscription_id)."""
    if not (is_configured() and subscription_id and payment_id and signature):
        return False
    expected = _hmac(settings.RAZORPAY_KEY_SECRET, f"{payment_id}|{subscription_id}".encode())
    return hmac.compare_digest(expected, signature)


def verify_webhook_signature(body, signature):
    secret = settings.RAZORPAY_WEBHOOK_SECRET
    if not (secret and signature):
        return False
    return hmac.compare_digest(_hmac(secret, body), signature)
