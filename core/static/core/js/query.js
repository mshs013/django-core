/* query.js — Alpine component behind the query builder page (core/templates/core/reports/query_builder.html): results in the
   middle, the editor in a right sidebar (an icon rail picks the section, "panel" shows or hides the section pane).
   The state "d" is serialised into the hidden "definition" input; the server validates every path, function and operator again
   (core/reports/query/engine.py), so nothing here is trusted. Aliases are computed the same way as on the server. */
document.addEventListener("alpine:init", function () {
  var seq = 0;
  var key = function () { seq += 1; return "k" + seq; };
  var clean = function (text) {
    text = String(text || "").trim().toLowerCase().replace(/[^a-z0-9_]+/g, "_").replace(/^_+|_+$/g, "").slice(0, 41) || "col";
    return /^[a-z]/.test(text) ? text : ("c_" + text).slice(0, 41);
  };
  var read = function (el, name) { try { return JSON.parse(el.dataset[name] || "null"); } catch (e) { return null; } };

  Alpine.data("queryBuilder", function () {
    return {
      panel: true, tab: "columns", open: {},
      titles: { joins: "Joins", columns: "Columns", filters: "Filters", group: "Group & HAVING", sort: "Sort & limit", chart: "Chart", save: "Save & share" }, fields: {}, O: {}, d: { joins: [], columns: [], filters: { op: "AND", items: [] }, having: [], sort: [], distinct: false, limit: 0, chart: { type: "", x: "", y: [], stacked: false } },
      key: key,
      init: function () {
        var self = this;
        (read(this.$el, "fields") || []).forEach(function (f) { self.fields[f.p] = f; });
        this.O = read(this.$el, "options") || {};
        try { this.panel = localStorage.getItem("core.qb.panel") !== "0"; } catch (e) {}
        this.$watch("panel", function (v) { try { localStorage.setItem("core.qb.panel", v ? "1" : "0"); } catch (e) {} });
        var raw = read(this.$el, "definition") || {};
        var leaf = function (l) { return Object.assign(self.newLeaf(), l || {}, { _k: key(), value: self.loadValue(l || {}) }); };
        var tree = function (t) { return { _k: key(), op: (t && t.op) || "AND", items: ((t && t.items) || []).map(function (i) { return i.items ? tree(i) : leaf(i); }) }; };
        this.d.columns = (raw.columns || []).map(function (c) {
          var type = c["case"] ? "case" : (c.path ? "field" : "count");
          return { _k: key(), type: type, path: c.path || "", fn: c.fn || "", arg: c.arg == null ? "" : String(c.arg), agg: c.agg || (type === "count" ? "count" : ""),
                   useWhere: !!c.where, where: c.where ? leaf(c.where) : null, alias: c.alias || "", label: c.label || "",
                   "case": c["case"] ? { when: leaf(c["case"].when), then: c["case"].then, "else": c["case"]["else"] } : { when: self.newLeaf(), then: "", "else": "" } };
        });
        this.d.filters = tree(raw.filters);
        this.d.having = (raw.having || []).map(function (h) { return { _k: key(), alias: h.alias, op: h.op, value: Array.isArray(h.value) ? (h.op === "in" ? h.value.join(", ") : h.value.slice()) : (h.value == null ? "" : h.value) }; });
        this.d.sort = (raw.sort || []).map(function (s) { return { _k: key(), alias: s.alias, dir: s.dir || "asc" }; });
        this.d.distinct = !!raw.distinct; this.d.limit = raw.limit || 0;
        this.d.chart = Object.assign({ type: "", x: "", y: [], stacked: false }, raw.chart || {});
        // joins: saved ones, or (older definitions) the tables the definition already uses
        this.d.joins = (raw.joins || []).map(function (j) { return { _k: key(), path: j.path, type: j.type === "inner" ? "inner" : "left" }; });
        if (!raw.joins) { this.joinUsed(); }
        if (!this.d.columns.length) { this.addColumn("field"); }
        Alpine.effect(function () { self.applyJoins(self.joinedTables()); });
        Alpine.effect(function () { if (self.tab === "group" && !self.grouped()) { self.tab = "columns"; } });
      },
      // ------------------------------------------------------------------ tabs, summary, per-column details
      pick: function (t) { if (this.panel && this.tab === t) { this.panel = false; } else { this.tab = t; this.panel = true; } },
      toggle: function (c) { this.open[c._k] = !this.open[c._k]; },
      detailSet: function (c) { return !!(c.label || (c.agg && c.useWhere && c.where && c.where.path) || c.type === "case"); },
      caseText: function (c) {
        var w = c["case"].when, f = this.fields[w.path];
        if (!w.path) { return "CASE … (click to set up)"; }
        var v = Array.isArray(w.value) ? w.value.join(", ") : w.value;
        return "CASE WHEN " + (f ? f.l : w.path) + " " + ((this.O.operatorLabels || {})[w.op] || w.op) + " " + v + " THEN " + c["case"].then + " ELSE " + c["case"]["else"];
      },
      filterCount: function () {
        return this.d.filters.items.reduce(function (n, i) { return n + (i.items ? i.items.length : 1); }, 0);
      },
      groupKeys: function () {
        var names = this.aliases(), self = this;
        return names.filter(function (_a, i) { return !self.d.columns[i].agg; });
      },
      summary: function () {
        var n = this.d.columns.length, f = this.filterCount(), parts = [n + " column" + (n === 1 ? "" : "s")];
        var jn = this.d.joins.filter(function (j) { return j.path; }).length;
        if (jn) { parts.unshift(jn + " join" + (jn === 1 ? "" : "s")); }
        parts.push(f ? f + " filter" + (f === 1 ? "" : "s") : "no filters");
        if (this.grouped()) { parts.push("grouped by " + (this.groupKeys().join(", ") || "all rows")); }
        if (this.d.having.length && this.grouped()) { parts.push(this.d.having.length + " result filter" + (this.d.having.length === 1 ? "" : "s")); }
        if (this.d.sort.length) { parts.push("sorted by " + this.d.sort.map(function (s) { return s.alias + (s.dir === "desc" ? " ↓" : " ↑"); }).join(", ")); }
        if (this.d.limit) { parts.push("top " + this.d.limit); }
        if (this.d.chart.type) { parts.push(this.d.chart.type + " chart"); }
        return parts.join(" · ");
      },
      loadValue: function (l) {
        if (Array.isArray(l.value)) { return ["in", "not_in"].includes(l.op) && this.kindOf(l.path) !== "choice" ? l.value.join(", ") : l.value.map(String); }
        return l.value == null ? "" : String(l.value);
      },
      // ------------------------------------------------------------------ catalogue helpers
      fillFields: function (el) {
        var t = document.getElementById("qb-field-options");
        if (t) { el.innerHTML = t.innerHTML; el.setAttribute("data-qb-fields", ""); this.applyJoins(this.joinedTables(), el); }
      },
      // ------------------------------------------------------------------ joins
      joinedTables: function () {
        var out = { "": true };
        this.d.joins.forEach(function (j) {
          if (!j.path) { return; }
          var parts = j.path.split("__");
          for (var i = 1; i <= parts.length; i++) { out[parts.slice(0, i).join("__")] = true; }
        });
        return out;
      },
      tableOf: function (path) { var i = (path || "").lastIndexOf("__"); return i < 0 ? "" : path.slice(0, i); },
      usedPaths: function () {
        var paths = [], leaf = function (l) { if (l && l.path) { paths.push(l.path); } };
        this.d.columns.forEach(function (c) { if (c.type === "field") { leaf(c); } if (c.type === "case") { leaf(c["case"].when); } if (c.useWhere) { leaf(c.where); } });
        var walk = function (t) { t.items.forEach(function (i) { if (i.items) { walk(i); } else { leaf(i); } }); };
        walk(this.d.filters);
        return paths;
      },
      unjoined: function () {
        var joined = this.joinedTables(), self = this, out = [];
        this.usedPaths().forEach(function (p) { var t = self.tableOf(p); if (!joined[t] && out.indexOf(t) < 0) { out.push(t); } });
        var label = {}; (this.O.tables || []).forEach(function (t) { label[t.p] = t.l; });
        return out.map(function (t) { return label[t] || t; });
      },
      joinUsed: function () {
        var joined = this.joinedTables(), self = this, add = [];
        this.usedPaths().forEach(function (p) { var t = self.tableOf(p); if (t && !joined[t] && add.indexOf(t) < 0) { add.push(t); } });
        add.forEach(function (t) { self.d.joins.push({ _k: key(), path: t, type: "left" }); });
      },
      /* Field pickers offer the root table and the joined tables only; a value already chosen stays visible. */
      applyJoins: function (joined, only) {
        var selects = only ? [only] : (this.$root ? this.$root.querySelectorAll("select[data-qb-fields]") : []);
        Array.prototype.forEach.call(selects, function (sel) {
          Array.prototype.forEach.call(sel.querySelectorAll("optgroup"), function (g) {
            var any = false;
            Array.prototype.forEach.call(g.querySelectorAll("option"), function (o) {
              var show = !!joined[o.dataset.j || ""] || o.value === sel.value;
              o.hidden = !show; o.disabled = !show; any = any || show;
            });
            g.hidden = !any;
          });
        });
      },
      kindOf: function (path) { var f = this.fields[path]; return f ? f.k : ""; },
      choicesOf: function (path) { var f = this.fields[path]; return f ? f.c : []; },
      fnsFor: function (kind) {
        var out = {}, fns = this.O.functions || {};
        Object.keys(fns).forEach(function (k) { if (fns[k].kinds.includes(kind)) { out[k] = fns[k]; } });
        return out;
      },
      filterFnsFor: function (kind) { var fns = this.O.functions || {}; return (this.O.filterFunctions || []).filter(function (k) { return fns[k] && fns[k].kinds.includes(kind); }); },
      leafKind: function (l) {
        if (l.fn) { return l.fn === "date" ? "date" : "number"; }
        return this.kindOf(l.path) || "text";
      },
      opsFor: function (kind) { return (this.O.operators || {})[kind] || []; },
      shape: function (l) {
        if (["isnull", "notnull"].includes(l.op)) { return "none"; }
        if (["between", "not_between"].includes(l.op)) { return "two"; }
        if (["in", "not_in"].includes(l.op)) { return "list"; }
        if (["last_days", "last_months"].includes(l.op)) { return "period"; }
        return "one";
      },
      inputType: function (kind) { return { number: "number", date: "date", datetime: "datetime-local", time: "time" }[kind] || "text"; },
      // ------------------------------------------------------------------ editing
      newLeaf: function () { return { _k: key(), path: "", fn: "", op: "eq", value: "" }; },
      resetLeaf: function (l) { l.fn = ""; this.resetOp(l); },
      resetOp: function (l) { var ops = this.opsFor(this.leafKind(l)); if (!ops.includes(l.op)) { l.op = ops[0] || "eq"; } this.resetValue(l); },
      resetValue: function (l) { var s = this.shape(l); l.value = s === "two" ? ["", ""] : (s === "list" && this.leafKind(l) === "choice" ? [] : ""); },
      addColumn: function (type) {
        var k = key();
        this.tab = "columns"; this.panel = true;
        if (type === "case") { this.open[k] = true; }
        this.d.columns.push({ _k: k, type: type, path: "", fn: "", arg: "", agg: type === "count" ? "count" : "", useWhere: false, where: null,
                              alias: "", label: "", "case": { when: this.newLeaf(), then: "", "else": "" } });
      },
      move: function (list, i, step) { var j = i + step; if (j < 0 || j >= list.length) { return; } var item = list.splice(i, 1)[0]; list.splice(j, 0, item); },
      grouped: function () { return this.d.columns.some(function (c) { return !!c.agg; }); },
      fallback: function (c) {
        var base = c.type === "count" ? "rows" : (c.type === "case" ? "case_value" : (c.fn ? c.fn + "_" + c.path : c.path).replace(/__/g, "_"));
        return c.agg && c.type !== "count" ? c.agg + "_" + base : base;
      },
      aliases: function () {
        var used = {}, self = this;
        return this.d.columns.map(function (c) {
          var text = clean(c.alias || self.fallback(c)), base = text, n = 2;
          while (used[text]) { text = (base.slice(0, 37) + "_" + n); n += 1; }
          used[text] = true; return text;
        });
      },
      numberAliases: function () {
        var self = this, names = this.aliases();
        return names.filter(function (a, i) {
          var c = self.d.columns[i];
          if (["count", "count_distinct", "sum", "avg"].includes(c.agg)) { return true; }
          if (c.fn && self.O.functions[c.fn]) { return self.O.functions[c.fn].result === "number"; }
          return c.type === "field" && self.kindOf(c.path) === "number";
        });
      },
      // ------------------------------------------------------------------ serialise
      json: function () {
        var self = this, names = this.aliases();
        var leaf = function (l) { var o = { path: l.path, op: l.op, value: l.value }; if (l.fn) { o.fn = l.fn; } return o; };
        var tree = function (t) { return { op: t.op, items: t.items.map(function (i) { return i.items ? tree(i) : leaf(i); }) }; };
        var columns = this.d.columns.map(function (c, i) {
          var o = { alias: names[i] };
          if (c.label) { o.label = c.label; }
          if (c.type === "field") { o.path = c.path; if (c.fn) { o.fn = c.fn; if (c.arg !== "") { o.arg = c.arg; } } }
          if (c.type === "case") { o["case"] = { when: leaf(c["case"].when), then: c["case"].then, "else": c["case"]["else"] }; }
          if (c.agg) { o.agg = c.agg; if (c.useWhere && c.where) { o.where = leaf(c.where); } }
          return o;
        });
        var chart = this.d.chart.type ? { type: this.d.chart.type, x: this.d.chart.x, y: this.d.chart.y.filter(function (y) { return names.includes(y); }), stacked: !!this.d.chart.stacked } : {};
        var joins = this.d.joins.filter(function (j) { return j.path; }).map(function (j) { return { path: j.path, type: j.type }; });
        return JSON.stringify({ joins: joins, columns: columns, filters: tree(this.d.filters), sort: this.d.sort.map(function (s) { return { alias: s.alias, dir: s.dir }; }),
                                having: this.grouped() ? this.d.having.map(function (h) { return { alias: h.alias, op: h.op, value: h.value }; }) : [],
                                distinct: !!this.d.distinct, limit: Number(this.d.limit) || 0, chart: chart });
      },
    };
  });
});
