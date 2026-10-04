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

  // Shrink large photos in the browser before upload (max 1920 px, JPEG). Uploads
  // get faster on mobile data and stay under the host's request size limit.
  // The server still re-encodes and validates every image.
  const RESIZE_MAX = 1920;
  const RESIZE_SKIP_BYTES = 700 * 1024;
  function loadImage(file) {
    return new Promise(function (resolve, reject) {
      const img = new Image();
      img.onload = function () { resolve(img); };
      img.onerror = reject;
      img.src = URL.createObjectURL(file);
    });
  }
  function resizeImage(file) {
    if (!/^image\/(jpeg|png|webp)$/.test(file.type)) return Promise.resolve(file);
    return loadImage(file).then(function (img) {
      URL.revokeObjectURL(img.src);
      const w = img.naturalWidth, h = img.naturalHeight;
      const scale = Math.min(1, RESIZE_MAX / Math.max(w, h));
      if (scale === 1 && file.size <= RESIZE_SKIP_BYTES) return file;
      const canvas = document.createElement("canvas");
      canvas.width = Math.round(w * scale);
      canvas.height = Math.round(h * scale);
      const ctx = canvas.getContext("2d");
      ctx.fillStyle = "#fff";
      ctx.fillRect(0, 0, canvas.width, canvas.height);
      ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
      return new Promise(function (resolve) {
        canvas.toBlob(function (blob) {
          if (!blob || blob.size >= file.size) { resolve(file); return; }
          const name = file.name.replace(/\.[^.]+$/, "") + ".jpg";
          resolve(new File([blob], name, { type: "image/jpeg", lastModified: Date.now() }));
        }, "image/jpeg", 0.85);
      });
    }).catch(function () { return file; });
  }
  function resizeInput(input) {
    if (typeof DataTransfer === "undefined" || !input.files.length) return Promise.resolve(true);
    input.dataset.resizing = "1";
    const token = (input._resizeToken || 0) + 1;
    input._resizeToken = token;
    const job = Promise.all(Array.from(input.files).map(resizeImage)).then(function (files) {
      if (input._resizeToken !== token) return;  // a newer selection replaced this one
      const dt = new DataTransfer();
      files.forEach(function (f) { dt.items.add(f); });
      input.files = dt.files;
    }).catch(function () {}).then(function () {
      const latest = input._resizeToken === token;
      if (latest) { delete input.dataset.resizing; input._resizeJob = null; }
      return latest;
    });
    input._resizeJob = job;
    return job;
  }

  // Image upload previews with client-side type/size checks (server validates again).
  document.addEventListener("change", function (event) {
    const input = event.target;
    if (!input.matches("input[type=file]")) return;
    if (input.matches("[data-resize]")) {
      resizeInput(input).then(function (latest) { if (latest) renderPreview(input); });
    } else {
      renderPreview(input);
    }
  });
  function renderPreview(input) {
    if (!input.matches("[data-preview]")) return;
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
  }

  // Before submitting, wait for any photo resizing that is still running.
  // Forms marked data-batch-upload send many photos in several smaller
  // requests (each under ~4 MB) and then reload the page to show the result.
  const BATCH_BYTES = 4 * 1024 * 1024 - 200 * 1024;
  document.addEventListener("submit", function (event) {
    const form = event.target;
    const inputs = Array.from(form.querySelectorAll("input[type=file][data-resize]"));
    const pending = inputs.map(function (i) { return i._resizeJob; }).filter(Boolean);
    if (pending.length) {
      event.preventDefault();
      form.dataset.submitted = "";
      Promise.all(pending).then(function () { form.requestSubmit ? form.requestSubmit(event.submitter || undefined) : form.submit(); });
      return;
    }
    if (!form.matches("[data-batch-upload]") || !window.fetch) return;
    const input = inputs[0];
    if (!input || !input.files.length) return;
    const files = Array.from(input.files);
    const total = files.reduce(function (sum, f) { return sum + f.size; }, 0);
    if (total <= BATCH_BYTES) return;  // fits in one normal request
    event.preventDefault();
    const batches = [];
    let current = [], size = 0;
    files.forEach(function (f) {
      if (current.length && size + f.size > BATCH_BYTES) { batches.push(current); current = []; size = 0; }
      current.push(f); size += f.size;
    });
    if (current.length) batches.push(current);
    const base = new FormData(form);
    base.delete(input.name);
    const errors = document.querySelector(input.getAttribute("data-errors") || "#upload-errors");
    let chain = Promise.resolve();
    batches.forEach(function (batch) {
      chain = chain.then(function () {
        const data = new FormData();
        base.forEach(function (value, key) { data.append(key, value); });
        batch.forEach(function (f) { data.append(input.name, f, f.name); });
        return fetch(form.action || window.location.href, { method: "POST", body: data, credentials: "same-origin" })
          .then(function (r) { if (!r.ok) throw new Error("Upload failed (" + r.status + ")"); });
      });
    });
    chain.then(function () { window.location.reload(); }).catch(function (err) {
      form.dataset.submitted = "";
      const btn = form.querySelector("[type=submit]");
      if (btn) btn.removeAttribute("aria-busy");
      if (errors) { errors.textContent = (err && err.message) || "Upload failed. Please try fewer photos at a time."; errors.hidden = false; }
    });
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

  // Move focus to an in-page confirmation (e.g. "Message received") so it is announced.
  // Wait for "load": the browser's #fragment jump would otherwise reset focus afterwards.
  const confirmation = document.querySelector("[data-autofocus]");
  if (confirmation) {
    const focusIt = function () { confirmation.focus({ preventScroll: true }); };
    if (document.readyState === "complete") focusIt(); else window.addEventListener("load", function () { setTimeout(focusIt, 0); });
  }

  // Installable web app: register the service worker (HTTPS or localhost only).
  const swUrl = document.documentElement.dataset.sw;
  const secure = location.protocol === "https:" || location.hostname === "localhost" || location.hostname === "127.0.0.1";
  if (swUrl && secure && "serviceWorker" in navigator) {
    window.addEventListener("load", function () { navigator.serviceWorker.register(swUrl, { scope: "/" }).catch(function () {}); });
  }
  // Show "Install the app" links only when the browser offers installation.
  let installPrompt = null;
  window.addEventListener("beforeinstallprompt", function (event) {
    event.preventDefault();
    installPrompt = event;
    document.querySelectorAll("[data-install-app]").forEach(function (el) { el.hidden = false; });
  });
  window.addEventListener("appinstalled", function () {
    installPrompt = null;
    document.querySelectorAll("[data-install-app]").forEach(function (el) { el.hidden = true; });
  });
  document.addEventListener("click", function (event) {
    const btn = event.target.closest("[data-install-button]");
    if (!btn || !installPrompt) return;
    event.preventDefault();
    installPrompt.prompt();
    installPrompt.userChoice.finally(function () { installPrompt = null; });
  });

  // On phones the dashboard nav is a horizontal strip: keep the active tab in view.
  const dashNav = document.querySelector(".dash-nav");
  const activeLink = dashNav && dashNav.querySelector(".nav-link.active");
  if (activeLink && dashNav.scrollWidth > dashNav.clientWidth) {
    const offset = activeLink.getBoundingClientRect().left - dashNav.getBoundingClientRect().left + dashNav.scrollLeft;
    dashNav.scrollLeft = offset - (dashNav.clientWidth - activeLink.offsetWidth) / 2;
  }
})();
