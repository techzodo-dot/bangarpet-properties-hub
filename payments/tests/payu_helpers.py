"""Build PayU replies signed the way PayU signs them (with the test salt from config/settings/test.py)."""
import hashlib

from django.conf import settings


def payu_reply(payment, status="success", amount=None, mihpayid="403993715500000001", salt=None, **extra):
    data = {
        "key": settings.PAYU_MERCHANT_KEY, "txnid": payment.gateway_order_id, "status": status,
        "amount": amount if amount is not None else f"{payment.amount:.2f}",
        "productinfo": f"{payment.plan.name} plan", "firstname": payment.user.get_short_name(),
        "email": payment.user.email, "udf1": payment.uid.hex, "udf2": "", "udf3": "", "udf4": "", "udf5": "",
        "mihpayid": mihpayid, "mode": "UPI", **extra,
    }
    reverse = "|".join([salt or settings.PAYU_MERCHANT_SALT, data["status"]]) + "||||||"
    reverse += "|".join(data[f] for f in ("udf5", "udf4", "udf3", "udf2", "udf1"))
    reverse += "|" + "|".join([data["email"], data["firstname"], data["productinfo"], data["amount"], data["txnid"], data["key"]])
    data["hash"] = hashlib.sha512(reverse.encode()).hexdigest()
    return data
