/* Opens Razorpay Checkout. The result is POSTed to the server, which verifies the
   signature before activating anything - the browser's word is never trusted. */
(function () {
  "use strict";
  function init() {
    const cfg = document.getElementById("rzp-config");
    const btn = document.getElementById("rzp-button");
    if (!cfg || !btn) return;
    const d = cfg.dataset;
    btn.addEventListener("click", function () {
      if (!window.Razorpay) { alert("Payment service could not load. Check your connection and try again."); return; }
      const success = document.getElementById("rzp-success");
      const failed = document.getElementById("rzp-failed");
      const options = {
        key: d.key, name: d.name, description: d.description,
        prefill: { name: d.prefillName, email: d.prefillEmail, contact: d.prefillContact },
        theme: { color: "#172B4D" },
        handler: function (resp) {
          // One-time payments return an order ID; auto-renewing ones a subscription ID.
          ["razorpay_order_id", "razorpay_subscription_id", "razorpay_payment_id", "razorpay_signature"].forEach(function (k) {
            if (success[k]) success[k].value = resp[k] || "";
          });
          btn.setAttribute("aria-busy", "true");
          success.submit();
        },
        modal: { ondismiss: function () { btn.removeAttribute("aria-busy"); } },
      };
      if (d.subscription) { options.subscription_id = d.subscription; }
      else { options.order_id = d.order; options.amount = d.amount; options.currency = "INR"; }
      const rzp = new window.Razorpay(options);
      rzp.on("payment.failed", function (resp) {
        failed.description.value = (resp.error && resp.error.description) || "Payment failed";
        failed.submit();
      });
      rzp.open();
    });
  }
  if (document.readyState !== "loading") init(); else document.addEventListener("DOMContentLoaded", init);
})();
