/* tg_weights.js — factor weights as shares that always add up to 100 %.
 * Global `window.TgWeights` (and module.exports, for tools/weights_check.js).
 *
 * Moving one share scales all the others to fit, keeping their ratios. The
 * ratios must survive a trip through the extremes: drag A to 100 % (every
 * other share shows 0), then to 0 %, then to 50 %, and B : C : D are what
 * they were before. So the state is not the shares themselves but, per
 * factor, a mass m >= 0 and a level: only the highest level with mass
 * shows, and setting a share to 100 % lifts that factor one level above
 * the rest instead of zeroing them. Nothing is ever rescaled in place, so
 * there is no rounding drift either.
 *
 *   var w = TgWeights.create({grade: 10, corner: 5, ...});   // any positive weights
 *   w.set('grade', 0.5);  w.share('corner');  w.shares();     // shares 0..1
 */
(function (root) {
  'use strict';
  var EPS = 1e-9;

  function create(init) {
    var names = Object.keys(init), m = {}, lvl = {};
    names.forEach(function (n) { m[n] = Math.max(0, +init[n] || 0); lvl[n] = 0; });

    function top(except) {
      var e = -Infinity;
      names.forEach(function (n) { if (n !== except && m[n] > 0 && lvl[n] > e) e = lvl[n]; });
      return e;
    }
    function mass(level, except) {
      var s = 0;
      names.forEach(function (n) { if (n !== except && m[n] > 0 && lvl[n] === level) s += m[n]; });
      return s;
    }
    function shares() {
      var e = top(null), out = {};
      if (e === -Infinity) { names.forEach(function (n) { out[n] = 0; }); return out; }
      var tot = mass(e, null);
      names.forEach(function (n) { out[n] = (m[n] > 0 && lvl[n] === e) ? m[n] / tot : 0; });
      return out;
    }
    function set(name, s) {
      s = Math.min(1, Math.max(0, +s || 0));
      var e = top(name);
      if (e === -Infinity) {
        // Every other factor is at 0: they share what is left equally.
        if (s < 1 - EPS) names.forEach(function (n) { if (n !== name) { m[n] = 1; lvl[n] = 0; } });
        e = 0;
        if (s >= 1 - EPS) { m[name] = 1; lvl[name] = 0; return; }
      }
      if (s <= EPS) { m[name] = 0; lvl[name] = e; return; }
      if (s >= 1 - EPS) { m[name] = 1; lvl[name] = e + 1; return; }
      m[name] = s / (1 - s) * mass(e, name);
      lvl[name] = e;
    }
    return {
      names: names,
      shares: shares,
      share: function (n) { return shares()[n]; },
      set: set
    };
  }

  // Shares (0..1) as percentages rounded to `dp` places that still add up
  // to exactly 100 (largest remainder), for display: 27.1 + 13.5 + ... = 100.0.
  function roundShares(sh, dp) {
    var f = Math.pow(10, dp), keys = Object.keys(sh), tot = 0, out = {};
    keys.forEach(function (k) { tot += sh[k]; });
    if (!(tot > 0)) { keys.forEach(function (k) { out[k] = 0; }); return out; }
    var units = Math.round(100 * f), used = 0;
    var parts = keys.map(function (k) {
      var exact = sh[k] / tot * units, fl = Math.floor(exact + 1e-9);
      used += fl;
      return { k: k, fl: fl, rem: exact - fl };
    });
    parts.slice().sort(function (a, b) { return b.rem - a.rem; }).slice(0, Math.max(0, units - used))
      .forEach(function (p) { p.fl += 1; });
    parts.forEach(function (p) { out[p.k] = p.fl / f; });
    return out;
  }

  var api = { create: create, roundShares: roundShares };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.TgWeights = api;
})(this);
