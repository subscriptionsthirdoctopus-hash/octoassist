/* Keeps --topbar-h in step with the topbar's real height.
 *
 * Pinned table headers rest at `top: var(--topbar-h)` so they come to a stop
 * immediately below the sticky topbar instead of sliding underneath it, which
 * is what put header labels and row data in the same band.
 *
 * The height cannot be hard-coded: the bar grows when its nav wraps on a
 * narrow window, and a stale value pins the header behind it. Measured here
 * and re-measured whenever the bar resizes. The CSS default is a sensible
 * fallback, so a browser that never runs this still gets a usable offset.
 */
(function () {
  "use strict";

  function apply() {
    var bar = document.querySelector(".topbar");
    if (!bar) return;                       // auth and landing pages have none
    var h = Math.round(bar.getBoundingClientRect().height);
    if (h > 0) {
      document.documentElement.style.setProperty("--topbar-h", h + "px");
    }
  }

  function start() {
    apply();
    var bar = document.querySelector(".topbar");
    if (bar && window.ResizeObserver) {
      // Catches nav wrapping, font loading and zoom — all of which change the
      // bar's height without firing a resize event.
      new ResizeObserver(apply).observe(bar);
    }
    // Belt and braces: the observer is the precise signal, but it does not
    // always deliver while a tab is backgrounded, which can leave the offset
    // stale after a viewport change. resize is cheap and self-correcting.
    window.addEventListener("resize", apply);
    // Web fonts land after first paint and change the bar's height with them.
    if (document.fonts && document.fonts.ready) {
      document.fonts.ready.then(apply).catch(function () {});
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();
