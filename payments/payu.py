"""PayU India hosted checkout: request hashes, response checks and the verify API.

The browser is sent to PayU's payment page with a form signed by our merchant
salt. PayU posts the result back (to the return URL and the webhook) with a
reverse hash that only PayU and we can compute, so a result is trusted only
after that hash and the amount have been checked on the server.
The salt never leaves the server.
Docs: https://docs.payu.in/docs/prebuilt-checkout-page-integration
"""
import hashlib
import hmac
import re

import requests
from django.conf import settings

PAYMENT_URLS = {"test": "https://test.payu.in/_payment", "live": "https://secure.payu.in/_payment"}
VERIFY_URLS = {
    "test": "https://test.payu.in/merchant/postservice.php?form=2",
    "live": "https://info.payu.in/merchant/postservice.php?form=2",
}
UDF_FIELDS = ("udf1", "udf2", "udf3", "udf4", "udf5")


class PayUError(Exception):
    pass


def is_configured():
    return bool(settings.PAYU_MERCHANT_KEY and settings.PAYU_MERCHANT_SALT)


def mode():
    return "live" if settings.PAYU_MODE == "live" else "test"


def is_test_mode():
    return mode() == "test"


def payment_url():
    return PAYMENT_URLS[mode()]


def _sha512(text):
    return hashlib.sha512(text.encode()).hexdigest()


def _clean(value, limit):
    """PayU rejects some characters (notably the "|" used in hashes)."""
    return re.sub(r"[^A-Za-z0-9 @._\-]", "", str(value or ""))[:limit].strip()


def request_hash(params):
    """sha512(key|txnid|amount|productinfo|firstname|email|udf1..udf5||||||salt)."""
    parts = [params["key"], params["txnid"], params["amount"], params["productinfo"], params["firstname"], params["email"]]
    parts += [params.get(f, "") for f in UDF_FIELDS]
    return _sha512("|".join(parts) + "||||||" + settings.PAYU_MERCHANT_SALT)


def checkout_fields(*, txnid, amount, productinfo, firstname, email, phone, surl, furl, udf1=""):
    """Form fields for PayU's hosted payment page, including the request hash."""
    params = {
        "key": settings.PAYU_MERCHANT_KEY,
        "txnid": txnid,
        "amount": f"{amount:.2f}",
        "productinfo": _clean(productinfo, 100) or "Plan",
        "firstname": _clean(firstname, 60) or "Customer",
        "email": email,
        "phone": "".join(ch for ch in str(phone or "") if ch.isdigit())[-10:],
        "surl": surl,
        "furl": furl,
        "udf1": _clean(udf1, 255),
    }
    params["hash"] = request_hash(params)
    return params


def response_hash_valid(data):
    """Check the reverse hash PayU sends with every result.

    sha512([additionalCharges|]salt|status||||||udf5..udf1|email|firstname|productinfo|amount|txnid|key)
    """
    received = (data.get("hash") or "").lower()
    if not (is_configured() and received):
        return False
    parts = [settings.PAYU_MERCHANT_SALT, data.get("status", "")]
    reverse = "|".join(parts) + "||||||" + "|".join(data.get(f, "") for f in reversed(UDF_FIELDS))
    reverse += "|" + "|".join([data.get("email", ""), data.get("firstname", ""), data.get("productinfo", ""),
                               data.get("amount", ""), data.get("txnid", ""), data.get("key", "")])
    if data.get("additionalCharges"):
        reverse = f"{data['additionalCharges']}|{reverse}"
    return hmac.compare_digest(_sha512(reverse), received) and data.get("key") == settings.PAYU_MERCHANT_KEY


def verify_payment(txnid):
    """Ask PayU for the current state of a transaction. Returns PayU's details dict for ``txnid``."""
    if not is_configured():
        raise PayUError("PayU is not configured.")
    key, command = settings.PAYU_MERCHANT_KEY, "verify_payment"
    payload = {"key": key, "command": command, "var1": txnid,
               "hash": _sha512(f"{key}|{command}|{txnid}|{settings.PAYU_MERCHANT_SALT}")}
    try:
        resp = requests.post(VERIFY_URLS[mode()], data=payload, timeout=15)
        data = resp.json()
    except (requests.RequestException, ValueError) as exc:
        raise PayUError(f"Could not reach PayU: {exc}") from exc
    details = (data.get("transaction_details") or {}).get(txnid)
    if not details:
        raise PayUError(data.get("msg") or "Transaction not found at PayU.")
    return details
