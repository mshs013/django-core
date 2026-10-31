/* Theme: until the user picks Light or Dark, the OS theme is followed (so a light OS shows light, a dark OS shows dark). Runs before first paint (no inline script, CSP-safe).
   Order of precedence: this browser's choice (localStorage) > the signed-in user's saved preference (data-theme-pref) > system. */
(function () {
  var root = document.documentElement, KEY = "core-theme", mq = window.matchMedia ? window.matchMedia("(prefers-color-scheme: dark)") : null;
  function valid(v) { return v === "light" || v === "dark" || v === "system"; }
  function stored() { try { var v = localStorage.getItem(KEY); return valid(v) ? v : null; } catch (e) { return null; } }
  function pref() { return stored() || (valid(root.dataset.themePref) ? root.dataset.themePref : "system"); }
  function apply() {
    var p = pref(), effective = p === "system" ? (mq && mq.matches ? "dark" : "light") : p;
    root.setAttribute("data-bs-theme", effective);
    root.dataset.themeChoice = p;
    var icon = effective === "dark" ? "ti-moon" : "ti-sun";          // single toggle button; the icon shows the active mode
    document.querySelectorAll("[data-theme-icon]").forEach(function (el) { el.className = "ti fs-2 " + icon; });
    document.querySelectorAll("[data-theme-set]").forEach(function (el) { el.classList.toggle("active", el.dataset.themeSet === effective); });
  }
  function save(choice) {
    try { localStorage.setItem(KEY, choice); } catch (e) {}
    var meta = document.querySelector('meta[name="csrf-token"]'), url = document.body && document.body.dataset.themeUrl;
    if (meta && url) {                                    // remember it on the account too (best effort)
      fetch(url, { method: "POST", credentials: "same-origin", headers: { "X-CSRFToken": meta.content, "Content-Type": "application/x-www-form-urlencoded" }, body: "theme=" + encodeURIComponent(choice) }).catch(function () {});
    }
  }
  apply();
  if (mq && mq.addEventListener) { mq.addEventListener("change", function () { if (pref() === "system") { apply(); } }); }
  document.addEventListener("DOMContentLoaded", function () {
    apply();
    document.addEventListener("click", function (ev) {
      var t = ev.target.closest("[data-theme-toggle]");
      if (t) { ev.preventDefault(); save(root.getAttribute("data-bs-theme") === "dark" ? "light" : "dark"); apply(); return; }
      var b = ev.target.closest("[data-theme-set]");
      if (b && valid(b.dataset.themeSet)) { ev.preventDefault(); save(b.dataset.themeSet); apply(); }
    });
  });
})();

/* Sidebar: full or folded to its icons (Tabler's html[data-bs-sidebar="folded"]), remembered per browser. Applied before first paint so
   the page does not jump. While folded, menu groups open as fly-outs; a group the server rendered open is closed (and reopened on unfold). */
(function () {
  var root = document.documentElement, KEY = "core.sidebar", wide = window.matchMedia ? window.matchMedia("(min-width: 992px)") : null;
  function folded() { return root.getAttribute("data-bs-sidebar") === "folded"; }
  function openGroups(fold) {
    document.querySelectorAll(".navbar-vertical .nav-item.dropdown").forEach(function (li) {
      var menu = li.querySelector(".dropdown-menu"), toggle = li.querySelector("[data-bs-toggle='dropdown']");
      if (!menu || !toggle) { return; }
      if (fold) {
        if (menu.classList.contains("show")) { li.dataset.coreWasOpen = "1"; }
        menu.classList.remove("show"); toggle.classList.remove("show"); toggle.setAttribute("aria-expanded", "false");
        menu.removeAttribute("data-bs-popper"); menu.style.maxHeight = ""; menu.style.overflowY = "";
      } else {
        var open = li.dataset.coreWasOpen === "1";
        menu.classList.toggle("show", open); toggle.classList.toggle("show", open); toggle.setAttribute("aria-expanded", String(open));
        menu.removeAttribute("data-bs-popper");
      }
    });
  }
  function sync() { document.querySelectorAll("[data-core-sidebar-toggle]").forEach(function (t) { t.setAttribute("aria-pressed", String(folded())); }); }
  try { if (localStorage.getItem(KEY) === "folded" && (!wide || wide.matches)) { root.setAttribute("data-bs-sidebar", "folded"); } } catch (e) {}
  document.addEventListener("DOMContentLoaded", function () {
    if (folded()) { openGroups(true); }
    sync();
    // folded: a fly-out never runs past the bottom of the window (the sidebar starts below the header); it scrolls instead
    document.addEventListener("shown.bs.dropdown", function (ev) {
      if (!folded()) { return; }
      var li = ev.target.closest(".navbar-vertical .nav-item.dropdown"), menu = li && li.querySelector(".dropdown-menu");
      if (menu) { menu.style.maxHeight = Math.max(160, window.innerHeight - menu.getBoundingClientRect().top - 8) + "px"; menu.style.overflowY = "auto"; }
    });
    document.addEventListener("click", function (ev) {
      var t = ev.target.closest("[data-core-sidebar-toggle]");
      if (t) {
        ev.preventDefault();
        var fold = !folded();
        if (fold) { root.setAttribute("data-bs-sidebar", "folded"); } else { root.removeAttribute("data-bs-sidebar"); }
        openGroups(fold); sync();
        try { localStorage.setItem(KEY, fold ? "folded" : "full"); } catch (e) {}
        return;
      }
      if (!folded()) { return; }
      // folded: a click outside an open fly-out closes it
      document.querySelectorAll(".navbar-vertical .nav-item.dropdown").forEach(function (li) {
        if (li.contains(ev.target)) { return; }
        var menu = li.querySelector(".dropdown-menu.show"), toggle = li.querySelector("[data-bs-toggle='dropdown']");
        if (menu) { menu.classList.remove("show"); if (toggle) { toggle.classList.remove("show"); toggle.setAttribute("aria-expanded", "false"); } }
      });
    });
  });
})();
