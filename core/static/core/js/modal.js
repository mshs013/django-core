/* modal.js — the global CRUD modal. HTMX loads partials into #core-modal-content; the server answers a successful save/delete with 204 +
   HX-Trigger "core:modal-done" (close, toast, refresh the table). Validation errors come back as HTTP 400 partials and are swapped in. */
(function () {
  var modalEl, content, dialog, instance;
  /* Tabler 1.6 ships a UMD bundle that exposes its components on window.tabler (NOT window.bootstrap); accept either. */
  function bs(name) {
    return (window.bootstrap && window.bootstrap[name]) || (window.tabler && window.tabler[name]) ||
           (window.tabler && window.tabler.bootstrap && window.tabler.bootstrap[name]) || null;
  }
  function init() {
    modalEl = document.getElementById("core-modal");
    if (!modalEl) { return false; }
    content = document.getElementById("core-modal-content");
    dialog = document.getElementById("core-modal-dialog");
    var Modal = bs("Modal");
    if (!instance && Modal) { instance = Modal.getOrCreateInstance(modalEl); }
    return !!instance;
  }
  function toast(text, level) {
    var box = document.getElementById("core-toasts");
    var Toast = bs("Toast");
    if (!box || !text || !Toast) { return; }
    var el = document.createElement("div"), body = document.createElement("div"), btn = document.createElement("button"), flex = document.createElement("div");
    el.className = "toast align-items-center text-bg-" + (level === "error" ? "danger" : level === "warning" ? "warning" : "success") + " border-0";
    el.setAttribute("role", "status");
    flex.className = "d-flex";
    body.className = "toast-body"; body.textContent = text;                        // textContent: never HTML
    btn.type = "button"; btn.className = "btn-close btn-close-white me-2 m-auto"; btn.setAttribute("data-bs-dismiss", "toast"); btn.setAttribute("aria-label", "Close");
    flex.appendChild(body); flex.appendChild(btn); el.appendChild(flex); box.appendChild(el);
    var t = Toast.getOrCreateInstance(el, { delay: 4000 });
    el.addEventListener("hidden.bs.toast", function () { el.remove(); });
    t.show();
  }
  function isModalTarget(ev) { return ev.detail && ev.detail.target && ev.detail.target.id === "core-modal-content"; }

  document.addEventListener("htmx:beforeSwap", function (ev) {                       // validation / refusal partials arrive as 400 / 422
    if (isModalTarget(ev) && (ev.detail.xhr.status === 400 || ev.detail.xhr.status === 422)) { ev.detail.shouldSwap = true; ev.detail.isError = false; }
  });
  document.addEventListener("htmx:afterSwap", function (ev) {
    if (!isModalTarget(ev) || !init()) { return; }
    var hint = content.querySelector("[data-modal-size]"), size = hint ? hint.getAttribute("data-modal-size") : "lg";
    dialog.className = "modal-dialog modal-dialog-centered modal-dialog-scrollable" + (size === "xl" ? " modal-xl" : size === "sm" ? " modal-sm" : size === "lg" ? " modal-lg" : "");
    instance.show();
    var bad = content.querySelector(".is-invalid"), first = bad || content.querySelector("input:not([type=hidden]):not([type=file]), select, textarea");
    if (first) { setTimeout(function () { try { first.focus(); } catch (e) {} }, 200); }
  });
  document.addEventListener("htmx:responseError", function (ev) {
    var status = ev.detail.xhr ? ev.detail.xhr.status : 0;
    if (status === 400 || status === 422) { return; }
    toast(status === 403 ? "You are not allowed to do that." : status === 404 ? "That record no longer exists." : "Something went wrong. Please try again.", "error");
    if (init() && modalEl.classList.contains("show") && status === 403) { instance.hide(); }
  });
  document.addEventListener("hidden.bs.modal", function (ev) {
    if (!ev.target || ev.target.id !== "core-modal") { return; }
    if (content) { content.innerHTML = ""; }
    if (instance) { instance.dispose(); instance = null; }                      // a fresh instance per opening (re-using one left a stray second backdrop)
    document.querySelectorAll(".modal-backdrop").forEach(function (b) { b.remove(); });
    document.body.classList.remove("modal-open"); document.body.style.removeProperty("overflow"); document.body.style.removeProperty("padding-right");
  });
  document.addEventListener("core:modal-done", function (ev) {
    var d = ev.detail || {};
    if (init() && modalEl.classList.contains("show")) { instance.hide(); }
    toast(d.message, d.level);
    if (d.refresh === false) { return; }
    var table = document.getElementById("resource-table");
    if (table && window.htmx) { window.htmx.ajax("GET", window.location.pathname + window.location.search, { target: "#resource-table", swap: "innerHTML" }); }
    else { setTimeout(function () { window.location.reload(); }, 400); }                // pages without a table (e.g. the menu builder)
  });
})();
