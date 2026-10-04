/* Chart.js renderers for dashboards. Data comes from json_script tags. */
(function () {
  "use strict";
  const NAVY = "#111d35", YELLOW = "#E3B63C", GREEN = "#138a3e", MUTED = "#c3cad6";
  const PALETTE = [NAVY, YELLOW, "#3b5480", "#C99A1F", "#7286a8", "#F2D27E", "#a7b4ca", GREEN];

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
    Chart.defaults.font.family = "Manrope, system-ui, sans-serif";
    Chart.defaults.color = "#5f6b7a";

    const views = read("views-data");
    if (views && document.getElementById("viewsChart")) {
      new Chart(document.getElementById("viewsChart"), {
        type: "line",
        data: { labels: views.labels, datasets: [{ data: views.views, borderColor: NAVY, backgroundColor: "rgba(17,29,53,.07)", fill: true, tension: .3, pointRadius: 2 }] },
        options: base(),
      });
    }

    const admin = read("admin-charts");
    if (!admin) return;
    const make = function (id, cfg) { const el = document.getElementById(id); if (el) new Chart(el, cfg); };
    make("chartRegistrations", { type: "bar", data: { labels: admin.months, datasets: [{ data: admin.registrations, backgroundColor: NAVY, borderRadius: 6 }] }, options: base() });
    make("chartEnquiries", { type: "line", data: { labels: admin.months, datasets: [{ data: admin.enquiries, borderColor: NAVY, backgroundColor: "rgba(17,29,53,.07)", fill: true, tension: .3 }] }, options: base() });
    make("chartRevenue", { type: "bar", data: { labels: admin.months, datasets: [{ data: admin.revenue, backgroundColor: YELLOW, borderColor: "#C99A1F", borderWidth: 1, borderRadius: 6 }] },
      options: base({ plugins: { legend: { display: false }, tooltip: { callbacks: { label: function (c) { return "₹" + Number(c.parsed.y).toLocaleString("en-IN"); } } } } }) });
    make("chartCategories", { type: "bar", data: { labels: admin.categories.labels, datasets: [{ data: admin.categories.values, backgroundColor: PALETTE, borderRadius: 6 }] }, options: base({ indexAxis: "y", scales: { x: { beginAtZero: true, ticks: { precision: 0 } }, y: { grid: { display: false } } } }) });
    make("chartActive", { type: "doughnut", data: { labels: ["Active (public)", "Inactive"], datasets: [{ data: admin.active_inactive, backgroundColor: [YELLOW, MUTED], borderWidth: 0 }] },
      options: { responsive: true, maintainAspectRatio: false, cutout: "72%", plugins: { legend: { position: "bottom" } } } });
  });
})();
