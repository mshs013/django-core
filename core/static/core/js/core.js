/* core.js — idle-timeout redirect, inline-formset helpers, position renumbering, Alpine search builder. No inline scripts (CSP-friendly). */
(function () {
  var idle = parseInt(document.body.dataset.idle || "0", 10);
  if (idle > 0) {
    var t;
    var reset = function () { clearTimeout(t); t = setTimeout(function () { window.location = "/login/?timeout=1"; }, idle * 1000 + 2000); };
    ["click", "keydown", "htmx:afterRequest"].forEach(function (e) { document.addEventListener(e, reset); }); reset();
  }
  window.coreRenumber = function (container) {
    var n = 0;
    container.querySelectorAll(":scope > .inline-row:not(.removed)").forEach(function (row) {
      var pos = row.querySelector("input[name$='-position']"); if (pos) { pos.value = n++; }
    });
  };
  document.addEventListener("click", function (ev) {
    var rm = ev.target.closest("[data-inline-remove]");
    if (rm) {
      var row = rm.closest(".inline-row"), del = row.querySelector("input[name$='-DELETE']");
      if (del) { del.checked = true; row.classList.add("removed"); } else { row.remove(); }
      window.coreRenumber(row.parentElement);
    }
    var up = ev.target.closest("[data-inline-move]");
    if (up) {
      var r = up.closest(".inline-row"), dir = up.dataset.inlineMove;
      var sib = dir === "up" ? r.previousElementSibling : r.nextElementSibling;
      if (sib && sib.classList.contains("inline-row")) { dir === "up" ? r.parentElement.insertBefore(r, sib) : r.parentElement.insertBefore(sib, r); window.coreRenumber(r.parentElement); }
    }
  });
  /* "+ Add row": the new row's index is the formset's current TOTAL_FORMS, read at click time (not when the form was rendered),
     and TOTAL_FORMS only ever grows, so rows removed in between leave a harmless empty slot instead of a duplicated index. */
  document.body.addEventListener("htmx:configRequest", function (ev) {
    var btn = ev.detail.elt;
    if (!btn || !btn.hasAttribute || !btn.hasAttribute("data-inline-add")) { return; }
    var box = btn.closest("[data-formset]"), tf = box && box.querySelector("input[name$='-TOTAL_FORMS']");
    if (!tf) { return; }
    var index = parseInt(tf.value || "0", 10);
    ev.detail.parameters.index = String(index);
    tf.value = String(index + 1);
  });
  document.body.addEventListener("htmx:afterSwap", function (ev) {
    var total = ev.detail.target.closest("[data-formset]");
    if (total) { var rows = total.querySelector(".rows"); if (rows) { window.coreRenumber(rows); } }
  });
  /* Alpine component for the advanced search builder: state is a one-level AND/OR tree serialised into the hidden "adv" input. */
  document.addEventListener("alpine:init", function () {
    Alpine.data("searchBuilder", function () {
      return {
        fields: [], op: "AND", items: [],
        init: function () {
          var read = function (el, k) { try { return JSON.parse(el.dataset[k] || "null"); } catch (e) { return null; } };
          this.fields = read(this.$el, "fields") || [];
          var initial = read(this.$el, "initial") || {};
          this.op = initial.op || "AND";
          this.items = (initial.items || []).filter(function (i) { return !("items" in i); });   // one flat level in the UI
        },
        meta: function (name) { return this.fields.find(function (f) { return f.name === name; }) || { operators: [], choices: [], kind: "text" }; },
        add: function () { var f = this.fields[0]; if (f) { this.items.push({ field: f.name, op: f.operators[0].value, value: "" }); } },
        remove: function (i) { this.items.splice(i, 1); },
        changeField: function (it) { it.op = this.meta(it.field).operators[0].value; it.value = ""; },
        json: function () { return JSON.stringify({ op: this.op, items: this.items }); },
      };
    });
    /* A form field shown only while other fields hold given values: data-show-when = [[name, [values]], ...] (all must match; a
       checkbox counts as "on" when ticked). Display only: the server validates the values whatever is shown. */
    Alpine.data("showWhen", function () {
      return {
        shown: true,
        init: function () {
          var el = this.$el, form = el.closest("form"), self = this, rules = [];
          try { rules = JSON.parse(el.dataset.showWhen || "[]") || []; } catch (e) {}
          if (!form || !rules.length) { return; }
          var value = function (name) {
            var inputs = form.querySelectorAll("[name='" + name + "']");
            for (var i = 0; i < inputs.length; i++) {
              var input = inputs[i];
              if (input.type === "checkbox" || input.type === "radio") { if (input.checked) { return input.value || "on"; } }
              else { return input.value; }
            }
            return "";
          };
          var check = function () { self.shown = rules.every(function (r) { return r[1].indexOf(value(r[0])) !== -1; }); };
          form.addEventListener("change", check);
          check();
        },
      };
    });
    Alpine.data("reportBuilder", function () {
      return {
        d: { columns: [], groupings: [], aggregates: [], sorting: [], calculated: [], chart: { type: "", x: "", y: [], stacked: false },
             parameters: { date_preset: "", date_from: "", date_to: "" } },
        panel: true,
        init: function () {
          var raw = {}; try { raw = JSON.parse(this.$el.dataset.definition || "{}") || {}; } catch (e) {}
          this.d = Object.assign(this.d, raw);
          this.d.chart = Object.assign({ type: "", x: "", y: [], stacked: false }, raw.chart || {});
          this.d.parameters = Object.assign({ date_preset: "", date_from: "", date_to: "" }, raw.parameters || {});
          try { this.panel = localStorage.getItem("core.rb.panel") !== "0"; } catch (e) {}
          this.$watch("panel", function (v) { try { localStorage.setItem("core.rb.panel", v ? "1" : "0"); } catch (e) {} });
        },
        /* output column names, in the order the server returns them (see ReportSpec.output_aliases) */
        outputs: function () {
          var grouped = this.d.groupings.length || this.d.aggregates.length;
          var aggs = this.d.aggregates.map(function (a) { return (a.alias || (a.fn + "_" + (a.column || "rows"))).toLowerCase(); });
          if (this.d.groupings.length && !aggs.length) { aggs = ["rows"]; }
          var base = grouped ? this.d.groupings.concat(aggs) : this.d.columns.slice();
          return base.concat(this.d.calculated.map(function (c) { return c.alias; }));
        },
        numberOutputs: function () {
          var kinds = {}; try { kinds = JSON.parse(this.$root.dataset.kinds || "{}") || {}; } catch (e) {}
          var aggs = this.d.aggregates.map(function (a) { return (a.alias || (a.fn + "_" + (a.column || "rows"))).toLowerCase(); });
          var calc = this.d.calculated.map(function (c) { return c.alias; });
          var grouped = this.d.groupings.length || this.d.aggregates.length;
          return this.outputs().filter(function (o) { return aggs.includes(o) || (o === "rows" && grouped) || calc.includes(o) || kinds[o] === "number"; });
        },
        definition: function () {
          var d = JSON.parse(JSON.stringify(this.d));
          if (!d.chart.type) { d.chart = {}; }
          return JSON.stringify(d);
        },
      };
    });
  });
})();
