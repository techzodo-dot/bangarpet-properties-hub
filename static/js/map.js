/* Google Maps helpers. Loaded only when GOOGLE_MAPS_API_KEY is configured. */
(function () {
  "use strict";
  const BANGARPET = { lat: 12.9911, lng: 78.1774 };

  function formatPrice(p, purpose) {
    if (!p) return "";
    const n = Number(p);
    const s = "₹" + n.toLocaleString("en-IN");
    return purpose === "rent" ? s + "/month" : s;
  }

  window.bphInitResultsMap = function () {
    const el = document.getElementById("results-map");
    const dataEl = document.getElementById("map-markers");
    if (!el || !dataEl) return;
    const markers = JSON.parse(dataEl.textContent);
    const map = new google.maps.Map(el, { center: BANGARPET, zoom: 13, mapTypeControl: false, streetViewControl: false });
    const bounds = new google.maps.LatLngBounds();
    const info = new google.maps.InfoWindow();
    markers.forEach(function (m) {
      const pos = { lat: m.lat, lng: m.lng };
      const marker = new google.maps.Marker({ position: pos, map: map, title: m.title });
      bounds.extend(pos);
      marker.addListener("click", function () {
        const wrap = document.createElement("div");
        const a = document.createElement("a");
        a.href = m.url; a.textContent = m.title; a.style.fontWeight = "600";
        const p = document.createElement("div"); p.textContent = formatPrice(m.price, m.purpose);
        wrap.appendChild(a); wrap.appendChild(p);
        info.setContent(wrap);
        info.open({ anchor: marker, map: map });
      });
    });
    if (markers.length > 1) map.fitBounds(bounds);
    else if (markers.length === 1) map.setCenter({ lat: markers[0].lat, lng: markers[0].lng });
  };

  window.bphInitDetailMap = function () {
    const el = document.getElementById("detail-map");
    if (!el) return;
    const pos = { lat: parseFloat(el.dataset.lat), lng: parseFloat(el.dataset.lng) };
    const exact = el.dataset.exact === "1";
    const map = new google.maps.Map(el, { center: pos, zoom: exact ? 16 : 14, mapTypeControl: false, streetViewControl: false });
    if (exact) new google.maps.Marker({ position: pos, map: map });
    else new google.maps.Circle({ map: map, center: pos, radius: 500, fillColor: "#172B4D", fillOpacity: 0.12, strokeColor: "#172B4D", strokeOpacity: 0.5, strokeWeight: 1 });
  };
})();
