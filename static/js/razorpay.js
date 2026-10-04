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
      const rzp = new window.Razorpay({
        key: d.key, amount: d.amount, currency: "INR", order_id: d.order,
        name: d.name, description: d.description,
        prefill: { name: d.prefillName, email: d.prefillEmail, contact: d.prefillContact },
        theme: { color: "#172B4D" },
        handler: function (resp) {
          success.razorpay_order_id.value = resp.razorpay_order_id;
          success.razorpay_payment_id.value = resp.razorpay_payment_id;
          success.razorpay_signature.value = resp.razorpay_signature;
          btn.setAttribute("aria-busy", "true");
          success.submit();
        },
        modal: { ondismiss: function () { btn.removeAttribute("aria-busy"); } },
      });
      rzp.on("payment.failed", function (resp) {
        failed.description.value = (resp.error && resp.error.description) || "Payment failed";
        failed.submit();
      });
      rzp.open();
    });
  }
  if (document.readyState !== "loading") init(); else document.addEventListener("DOMContentLoaded", init);
})();
