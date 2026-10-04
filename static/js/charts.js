/* Chart.js renderers for dashboards. Data comes from json_script tags. */
(function () {
  "use strict";
  const NAVY = "#172B4D", YELLOW = "#FFD600", GREEN = "#138a3e", MUTED = "#9aa8bf";
  const PALETTE = [NAVY, YELLOW, "#3d5a8a", "#e6a700", "#6b84b0", "#c9a800", "#8fa3c6", GREEN];

  function read(id) {
    const el = document.getElementById(id);
    return el ? JSON.parse(el.textContent) : null;
  }
  function base(extra) {
    return Object.assign({
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: { x: { grid: { display: false } }, y: { beginAtZero: true, ticks: { precision: 0 } } },
    }, extra || {});
  }
  function ready(fn) { if (document.readyState !== "loading") fn(); else document.addEventListener("DOMContentLoaded", fn); }

  ready(function () {
    if (!window.Chart) return;
    Chart.defaults.font.family = "Inter, system-ui, sans-serif";
    Chart.defaults.color = "#5f6b7a";

    const views = read("views-data");
    if (views && document.getElementById("viewsChart")) {
      new Chart(document.getElementById("viewsChart"), {
        type: "line",
        data: { labels: views.labels, datasets: [{ data: views.views, borderColor: NAVY, backgroundColor: "rgba(23,43,77,.08)", fill: true, tension: .3, pointRadius: 2 }] },
        options: base(),
      });
    }

    const admin = read("admin-charts");
    if (!admin) return;
    const make = function (id, cfg) { const el = document.getElementById(id); if (el) new Chart(el, cfg); };
    make("chartRegistrations", { type: "bar", data: { labels: admin.months, datasets: [{ data: admin.registrations, backgroundColor: NAVY, borderRadius: 6 }] }, options: base() });
    make("chartEnquiries", { type: "line", data: { labels: admin.months, datasets: [{ data: admin.enquiries, borderColor: NAVY, backgroundColor: "rgba(23,43,77,.08)", fill: true, tension: .3 }] }, options: base() });
    make("chartRevenue", { type: "bar", data: { labels: admin.months, datasets: [{ data: admin.revenue, backgroundColor: YELLOW, borderColor: "#c9a800", borderWidth: 1, borderRadius: 6 }] },
      options: base({ plugins: { legend: { display: false }, tooltip: { callbacks: { label: function (c) { return "₹" + Number(c.parsed.y).toLocaleString("en-IN"); } } } } }) });
    make("chartCategories", { type: "bar", data: { labels: admin.categories.labels, datasets: [{ data: admin.categories.values, backgroundColor: PALETTE, borderRadius: 6 }] }, options: base({ indexAxis: "y", scales: { x: { beginAtZero: true, ticks: { precision: 0 } }, y: { grid: { display: false } } } }) });
    make("chartActive", { type: "doughnut", data: { labels: ["Active (public)", "Inactive"], datasets: [{ data: admin.active_inactive, backgroundColor: [GREEN, MUTED], borderWidth: 0 }] },
      options: { responsive: true, maintainAspectRatio: false, cutout: "65%", plugins: { legend: { position: "bottom" } } } });
  });
})();
