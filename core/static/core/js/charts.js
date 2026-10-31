/* charts.js — draws every [data-chart] block with the vendored Chart.js, on page load and after HTMX swaps (report previews).
   data-chart = {"type": "bar|line|area|pie|doughnut", "labels": [...], "datasets": [{"label", "data"}], "stacked": bool}.
   Colours come from the Tabler theme variables, so light and dark themes both read well. */
(function () {
  if (typeof Chart === "undefined") { return; }
  var palette = function () {
    var css = getComputedStyle(document.documentElement);
    return ["--tblr-primary", "--tblr-green", "--tblr-orange", "--tblr-purple", "--tblr-red", "--tblr-cyan", "--tblr-yellow", "--tblr-pink",
            "--tblr-teal", "--tblr-indigo"].map(function (v) { return (css.getPropertyValue(v) || "").trim() || "#206bc4"; });
  };
  var alpha = function (color, a) {
    var m = /^#([0-9a-f]{6})$/i.exec(color);
    if (!m) { return color; }
    var n = parseInt(m[1], 16);
    return "rgba(" + (n >> 16) + "," + ((n >> 8) & 255) + "," + (n & 255) + "," + a + ")";
  };
  var draw = function (box) {
    var spec; try { spec = JSON.parse(box.dataset.chart || "null"); } catch (e) { spec = null; }
    var canvas = box.querySelector("canvas");
    if (!spec || !canvas) { return; }
    var old = Chart.getChart(canvas); if (old) { old.destroy(); }
    var colors = palette(), round = spec.type === "pie" || spec.type === "doughnut";
    var css = getComputedStyle(document.body), text = (css.color || "#666"), grid = "rgba(128,128,128,.18)";
    var datasets = (spec.datasets || []).map(function (ds, i) {
      var c = colors[i % colors.length];
      return round ? { label: ds.label, data: ds.data, backgroundColor: spec.labels.map(function (_l, j) { return colors[j % colors.length]; }), borderWidth: 1 }
        : { label: ds.label, data: ds.data, borderColor: c, backgroundColor: spec.type === "bar" ? alpha(c, 0.75) : alpha(c, 0.18),
            fill: spec.type === "area", tension: 0.25, pointRadius: spec.labels.length > 60 ? 0 : 3, borderWidth: 2 };
    });
    box.style.height = round ? "300px" : "320px";
    new Chart(canvas, {
      type: spec.type === "area" ? "line" : spec.type,
      data: { labels: spec.labels, datasets: datasets },
      options: {
        responsive: true, maintainAspectRatio: false, animation: false,
        plugins: { legend: { display: round || datasets.length > 1, labels: { color: text } }, tooltip: { mode: round ? "nearest" : "index", intersect: false } },
        scales: round ? {} : {
          x: { stacked: !!spec.stacked, ticks: { color: text, maxRotation: 50, autoSkip: true }, grid: { color: grid } },
          y: { stacked: !!spec.stacked, beginAtZero: true, ticks: { color: text }, grid: { color: grid } },
        },
      },
    });
  };
  var drawAll = function (root) { (root || document).querySelectorAll("[data-chart]").forEach(draw); };
  if (document.readyState === "loading") { document.addEventListener("DOMContentLoaded", function () { drawAll(); }); } else { drawAll(); }
  document.addEventListener("htmx:afterSwap", function (ev) { drawAll(ev.detail.target); });
  new MutationObserver(function () { drawAll(); }).observe(document.documentElement, { attributes: true, attributeFilter: ["data-bs-theme"] });
})();
