/* Virtual tour preview: auto-advancing, accessible room-by-room slideshow. */
(function () {
  "use strict";
  const reduceMotion = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  function initTour(stage) {
    const slides = Array.from(stage.querySelectorAll(".tour-slide"));
    if (!slides.length) return;
    const bars = Array.from(stage.querySelectorAll(".tour-progress span"));
    const rooms = Array.from(document.querySelectorAll("[data-tour-goto]"));
    const label = stage.querySelector("[data-tour-label]");
    const count = stage.querySelector("[data-tour-count]");
    const toggle = stage.querySelector("[data-tour-toggle]");
    const interval = parseInt(stage.dataset.interval || "4000", 10);
    let index = 0, timer = null, playing = !reduceMotion;

    function show(i) {
      index = (i + slides.length) % slides.length;
      slides.forEach(function (s, n) {
        s.classList.toggle("is-active", n === index);
        s.setAttribute("aria-hidden", n === index ? "false" : "true");
      });
      bars.forEach(function (b, n) {
        b.classList.toggle("is-done", n < index);
        b.classList.remove("is-active");
        if (n === index) { void b.offsetWidth; b.classList.add("is-active"); }
      });
      rooms.forEach(function (r) { r.classList.toggle("is-active", parseInt(r.dataset.tourGoto, 10) === index); });
      if (label) label.textContent = slides[index].dataset.caption || "Photo " + (index + 1);
      if (count) count.textContent = index + 1;
    }
    function schedule() {
      clearInterval(timer);
      stage.classList.toggle("is-paused", !playing);
      if (playing) timer = setInterval(function () { show(index + 1); }, interval);
      if (toggle) {
        toggle.setAttribute("aria-label", playing ? "Pause tour" : "Play tour");
        toggle.innerHTML = playing ? '<i class="bi bi-pause-fill"></i>' : '<i class="bi bi-play-fill"></i>';
      }
    }
    stage.style.setProperty("--tour-interval", interval + "ms");
    stage.querySelector("[data-tour-prev]").addEventListener("click", function () { show(index - 1); schedule(); });
    stage.querySelector("[data-tour-next]").addEventListener("click", function () { show(index + 1); schedule(); });
    if (toggle) toggle.addEventListener("click", function () { playing = !playing; schedule(); });
    rooms.forEach(function (r) { r.addEventListener("click", function () { show(parseInt(r.dataset.tourGoto, 10)); schedule(); }); });
    stage.addEventListener("keydown", function (e) {
      if (e.key === "ArrowRight") { show(index + 1); schedule(); }
      if (e.key === "ArrowLeft") { show(index - 1); schedule(); }
    });
    // Only run while visible, to save battery and data.
    if ("IntersectionObserver" in window) {
      new IntersectionObserver(function (entries) {
        entries.forEach(function (en) { if (en.isIntersecting) schedule(); else clearInterval(timer); });
      }, { threshold: 0.3 }).observe(stage);
    } else { schedule(); }
    show(0);
  }

  // Load the video iframe only when its modal opens; unload on close.
  document.addEventListener("show.bs.modal", function (e) {
    const frame = e.target.querySelector("iframe[data-src]");
    if (frame) frame.src = frame.dataset.src;
  });
  document.addEventListener("hidden.bs.modal", function (e) {
    const frame = e.target.querySelector("iframe[data-src]");
    if (frame) frame.src = "about:blank";
  });

  function ready() { document.querySelectorAll("[data-tour]").forEach(initTour); }
  if (document.readyState !== "loading") ready(); else document.addEventListener("DOMContentLoaded", ready);
})();
