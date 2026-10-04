/* Bangarpet Property Hub - progressive enhancements (the site works without JS). */
(function () {
  "use strict";

  function getCookie(name) {
    const match = document.cookie.match(new RegExp("(^|;\\s*)(" + name + ")=([^;]*)"));
    return match ? decodeURIComponent(match[3]) : null;
  }
  function csrfToken() {
    const input = document.querySelector("input[name=csrfmiddlewaretoken]");
    return (input && input.value) || getCookie("bph_csrftoken") || getCookie("csrftoken");
  }

  // Prevent accidental double submissions on forms marked data-once.
  document.addEventListener("submit", function (event) {
    const form = event.target;
    if (!form.matches("form[data-once]")) return;
    if (form.dataset.submitted === "1") { event.preventDefault(); return; }
    form.dataset.submitted = "1";
    const btn = event.submitter || form.querySelector("[type=submit]");
    if (btn) { btn.setAttribute("aria-busy", "true"); }
    // Re-enable after a while in case the user navigates back.
    setTimeout(function () { form.dataset.submitted = ""; if (btn) btn.removeAttribute("aria-busy"); }, 8000);
  });

  // Confirmation prompts for destructive actions.
  document.addEventListener("submit", function (event) {
    const form = event.target;
    const msg = form.getAttribute("data-confirm");
    if (msg && !window.confirm(msg)) { event.preventDefault(); event.stopImmediatePropagation(); form.dataset.submitted = ""; }
  }, true);

  // Favourite toggle via fetch, falling back to a normal POST.
  document.addEventListener("submit", function (event) {
    const form = event.target;
    if (!form.matches("form.fav-form")) return;
    event.preventDefault();
    const btn = form.querySelector("button");
    fetch(form.action, {
      method: "POST",
      headers: { "X-CSRFToken": csrfToken(), "X-Requested-With": "XMLHttpRequest", "Accept": "application/json" },
      credentials: "same-origin",
    }).then(function (r) {
      if (r.status === 401 || r.status === 403 || r.redirected) { window.location = form.dataset.loginUrl || "/login/"; return null; }
      return r.json();
    }).then(function (data) {
      if (!data) return;
      document.querySelectorAll('form.fav-form[action="' + form.getAttribute("action") + '"] button').forEach(function (b) {
        b.classList.toggle("is-fav", data.favourited);
        b.setAttribute("aria-pressed", data.favourited ? "true" : "false");
        b.setAttribute("aria-label", data.favourited ? "Remove from saved" : "Save property");
        const icon = b.querySelector("i");
        if (icon) icon.className = data.favourited ? "bi bi-heart-fill" : "bi bi-heart";
        const label = b.querySelector(".fav-label");
        if (label) label.textContent = data.favourited ? "Saved" : "Save";
      });
    }).catch(function () { form.submit(); });
    if (btn) btn.blur();
  });

  // Show/hide fields based on a controlling input: data-toggle-source="name" data-toggle-value="rent".
  function syncToggles() {
    document.querySelectorAll("[data-toggle-source]").forEach(function (el) {
      const name = el.getAttribute("data-toggle-source");
      const values = el.getAttribute("data-toggle-value").split(",");
      const inputs = document.querySelectorAll('[name="' + name + '"]');
      let current = "";
      inputs.forEach(function (i) {
        if ((i.type === "radio" || i.type === "checkbox")) { if (i.checked) current = i.value; }
        else current = i.value;
      });
      const show = values.indexOf(current) !== -1;
      el.hidden = !show;
      el.querySelectorAll("input, select, textarea").forEach(function (f) { f.disabled = !show; });
    });
    document.querySelectorAll("[data-show-for-broker]").forEach(function (el) {
      const checked = document.querySelector('input[name="account_type"]:checked');
      el.hidden = !(checked && checked.value === "broker");
    });
  }
  document.addEventListener("change", syncToggles);
  document.addEventListener("DOMContentLoaded", syncToggles);

  // Category -> purpose constraints in the listing wizard.
  document.addEventListener("DOMContentLoaded", function () {
    const cat = document.querySelector("select[data-category-rules]");
    if (!cat) return;
    const rules = JSON.parse(cat.getAttribute("data-category-rules"));
    function apply() {
      const rule = rules[cat.value];
      document.querySelectorAll('input[name="purpose"]').forEach(function (r) {
        const allowed = !rule || (r.value === "rent" ? rule.rent : rule.sale);
        r.disabled = !allowed;
        if (!allowed && r.checked) r.checked = false;
        const label = r.closest("label");
        if (label) label.classList.toggle("opacity-50", !allowed);
      });
      syncToggles();
    }
    cat.addEventListener("change", apply);
    apply();
  });

  // Image upload previews with client-side type/size checks (server validates again).
  document.addEventListener("change", function (event) {
    const input = event.target;
    if (!input.matches("input[type=file][data-preview]")) return;
    const target = document.querySelector(input.getAttribute("data-preview"));
    const maxMb = parseFloat(input.getAttribute("data-max-mb") || "5");
    const errors = document.querySelector(input.getAttribute("data-errors") || "#upload-errors");
    if (!target) return;
    target.innerHTML = "";
    const problems = [];
    Array.from(input.files).forEach(function (file) {
      if (!/^image\/(jpeg|png|webp)$/.test(file.type)) { problems.push(file.name + ": only JPG, PNG or WEBP allowed."); return; }
      if (file.size > maxMb * 1024 * 1024) { problems.push(file.name + ": larger than " + maxMb + " MB."); return; }
      const div = document.createElement("div");
      div.className = "pv";
      const img = document.createElement("img");
      img.alt = file.name;
      img.src = URL.createObjectURL(file);
      img.onload = function () { URL.revokeObjectURL(img.src); };
      div.appendChild(img);
      target.appendChild(div);
    });
    if (errors) {
      errors.innerHTML = "";
      problems.forEach(function (p) { const d = document.createElement("div"); d.textContent = p; errors.appendChild(d); });
      errors.hidden = problems.length === 0;
    }
  });

  // Drag & drop onto upload zones.
  document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll(".upload-drop").forEach(function (zone) {
      const input = zone.querySelector("input[type=file]");
      ["dragenter", "dragover"].forEach(function (ev) { zone.addEventListener(ev, function (e) { e.preventDefault(); zone.classList.add("drag"); }); });
      ["dragleave", "drop"].forEach(function (ev) { zone.addEventListener(ev, function (e) { e.preventDefault(); zone.classList.remove("drag"); }); });
      zone.addEventListener("drop", function (e) {
        if (!input || !e.dataTransfer.files.length) return;
        input.files = e.dataTransfer.files;
        input.dispatchEvent(new Event("change", { bubbles: true }));
      });
    });
  });

  // Gallery: open the full-screen carousel at the clicked image.
  document.addEventListener("click", function (event) {
    const trigger = event.target.closest("[data-gallery-index]");
    if (!trigger) return;
    const modalEl = document.getElementById("galleryModal");
    if (!modalEl || !window.bootstrap) return;
    const carouselEl = modalEl.querySelector(".carousel");
    const carousel = window.bootstrap.Carousel.getOrCreateInstance(carouselEl, { interval: false });
    carousel.to(parseInt(trigger.getAttribute("data-gallery-index"), 10));
    window.bootstrap.Modal.getOrCreateInstance(modalEl).show();
  });

  // Share button: native share sheet when available, otherwise copy the link.
  document.addEventListener("click", function (event) {
    const btn = event.target.closest("[data-share]");
    if (!btn) return;
    const data = { title: btn.getAttribute("data-title") || document.title, url: btn.getAttribute("data-share") };
    if (navigator.share) { navigator.share(data).catch(function () {}); return; }
    if (navigator.clipboard) {
      navigator.clipboard.writeText(data.url).then(function () {
        const original = btn.innerHTML;
        btn.innerHTML = '<i class="bi bi-check2"></i> Link copied';
        setTimeout(function () { btn.innerHTML = original; }, 2000);
      });
    }
  });

  // Auto-submit filter controls marked data-autosubmit (e.g. sort select).
  document.addEventListener("change", function (event) {
    const el = event.target;
    if (el.matches("[data-autosubmit]") && el.form) el.form.submit();
  });

  // "Request a visit" buttons pre-tick the visit checkbox in the enquiry modal.
  document.addEventListener("click", function (event) {
    const btn = event.target.closest('[data-bs-target="#enquiryModal"]');
    if (!btn) return;
    const box = document.getElementById("id_request_visit");
    if (box) { box.checked = btn.getAttribute("data-visit") === "1"; box.dispatchEvent(new Event("change", { bubbles: true })); }
  });

  // Pill-style radio tabs (hero search).
  document.addEventListener("change", function (event) {
    const input = event.target;
    if (!input.matches(".nav-pills input[type=radio]")) return;
    input.closest(".nav-pills").querySelectorAll("label.nav-link").forEach(function (l) { l.classList.remove("active"); });
    input.closest("label").classList.add("active");
  });

  // Admin sidebar toggle on small screens.
  document.addEventListener("click", function (event) {
    const btn = event.target.closest("[data-sidebar-toggle]");
    if (!btn) return;
    const sidebar = document.querySelector(".admin-sidebar");
    if (sidebar) sidebar.classList.toggle("open");
  });

  // On phones the dashboard nav is a horizontal strip: keep the active tab in view.
  const dashNav = document.querySelector(".dash-nav");
  const activeLink = dashNav && dashNav.querySelector(".nav-link.active");
  if (activeLink && dashNav.scrollWidth > dashNav.clientWidth) {
    const offset = activeLink.getBoundingClientRect().left - dashNav.getBoundingClientRect().left + dashNav.scrollLeft;
    dashNav.scrollLeft = offset - (dashNav.clientWidth - activeLink.offsetWidth) / 2;
  }
})();
