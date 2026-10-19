/* Copied verbatim from landslidescience inventory/static/inventory/js/profile_chart.js
 * (hig314/landslidescience @ e152641, 2026-10-18). One copy of every shared
 * thing: do not edit here; change it there and run
 * `python tools/sync_shared.py sync` (tools/shared.json pins the commit). */
/* profile_chart.js — the elevation-profile chart and its floating panel.
 * Global `window.LSProfileChart`.
 *
 * Extracted 2026-10-11 from profile.js (the ∿ line profile on /inventory/)
 * so the group profile on /lidar/ (trace_profile.js) is the same instrument:
 * one panel (dragged by its header, resized from its corner -- ls_float.js),
 * one chart (1:1 or exaggerated, gaps where there is no data, a legend that
 * moves below a thin 1:1 band), one hover (a guide on the chart, a tooltip,
 * a marker on the map through the host), one brushed window with summary
 * statistics, SVG and CSV.
 *
 * The chart knows nothing of DEMs or planes. A host gives it series:
 *
 *   {length: m, series: [{id, label, short?, color, dash?, width?, marker?,
 *                         runs: [[{d, z, ...host fields}, ...], ...]}]}
 *
 * marker: true draws the series as dots (with a ±q.sigma bar where a sample
 * has one) instead of a line -- sparse points such as soundings.
 *
 * `d` is metres along the x axis, `z` metres (NaN = no data: the pen
 * lifts). A run is a polyline; a series draws all its runs in one colour and
 * is one line of the legend and one row of the window table. The inventory
 * profile has one series per DEM with one run each; the group profile has
 * one series per group with a run per trace part.
 *
 *   var ch = LSProfileChart.create({
 *     panel, makePanel,            // an empty .float-panel; LSFloat.make or a wrapper
 *     buttons: [{cls, label, title, onClick}],   // header buttons before 1:1
 *     hover: 'column' | 'nearest', // a vertical slice (all series at one d) or the nearest sample
 *     ends: ['A', 'A′'],           // optional labels at the two ends of the x axis
 *     statsName: 'DEM',            // the window table's first column
 *     tip(hit) -> {html, read},    // hit: {d} (column) or {d, series, point} (nearest)
 *     onHover(hit | null),         // the host marks the map
 *     onChange(),                  // the lock or the window changed (URL state)
 *     onClose(),                   // the panel's ✕
 *     csv() -> {name, text}, svgName() -> name
 *   });
 *   ch.setData(data) / setStatus(text) / setTitle(text) / open() / close() / isOpen()
 *   ch.locked() / setLocked(bool) / window() / setWindow({d0, d1} | null) / stats(series)
 *   ch.hoverAt(d | null)   show the guide and readout at d from outside (a map or
 *                          another chart); does not call onHover, so hosts can't loop
 *
 * Tracks (trailgeek's refine tool, 2026-10-18): rows under the profile that
 * share its x axis, its hover guide and its brushed window. The host draws
 * them; the chart leaves room and says where:
 *
 *   tracks: {height: px, draw(geom) -> SVG string}
 *     geom = {ox, sx, L, top, width, height}: x of d is ox + d * sx, the rows
 *     fill [top, top + height] and [ox, ox + width].
 *   marginRight: px                 // room right of the plot (default 14), e.g. track labels
 *
 * With tracks, the profile sits at the top of the plot area even at 1:1 (it
 * is centred without them), so the rows stay right under it.
 *
 * Map marker paint for the hover position: LSProfileChart.CURSOR_PAINT.
 */
window.LSProfileChart = (function () {
  'use strict';

  var CURSOR_PAINT = { 'circle-radius': 6, 'circle-color': '#ffeb3b',
                       'circle-stroke-color': '#333', 'circle-stroke-width': 2 };

  function esc(s) { return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/"/g, '&quot;'); }
  function fmtD(m) { return Math.abs(m) >= 10000 ? (m / 1000).toFixed(1) + ' km' : Math.round(m) + ' m'; }
  function niceStep(span, target) {
    var raw = span / target, p = Math.pow(10, Math.floor(Math.log10(raw)));
    var f = raw / p;
    return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10) * p;
  }
  function download(name, mime, text) {
    var a = document.createElement('a');
    a.href = URL.createObjectURL(new Blob([text], { type: mime }));
    a.download = name; document.body.appendChild(a); a.click();
    setTimeout(function () { URL.revokeObjectURL(a.href); a.remove(); }, 500);
    window.LSTrack && LSTrack.event('download', { kind: 'profile_' + name.split('.').pop() });
  }

  // Mean elevation of the samples in the window; slope as the least-squares
  // line through them (signed along the x axis, degrees and %); curvature as
  // twice the quadratic coefficient of a least-squares parabola (1/km,
  // positive = concave up). Fits rather than finite differences, so the
  // 0.1 m terrain-RGB step does not dominate a short window. For a group
  // profile the slope is the trend of the group along the plane -- for
  // moraine crests, the gradient of the former ice surface.
  function windowStats(series, w) {
    var d0 = Math.min(w.d0, w.d1), d1 = Math.max(w.d0, w.d1);
    var xs = [], ys = [];
    series.runs.forEach(function (run) { run.forEach(function (q) {
      if (q.d >= d0 && q.d <= d1 && !isNaN(q.z)) { xs.push(q.d - d0); ys.push(q.z); }
    }); });
    var n = xs.length;
    if (n < 2) return { n: n };
    var mean = ys.reduce(function (a, b) { return a + b; }, 0) / n;
    var mx = xs.reduce(function (a, b) { return a + b; }, 0) / n, sxx = 0, sxy = 0, i;
    for (i = 0; i < n; i++) { sxx += (xs[i] - mx) * (xs[i] - mx); sxy += (xs[i] - mx) * (ys[i] - mean); }
    var b1 = sxx > 0 ? sxy / sxx : 0;
    var a2 = NaN;
    if (n >= 3 && sxx > 0) {
      var S = [0, 0, 0, 0, 0], T = [0, 0, 0];
      for (i = 0; i < n; i++) {
        var x = xs[i] - mx, x2 = x * x;
        S[0] += 1; S[1] += x; S[2] += x2; S[3] += x2 * x; S[4] += x2 * x2;
        T[0] += ys[i]; T[1] += ys[i] * x; T[2] += ys[i] * x2;
      }
      var det = function (m) {
        return m[0][0] * (m[1][1] * m[2][2] - m[1][2] * m[2][1]) - m[0][1] * (m[1][0] * m[2][2] - m[1][2] * m[2][0]) + m[0][2] * (m[1][0] * m[2][1] - m[1][1] * m[2][0]);
      };
      var D = det([[S[0], S[1], S[2]], [S[1], S[2], S[3]], [S[2], S[3], S[4]]]);
      if (Math.abs(D) > 1e-9) a2 = det([[S[0], S[1], T[0]], [S[1], S[2], T[1]], [S[2], S[3], T[2]]]) / D;
    }
    return { n: n, length: d1 - d0, mean: mean, slopeDeg: Math.atan(b1) * 180 / Math.PI, slopePct: b1 * 100,
             curvature: isNaN(a2) ? NaN : 2 * a2 * 1000 };
  }

  function create(o) {
    var panel = o.panel, hoverMode = o.hover || 'column';
    var data = null, locked = true, sel = null, brushing = false, statusText = '', titleText = '';
    var el = {};

    // ---- DOM ---------------------------------------------------------------
    panel.innerHTML =
      '<div class="float-header profile-header"><span class="profile-title"></span><span class="profile-status"></span></div>' +
      '<div class="profile-body"><svg></svg><div class="profile-tip"></div></div>' +
      '<div class="profile-stats"></div><div class="profile-read">&nbsp;</div>';
    var hd = panel.querySelector('.profile-header');
    function btn(cls, label, title, fn) {
      var b = document.createElement('button');
      b.type = 'button'; b.className = cls; b.innerHTML = label; if (title) b.title = title;
      if (fn) b.addEventListener('click', fn);
      hd.appendChild(b);
      return b;
    }
    (o.buttons || []).forEach(function (b) { btn(b.cls || '', b.label, b.title, b.onClick); });
    el.lock = btn('profile-lock active', '1:1', 'True aspect (1:1). Click to let the profile fill the panel.', function () {
      setLocked(!locked); if (o.onChange) o.onChange();
    });
    btn('profile-svg', 'SVG', 'Download the chart as SVG', downloadSvg);
    btn('profile-csv', 'CSV', 'Download the samples as CSV', downloadCsv);
    var closeBtn = btn('profile-close', '&times;', 'Close');
    el.title = hd.querySelector('.profile-title');
    el.status = hd.querySelector('.profile-status');
    el.body = panel.querySelector('.profile-body');
    el.svg = panel.querySelector('svg');
    el.tip = panel.querySelector('.profile-tip');
    el.stats = panel.querySelector('.profile-stats');
    el.read = panel.querySelector('.profile-read');
    if (hoverMode === 'nearest') el.svg.classList.add('nearest');

    var fp = o.makePanel(panel, { handle: hd, close: closeBtn, onResize: draw });
    closeBtn.addEventListener('click', function () {
      data = null; sel = null; leave();
      if (o.onClose) o.onClose();
    });

    // ---- drawing -----------------------------------------------------------
    function draw() {
      if (!data || !el.svg) return;
      var series = data.series;
      var rect = el.body.getBoundingClientRect();
      var W = Math.max(200, rect.width), H = Math.max(120, rect.height);
      var tracks = o.tracks && o.tracks.height > 0 ? o.tracks : null, TG = 22;   // TG: the x labels between profile and tracks
      var m = { l: 52, r: o.marginRight || 14, t: 16, b: 28 + (tracks ? tracks.height + TG : 0) };
      var iw = W - m.l - m.r, ih = H - m.t - m.b;
      var zmin = Infinity, zmax = -Infinity;
      series.forEach(function (s) { s.runs.forEach(function (run) { run.forEach(function (q) {
        if (!isNaN(q.z)) { if (q.z < zmin) zmin = q.z; if (q.z > zmax) zmax = q.z; }
      }); }); });
      if (!isFinite(zmin)) { zmin = 0; zmax = 1; }
      if (zmax - zmin < 1) { zmax = zmin + 1; }
      var L = Math.max(1, data.length), range = zmax - zmin;
      var sx, sy, ve;
      if (locked) { sx = sy = Math.min(iw / L, ih / range); ve = 1; }   // one scale, the profile fits
      else { sx = iw / L; sy = ih / range; ve = sy / sx; }
      var cw = sx * L, ch = sy * range;
      var ox = m.l, oy = m.t + (tracks ? 0 : (ih - ch) / 2);   // centre vertically when locked, unless tracks follow
      var X = function (d) { return ox + d * sx; };
      var Y = function (z) { return oy + (zmax - z) * sy; };
      var parts = ['<rect x="0" y="0" width="' + W + '" height="' + H + '" fill="#fff"/>'];
      var xs = niceStep(L, 6), ys = niceStep(range, 4);
      for (var d = 0; d <= L + 1e-9; d += xs) {
        parts.push('<line x1="' + X(d) + '" y1="' + oy + '" x2="' + X(d) + '" y2="' + (oy + ch) + '" stroke="#eee"/>');
        parts.push('<text x="' + X(d) + '" y="' + (oy + ch + 14) + '" font-size="10" text-anchor="middle" fill="#555">' + fmtD(d) + '</text>');
      }
      for (var z = Math.ceil(zmin / ys) * ys; z <= zmax + 1e-9; z += ys) {
        parts.push('<line x1="' + ox + '" y1="' + Y(z) + '" x2="' + (ox + cw) + '" y2="' + Y(z) + '" stroke="#eee"/>');
        parts.push('<text x="' + (ox - 4) + '" y="' + (Y(z) + 3) + '" font-size="10" text-anchor="end" fill="#555">' + Math.round(z) + '</text>');
      }
      if (sel) {
        var sx0 = X(Math.max(0, Math.min(sel.d0, sel.d1))), sx1 = X(Math.min(L, Math.max(sel.d0, sel.d1)));
        parts.push('<rect x="' + sx0 + '" y="' + oy + '" width="' + Math.max(0, sx1 - sx0) + '" height="' + ch +
                   '" fill="#5D4037" fill-opacity="0.10" stroke="#5D4037" stroke-opacity="0.6"/>');
      }
      parts.push('<rect x="' + ox + '" y="' + oy + '" width="' + cw + '" height="' + ch + '" fill="none" stroke="#999"/>');
      var ty = oy + ch + TG, gy1 = oy + ch;
      if (tracks) {
        parts.push(tracks.draw({ ox: ox, sx: sx, L: L, top: ty, width: cw, height: tracks.height }) || '');
        if (sel) {
          var tx0 = X(Math.max(0, Math.min(sel.d0, sel.d1))), tx1 = X(Math.min(L, Math.max(sel.d0, sel.d1)));
          parts.push('<rect x="' + tx0 + '" y="' + ty + '" width="' + Math.max(0, tx1 - tx0) + '" height="' + tracks.height +
                     '" fill="#5D4037" fill-opacity="0.10" stroke="#5D4037" stroke-opacity="0.6" pointer-events="none"/>');
        }
        gy1 = ty + tracks.height;
      }
      parts.push('<text x="' + (ox - 36) + '" y="' + (oy + ch / 2) + '" font-size="10" fill="#555" text-anchor="middle" transform="rotate(-90 ' + (ox - 36) + ' ' + (oy + ch / 2) + ')">elevation (m)</text>');
      if (o.ends) {
        parts.push('<text x="' + (ox + 2) + '" y="' + (oy - 4) + '" font-size="11" font-weight="700" fill="#111">' + esc(o.ends[0]) + '</text>');
        parts.push('<text x="' + (ox + cw - 2) + '" y="' + (oy - 4) + '" font-size="11" font-weight="700" fill="#111" text-anchor="end">' + esc(o.ends[1]) + '</text>');
      }
      // Lines: one colour per series, the pen lifting between runs and at gaps.
      // Screen positions are kept on the samples for the nearest-sample hover.
      series.forEach(function (s) {
        if (s.marker) {
          // A point series (soundings): a dot per sample, a bar for its ±sigma.
          s.runs.forEach(function (run) { run.forEach(function (q) {
            q._px = X(q.d); q._py = isNaN(q.z) ? NaN : Y(q.z);
            if (isNaN(q.z)) return;
            if (q.sigma > 0) parts.push('<line x1="' + q._px.toFixed(1) + '" y1="' + Y(q.z + q.sigma).toFixed(1) + '" x2="' + q._px.toFixed(1) +
                                        '" y2="' + Y(q.z - q.sigma).toFixed(1) + '" stroke="' + s.color + '" stroke-opacity="0.6" stroke-width="1"/>');
            parts.push('<circle cx="' + q._px.toFixed(1) + '" cy="' + q._py.toFixed(1) + '" r="2.6" fill="' + s.color + '" stroke="#fff" stroke-width="0.6"/>');
          }); });
          return;
        }
        var dstr = '';
        s.runs.forEach(function (run) {
          var pen = false;
          run.forEach(function (q) {
            q._px = X(q.d); q._py = isNaN(q.z) ? NaN : Y(q.z);
            if (isNaN(q.z)) { pen = false; return; }
            dstr += (pen ? 'L' : 'M') + q._px.toFixed(1) + ' ' + q._py.toFixed(1);
            pen = true;
          });
        });
        if (dstr) parts.push('<path d="' + dstr + '" fill="none" stroke="' + s.color + '" stroke-width="' + (s.width || 1.8) +
                             '" stroke-linejoin="round"' + (s.dash ? ' stroke-dasharray="' + s.dash + '"' : '') + '/>');
      });
      // Legend: inside the plot when it fills the panel, below it when a 1:1
      // profile is a thin band with room to spare (the usual case at 1:1).
      var need = series.length * 13 + 6, below = !tracks && (H - m.b - (oy + ch)) > need + 18;   // tracks take the room below
      var ly = below ? oy + ch + 30 : oy + 12, lx = ox + 8;
      series.forEach(function (s) {
        if (s.marker) parts.push('<circle cx="' + (lx + 11) + '" cy="' + ly + '" r="3" fill="' + s.color + '"/>');
        else parts.push('<line x1="' + lx + '" y1="' + ly + '" x2="' + (lx + 22) + '" y2="' + ly + '" stroke="' + s.color +
                   '" stroke-width="2"' + (s.dash ? ' stroke-dasharray="' + s.dash + '"' : '') + '/>');
        parts.push('<text x="' + (lx + 26) + '" y="' + (ly + 3) + '" font-size="10" fill="#333">' + esc(s.label) + '</text>');
        ly += 13;
      });
      parts.push('<text x="' + (o.ends ? ox + cw / 2 : ox + cw - 4) + '" y="' + (oy - 3) + '" font-size="10" text-anchor="' +
                 (o.ends ? 'middle' : 'end') + '" fill="#333">' +
                 (locked ? 'VE 1:1' : 'VE ×' + (ve >= 10 ? ve.toFixed(0) : ve.toFixed(1))) + '</text>');
      // Hover marks (positioned by the mouse handler; left out of the SVG download).
      parts.push('<line class="pc-guide" x1="0" y1="' + oy + '" x2="0" y2="' + gy1 + '" stroke="#333" stroke-dasharray="2 2" visibility="hidden"/>');
      parts.push('<circle class="pc-hit" r="4" fill="#ffeb3b" stroke="#333" stroke-width="1.5" visibility="hidden"/>');
      el.svg.setAttribute('viewBox', '0 0 ' + W + ' ' + H);
      el.svg.setAttribute('width', W); el.svg.setAttribute('height', H);
      el.svg.innerHTML = parts.join('');
      el.svg._geom = { ox: ox, sx: sx, L: L, Y: Y };
      paintStats();
    }

    // ---- hover -------------------------------------------------------------
    function svgXY(e) {
      var r = el.svg.getBoundingClientRect(), vb = el.svg.viewBox.baseVal;
      return [(e.clientX - r.left) * (vb.width / r.width), (e.clientY - r.top) * (vb.height / r.height)];
    }
    function onMove(e) {
      if (!data || !el.svg._geom || brushing) return;
      var g = el.svg._geom, p = svgXY(e), hit = null;
      if (hoverMode === 'nearest') {
        var bd = 18 * 18;
        data.series.forEach(function (s) { s.runs.forEach(function (run) { run.forEach(function (q) {
          if (isNaN(q._py)) return;
          var dd = (q._px - p[0]) * (q._px - p[0]) + (q._py - p[1]) * (q._py - p[1]);
          if (dd < bd) { bd = dd; hit = { d: q.d, series: s, point: q }; }
        }); }); });
      } else {
        var d = (p[0] - g.ox) / g.sx;
        if (d >= 0 && d <= g.L) hit = { d: d };
      }
      if (!hit) { leave(); return; }
      showHit(hit, e);
      if (o.onHover) o.onHover(hit);
    }
    function showHit(hit, e) {
      var g = el.svg._geom;
      var gx = g.ox + hit.d * g.sx;
      var guide = el.svg.querySelector('.pc-guide'), dot = el.svg.querySelector('.pc-hit');
      if (guide) { guide.setAttribute('x1', gx); guide.setAttribute('x2', gx); guide.setAttribute('visibility', 'visible'); }
      if (dot) {
        if (hit.point) { dot.setAttribute('cx', hit.point._px); dot.setAttribute('cy', hit.point._py); dot.setAttribute('visibility', 'visible'); }
        else dot.setAttribute('visibility', 'hidden');
      }
      var t = o.tip ? o.tip(hit) : null;
      el.read.textContent = (t && t.read) || '';
      if (!e || !t || !t.html) { el.tip.style.display = 'none'; return; }
      el.tip.innerHTML = t.html;
      el.tip.style.display = 'block';
      // Keep the box inside the panel: flip to the left of the cursor near the right edge.
      var br = el.body.getBoundingClientRect(), bw = el.body.clientWidth, tw = el.tip.offsetWidth;
      var px = e.clientX - br.left;
      el.tip.style.left = (px + 14 + tw > bw ? px - tw - 10 : px + 14) + 'px';
      el.tip.style.top = Math.max(4, Math.min(el.body.clientHeight - el.tip.offsetHeight - 4, e.clientY - br.top - 10)) + 'px';
    }
    function hoverAt(d) {
      if (!data || !el.svg._geom) return;
      if (d == null || d < 0 || d > el.svg._geom.L) { clearHover(); return; }
      showHit({ d: d }, null);
    }
    function clearHover() {
      var guide = el.svg.querySelector('.pc-guide'), dot = el.svg.querySelector('.pc-hit');
      if (guide) guide.setAttribute('visibility', 'hidden');
      if (dot) dot.setAttribute('visibility', 'hidden');
      el.tip.style.display = 'none';
      el.read.innerHTML = '&nbsp;';
    }
    function leave() {
      clearHover();
      if (o.onHover) o.onHover(null);
    }
    el.svg.addEventListener('mousemove', onMove);
    el.svg.addEventListener('mouseleave', leave);

    // ---- brushed window ----------------------------------------------------
    function paintStats() {
      if (!sel || !data || !data.series.length) { el.stats.style.display = 'none'; el.stats.innerHTML = ''; return; }
      var d0 = Math.min(sel.d0, sel.d1), d1 = Math.max(sel.d0, sel.d1);
      var h = '<div class="profile-stats-head">Window ' + fmtD(d0) + ' – ' + fmtD(d1) + ' (' + fmtD(d1 - d0) + ')' +
              '<button type="button" class="profile-stats-x" title="Clear the window">×</button></div>' +
              '<table><thead><tr><th>' + esc(o.statsName || 'series') + '</th><th title="Arithmetic mean of the samples in the window">mean z (m)</th>' +
              '<th title="Least-squares line through the samples, signed along the x axis">slope</th>' +
              '<th title="Twice the quadratic coefficient of a least-squares parabola; positive = concave up">curvature (1/km)</th><th>n</th></tr></thead><tbody>';
      data.series.forEach(function (s) {
        var st = windowStats(s, sel);
        h += '<tr><td><span class="profile-tip-sw" style="background:' + s.color + '"></span>' + esc(s.short || s.label) + '</td>';
        if (st.n < 2) { h += '<td colspan="3" class="profile-stats-none">no data in window</td><td>' + st.n + '</td></tr>'; return; }
        h += '<td>' + st.mean.toFixed(1) + '</td><td>' + st.slopeDeg.toFixed(1) + '° (' + st.slopePct.toFixed(1) + ' %)</td>' +
             '<td>' + (isNaN(st.curvature) ? '—' : st.curvature.toFixed(3)) + '</td><td>' + st.n + '</td></tr>';
      });
      h += '</tbody></table>';
      el.stats.innerHTML = h;
      el.stats.style.display = 'block';
      el.stats.querySelector('.profile-stats-x').addEventListener('click', function () {
        sel = null; draw(); if (o.onChange) o.onChange();
      });
    }
    function chartD(clientX) {
      var g = el.svg._geom, r = el.svg.getBoundingClientRect();
      var x = (clientX - r.left) * (el.svg.viewBox.baseVal.width / r.width);
      return Math.max(0, Math.min(g.L, (x - g.ox) / g.sx));
    }
    LSTools.drag(el.svg, {
      onStart: function (e) {
        if (!data || !el.svg._geom || e.button !== 0) return false;
        brushing = true;
        sel = { d0: chartD(e.clientX), d1: chartD(e.clientX) };
        el.tip.style.display = 'none';
      },
      onMove: function (e) { if (brushing) { sel.d1 = chartD(e.clientX); draw(); } },
      onEnd: function (e, info) {
        if (!brushing) return;
        brushing = false;
        // A click (no real drag) clears the window instead of making a sliver.
        if (!e || Math.abs(e.clientX - info.startX) < 3) sel = null;
        draw();
        if (o.onChange) o.onChange();
      }
    });

    // ---- downloads ---------------------------------------------------------
    function downloadSvg() {
      if (!data) return;
      var clone = el.svg.cloneNode(true);
      clone.setAttribute('xmlns', 'http://www.w3.org/2000/svg');
      clone.setAttribute('font-family', 'Helvetica, Arial, sans-serif');
      clone.querySelectorAll('.pc-guide, .pc-hit').forEach(function (n) { n.remove(); });
      download(o.svgName ? o.svgName() : 'profile.svg', 'image/svg+xml',
               '<?xml version="1.0" encoding="UTF-8"?>\n' + clone.outerHTML);
    }
    function downloadCsv() {
      if (!data || !o.csv) return;
      var c = o.csv();
      if (c) download(c.name, 'text/csv', c.text);
    }

    // ---- API ---------------------------------------------------------------
    function setLocked(b) {
      locked = !!b;
      el.lock.classList.toggle('active', locked);
      el.lock.title = locked ? 'True aspect (1:1). Click to let the profile fill the panel.'
                             : 'Vertically exaggerated to fill the panel. Click for 1:1.';
      draw();
    }
    function setStatus(t) { statusText = t || ''; el.status.textContent = statusText; el.status.title = statusText; }
    function setTitle(t) { titleText = t || ''; el.title.textContent = titleText; el.title.style.display = titleText ? '' : 'none'; }
    setTitle(o.title || '');
    return {
      el: el,
      open: function () { if (!fp.isOpen()) fp.open(); },
      close: function () { data = null; sel = null; leave(); if (fp.isOpen()) fp.close(); },
      isOpen: fp.isOpen,
      setData: function (d) { data = d; draw(); },
      data: function () { return data; },
      draw: draw,
      setStatus: setStatus, setTitle: setTitle,
      locked: function () { return locked; }, setLocked: setLocked,
      window: function () { return sel ? { d0: Math.min(sel.d0, sel.d1), d1: Math.max(sel.d0, sel.d1) } : null; },
      setWindow: function (w) { sel = w ? { d0: w.d0, d1: w.d1 } : null; draw(); },
      hoverAt: hoverAt,
      stats: function (s) { return sel ? windowStats(s, sel) : null; }
    };
  }

  return { create: create, CURSOR_PAINT: CURSOR_PAINT, fmtD: fmtD, esc: esc, download: download };
})();
