/* Client-side column sorting for every `table.data` in the app.
 *
 * Applied automatically on load — no per-template markup is needed, so tables
 * added later inherit this for free. That matters here: there are 46 data
 * tables across 38 templates, and wiring each one by hand would rot.
 *
 * Behaviour
 *   - Click a header to sort by it; click again to reverse. An arrow shows the
 *     active column and direction.
 *   - Values are compared as numbers, then dates, then text, whichever fits the
 *     whole column. Currency/size suffixes and thousands separators are handled.
 *   - Both date shapes the app renders are understood: dd/mm/yyyy (the `dmy`
 *     filter) and YYYY-MM-DD HH:MM (the `ist` filter).
 *   - Placeholder cells ("—", "never", "") always sort last, in both
 *     directions. They are absences, not small values, so burying them keeps
 *     the informative rows together.
 *   - Rows hidden by a page's own filter stay hidden: sorting reorders rows and
 *     never touches their display style.
 *
 * Opting out: add `data-nosort` to a `th` (or `data-nosort` on the table to
 * disable it entirely). Columns holding only checkboxes or action buttons are
 * skipped automatically.
 */
(function () {
  "use strict";

  var PLACEHOLDERS = ["", "—", "-", "–", "never", "n/a", "none"];

  function cellText(row, index) {
    var cell = row.children[index];
    if (!cell) return "";
    return (cell.textContent || "").trim();
  }

  function isPlaceholder(text) {
    return PLACEHOLDERS.indexOf(text.toLowerCase()) !== -1;
  }

  // "1,234", "₹1,234.50", "12 MB", "45%" -> number. NaN if not numeric.
  //
  // Only a known currency prefix and a known unit suffix are stripped. Peeling
  // off arbitrary leading non-digits instead would read "LAP-010" as -10, which
  // made whole hostname columns look numeric and sort backwards.
  function asNumber(text) {
    var s = text
      .replace(/^[₹$€£]\s*/, "")                 // currency prefix
      .replace(/\s*(%|[KMGT]?B|ms|s)$/i, "")     // size / duration / percent unit
      .replace(/,/g, "")                          // thousands separators
      .trim();
    if (!/^[+-]?(\d+(\.\d*)?|\.\d+)$/.test(s)) return NaN;
    return parseFloat(s);
  }

  // dd/mm/yyyy (dmy filter) or YYYY-MM-DD[ HH:MM] (ist filter) -> epoch ms.
  function asDate(text) {
    var m = text.match(/^(\d{2})\/(\d{2})\/(\d{4})$/);
    if (m) return Date.UTC(+m[3], +m[2] - 1, +m[1]);
    m = text.match(/^(\d{4})-(\d{2})-(\d{2})(?:[ T](\d{2}):(\d{2}))?/);
    if (m) return Date.UTC(+m[1], +m[2] - 1, +m[3], +(m[4] || 0), +(m[5] || 0));
    return NaN;
  }

  // Decide once per column so a stray value cannot flip comparators mid-sort.
  function columnKind(rows, index) {
    var seen = 0, numeric = 0, dated = 0;
    for (var i = 0; i < rows.length; i++) {
      var text = cellText(rows[i], index);
      if (isPlaceholder(text)) continue;
      seen++;
      if (!isNaN(asDate(text))) dated++;
      else if (!isNaN(asNumber(text))) numeric++;
    }
    if (seen === 0) return "text";
    if (dated === seen) return "date";
    if (numeric === seen) return "number";
    return "text";
  }

  // Natural, case-insensitive compare: digit runs are compared as numbers so
  // LAP-2 precedes LAP-10.
  //
  // Hand-rolled rather than localeCompare(..., {numeric: true}), which reads
  // the hyphen in "LAP-010" as a minus sign and orders hostnames backwards.
  // Splitting into digit/non-digit runs keeps every digit run unsigned.
  function naturalCompare(a, b) {
    var ax = a.toLowerCase().match(/\d+|\D+/g) || [];
    var bx = b.toLowerCase().match(/\d+|\D+/g) || [];
    var n = Math.max(ax.length, bx.length);
    for (var i = 0; i < n; i++) {
      var pa = ax[i], pb = bx[i];
      if (pa === undefined) return -1;   // shorter string first
      if (pb === undefined) return 1;
      var aNum = /^\d+$/.test(pa), bNum = /^\d+$/.test(pb);
      if (aNum && bNum) {
        var diff = parseInt(pa, 10) - parseInt(pb, 10);
        if (diff !== 0) return diff;
      } else if (pa !== pb) {
        return pa < pb ? -1 : 1;
      }
    }
    return 0;
  }

  function comparator(kind, index, direction) {
    return function (a, b) {
      var textA = cellText(a, index), textB = cellText(b, index);
      var emptyA = isPlaceholder(textA), emptyB = isPlaceholder(textB);
      // Absences sink regardless of direction.
      if (emptyA && emptyB) return 0;
      if (emptyA) return 1;
      if (emptyB) return -1;

      var result;
      if (kind === "number") {
        result = asNumber(textA) - asNumber(textB);
      } else if (kind === "date") {
        result = asDate(textA) - asDate(textB);
      } else {
        result = naturalCompare(textA, textB);
      }
      return direction === "asc" ? result : -result;
    };
  }

  // A column of checkboxes or action buttons has nothing meaningful to order
  // by. Text *inside* a control is a label, not data — an Actions column of
  // "Uninstall"/"Update" buttons must still count as controls-only — so the
  // controls are stripped before asking whether anything is left.
  var CONTROLS = "input, button, form, select, textarea";

  function isControlColumn(rows, index) {
    var sampled = 0;
    for (var i = 0; i < rows.length && sampled < 25; i++) {
      var cell = rows[i].children[index];
      if (!cell) continue;
      sampled++;
      if (!cell.querySelector(CONTROLS)) return false;
      var clone = cell.cloneNode(true);
      clone.querySelectorAll(CONTROLS).forEach(function (el) { el.remove(); });
      if ((clone.textContent || "").trim() !== "") return false;
    }
    return sampled > 0;
  }

  function enhance(table) {
    if (table.hasAttribute("data-nosort")) return;
    var head = table.tHead, body = table.tBodies[0];
    if (!head || !body || head.rows.length === 0) return;
    // Only a single header row can be mapped cleanly onto columns.
    if (head.rows.length !== 1) return;
    var rows = Array.prototype.slice.call(body.rows);
    if (rows.length < 2) return;                     // nothing to reorder
    // Colspan rows ("no results") would be reordered into nonsense.
    for (var r = 0; r < rows.length; r++) {
      if (rows[r].children.length !== head.rows[0].children.length) return;
    }

    var headers = Array.prototype.slice.call(head.rows[0].children);
    headers.forEach(function (th, index) {
      if (th.hasAttribute("data-nosort")) return;
      if (th.querySelector("input, button")) return;  // select-all checkbox
      if (isControlColumn(rows, index)) return;

      th.classList.add("sortable");
      th.setAttribute("role", "button");
      th.setAttribute("tabindex", "0");
      th.setAttribute("aria-sort", "none");

      function activate() {
        var direction = th.getAttribute("data-dir") === "asc" ? "desc" : "asc";
        headers.forEach(function (other) {
          other.removeAttribute("data-dir");
          other.classList.remove("sorted");
          if (other.hasAttribute("aria-sort")) other.setAttribute("aria-sort", "none");
        });
        th.setAttribute("data-dir", direction);
        th.classList.add("sorted");
        th.setAttribute("aria-sort", direction === "asc" ? "ascending" : "descending");

        var current = Array.prototype.slice.call(body.rows);
        var kind = columnKind(current, index);
        current.sort(comparator(kind, index, direction));
        // Re-appending moves nodes without cloning, so listeners and the
        // checked state of any input inside a row survive.
        var fragment = document.createDocumentFragment();
        current.forEach(function (row) { fragment.appendChild(row); });
        body.appendChild(fragment);
      }

      th.addEventListener("click", activate);
      th.addEventListener("keydown", function (e) {
        if (e.key === "Enter" || e.key === " ") { e.preventDefault(); activate(); }
      });
    });
  }

  function init() {
    document.querySelectorAll("table.data").forEach(enhance);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
