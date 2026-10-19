/* Copied verbatim from landslidescience inventory/static/inventory/js/ls_float.js
 * (hig314/landslidescience @ e152641, 2026-10-18). One copy of every shared
 * thing: do not edit here; change it there and run
 * `python tools/sync_shared.py sync` (tools/shared.json pins the commit). */
/* ls_float.js — floating, draggable, resizable panels. Global `window.LSFloat`.
 *
 * Extracted 2026-10-11 from map.js (makeFloatingPanel) so /lidar/ panels
 * behave like the inventory's: dragged by their header, resized from the
 * bottom-right corner (CSS `resize: both` on .float-panel, ls_panels.css),
 * kept inside the parent. A panel is anchored by left/top: anchoring it by
 * bottom or right makes the browser's corner resize grow it the wrong way.
 * Needs ls_tools.js (LSTools.drag) on the page.
 *
 *   var fp = LSFloat.make(panel, {handle, toggle, close, onResize, onChange})
 *   fp.open() / fp.close() / fp.isOpen()
 *
 * `onChange` runs after every open and close (map.js uses it to rewrite the
 * URL hash). Open/closed is the `hidden` class.
 */
window.LSFloat = (function () {
  'use strict';

  function make(panel, opts) {
    if (!panel) return { open: function () {}, close: function () {}, isOpen: function () { return false; } };
    opts = opts || {};
    var handle = opts.handle, toggle = opts.toggle, closeBtn = opts.close,
        onResize = opts.onResize, onChange = opts.onChange;
    function isOpen() { return !panel.classList.contains('hidden'); }
    function open() {
      panel.classList.remove('hidden');
      if (toggle) toggle.classList.add('active');
      if (onResize) setTimeout(onResize, 60);   // after layout settles
      if (onChange) onChange();
    }
    function close() {
      panel.classList.add('hidden');
      if (toggle) toggle.classList.remove('active');
      if (onChange) onChange();
    }
    if (toggle)   toggle.addEventListener('click', function (e) { e.preventDefault(); isOpen() ? close() : open(); });
    if (closeBtn) closeBtn.addEventListener('click', function (e) { e.preventDefault(); close(); });

    if (onResize && window.ResizeObserver) {
      var raf = null;
      new ResizeObserver(function () {
        if (!isOpen()) return;
        if (raf) cancelAnimationFrame(raf);
        raf = requestAnimationFrame(onResize);
      }).observe(panel);
    }
    if (handle) {
      var offX = 0, offY = 0, parRect = null;
      LSTools.drag(handle, {
        onStart: function (e) {
          if (e.target.closest('button, a, input, label, select')) return false;  // let controls work
          var r = panel.getBoundingClientRect();
          parRect = (panel.offsetParent || document.body).getBoundingClientRect();
          offX = e.clientX - r.left; offY = e.clientY - r.top;
          // Pin the current position as left/top BEFORE clearing right/bottom
          // so the anchor flip doesn't jump.
          panel.style.left = (r.left - parRect.left) + 'px';
          panel.style.top  = (r.top  - parRect.top)  + 'px';
          panel.style.right = 'auto'; panel.style.bottom = 'auto';
        },
        onMove: function (e) {
          var w = panel.offsetWidth, h = panel.offsetHeight;
          panel.style.left = Math.max(0, Math.min(parRect.width  - w, e.clientX - offX - parRect.left)) + 'px';
          panel.style.top  = Math.max(0, Math.min(parRect.height - h, e.clientY - offY - parRect.top))  + 'px';
        }
      });
    }
    return { open: open, close: close, isOpen: isOpen };
  }

  return { make: make };
})();
