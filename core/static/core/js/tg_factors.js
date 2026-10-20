/* tg_factors.js — the refine tool's factor panels. Global `window.TgFactors`.
 *
 * Two floating panels over the full map (owner, 2026-10-18), opened from a
 * refine result:
 *
 *   Factors along the line: landslidescience's profile chart (LSProfileChart,
 *     synced: tools/shared.json) with 1:1, hover, a brushed window, SVG and
 *     CSV, and under it one track per factor sharing its x axis: the value the
 *     cost reads (grade, side slope, TSA, cut and fill, TWI, distance since a
 *     reversal, bend radius) over a band shaded by the extra effort per metre
 *     that factor charges there. The window also gets the effort in it, per
 *     factor, for the line and the line as drawn.
 *   Cost space: any two factors as axes. Behind, what the two charge
 *     together per metre at each pair of values (k_x f_x + k_y f_y, log
 *     colour); over it, a density of the line's length in the space (metres
 *     per cell). Hover a cell to see where on the map those metres are.
 *
 * Hover anywhere (either panel, or the line on the map's samples) marks the
 * point on the map and in both panels.
 *
 *   TgFactors.show(app, result, index, label)   // index: candidate, or -1 for the line itself
 *   TgFactors.analyse(app, kind, key, label)    // any trail / track / alignment: scored in the
 *                                               // worker with your refine settings, no search
 *   TgFactors.close() / isOpen() / onStyleReload()
 */
window.TgFactors = (function () {
  'use strict';

  var FREE = 0.01;                 // a curve's floor (settings.FREE)
  var TRACK_H = 34, TRACK_GAP = 5;
  var SRC_PT = 'tgf-cursor', SRC_CELL = 'tgf-cell';
  var TRACKS = [
    { key: 'grade', label: 'Grade', unit: '%', dom: [0, 60] },
    { key: 'slope', label: 'Terrain slope', unit: '%', dom: [0, 100] },
    { key: 'tsa', label: 'TSA', unit: '°', dom: [0, 90] },
    { key: 'cutfill', label: 'Cut and fill', unit: 'm', dom: [-1, 0.5], zero: true },
    { key: 'wet', label: 'Wet ground', unit: 'TWI', dom: [0, 16] },
    { key: 'drainage', label: 'Drainage run', unit: 'm', dom: [0, 300] },
    { key: 'bend', label: 'Bend radius', unit: 'm', dom: [0.5, 100], log: true },
    { key: 'existing', label: 'Off existing tread', unit: 'm', dom: [0, 25], only: 'existing' },
    { key: 'climb', label: 'Height lost', unit: 'm', dom: [0, 5], only: 'climb' }
  ];
  var SPACE = ['grade', 'slope', 'tsa', 'cutfill', 'wet', 'drainage', 'existing'];
  var SPACE_DOM = { grade: [0, 60], slope: [0, 100], tsa: [0, 90], cutfill: [-1, 0.5], wet: [0, 16], drainage: [0, 300], existing: [0, 25] };
  var EXTRA = ['grade', 'falltread', 'slope', 'tsa', 'cutfill', 'wet', 'drainage', 'bend', 'existing', 'climb'];
  var LABEL = { grade: 'Grade', falltread: 'Fall-line tread', slope: 'Terrain slope', tsa: 'TSA', cutfill: 'Cut and fill',
                wet: 'Wet ground', drainage: 'Drainage', bend: 'Bends', existing: 'Off existing tread', climb: 'Excess climb' };

  var app = null, res = null, idx = 0, L = null, I = null;
  var chart = null, profOpts = null, profPanel = null, spacePanel = null, spaceFp = null, winEl = null;
  var spX = 'grade', spY = 'slope', spGeom = null, hoverI = null;

  function esc(t) { return LSProfileChart.esc(t); }
  function n(v, d) { return v == null || !isFinite(v) ? '–' : (+v).toFixed(d == null ? 0 : d); }
  function curveFn(curve) {
    var xs = curve.map(function (c) { return c[0]; });
    var ly = curve.map(function (c) { return Math.log(c[1] > 0 ? c[1] : FREE); });
    return function (x) {
      if (xs.length === 1 || x <= xs[0]) return Math.exp(ly[0]);
      if (x >= xs[xs.length - 1]) return Math.exp(ly[ly.length - 1]);
      var i = 0; while (i < xs.length - 2 && x >= xs[i + 1]) i++;
      var t = (x - xs[i]) / Math.max(xs[i + 1] - xs[i], 1e-12);
      return Math.exp(ly[i] + (ly[i + 1] - ly[i]) * t);
    };
  }

  // A scored line with what the panels need per sample.
  function line(c, label) {
    var p = c.profile, t = c.tracks, nS = p.d.length, ds = new Array(nS), i;
    for (i = 0; i < nS; i++) ds[i] = i === 0 ? (p.d[1] - p.d[0]) / 2 : i === nS - 1 ? (p.d[nS - 1] - p.d[nS - 2]) / 2 : (p.d[i + 1] - p.d[i - 1]) / 2;
    var dens = p.d.map(function (_, j) {
      return 1 + EXTRA.reduce(function (a, k) { return a + (t.cost[k] ? t.cost[k][j] : 0); }, 0);
    });
    return { label: label, c: c, d: p.d, ground: p.ground, design: p.design, t: t, ds: ds, dens: dens };
  }
  function nearest(A, d) {
    var i = d3.bisectLeft(A.d, d);
    if (i <= 0) return 0;
    if (i >= A.d.length) return A.d.length - 1;
    return d - A.d[i - 1] < A.d[i] - d ? i - 1 : i;
  }

  // ---- panels ---------------------------------------------------------------------------
  function host() { return document.querySelector('main') || document.body; }
  function makePanels() {
    if (profPanel) return;
    profPanel = document.createElement('div');
    profPanel.className = 'float-panel tgf-prof hidden';
    host().appendChild(profPanel);
    profOpts = {
      panel: profPanel,
      makePanel: function (el, o) { return LSFloat.make(el, o); },
      title: 'Factors along the line',
      statsName: 'line',
      hover: 'column',
      marginRight: 112,
      buttons: [{ cls: 'tgf-space-btn', label: 'Cost space', title: 'Any two factors as axes: what they charge, and where this line spends its length',
                  onClick: function () { openSpace(); } }],
      tracks: { height: TRACKS.length * (TRACK_H + TRACK_GAP), draw: drawTracks },   // set per line in show()
      tip: tip,
      onHover: function (hit) { setHover(hit ? nearest(L, hit.d) : null, 'chart'); },
      onChange: paintWindow,
      onClose: function () { clearMap(); if (spaceFp) spaceFp.close(); },
      csv: csv,
      svgName: function () { return 'refine_factors.svg'; }
    };
    chart = LSProfileChart.create(profOpts);       // it reads profOpts.tracks at every draw
    chart.setLocked(false);              // exaggerated: the profile fills its part of the panel; 1:1 is a click away
    winEl = document.createElement('div');
    winEl.className = 'tgf-window';
    profPanel.appendChild(winEl);

    spacePanel = document.createElement('div');
    spacePanel.className = 'float-panel tgf-space hidden';
    spacePanel.innerHTML =
      '<div class="float-header profile-header"><span class="profile-title">Cost space</span>' +
      '<label class="tgf-ax">across <select id="tgf-x"></select></label><label class="tgf-ax">up <select id="tgf-y"></select></label>' +
      '<button type="button" class="profile-close" title="Close">&times;</button></div>' +
      '<div class="tgf-space-body"><svg></svg></div>' +
      '<div class="profile-read tgf-space-read">&nbsp;</div>';
    host().appendChild(spacePanel);
    ['tgf-x', 'tgf-y'].forEach(function (id, j) {
      var sel = spacePanel.querySelector('#' + id);
      SPACE.forEach(function (k) {
        var o = document.createElement('option'); o.value = k; o.textContent = LABEL[k]; sel.appendChild(o);
      });
      sel.value = j ? spY : spX;
      sel.addEventListener('change', function () { if (j) spY = sel.value; else spX = sel.value; drawSpace(); });
    });
    spaceFp = LSFloat.make(spacePanel, { handle: spacePanel.querySelector('.float-header'), close: spacePanel.querySelector('.profile-close'),
                                         onResize: drawSpace });
    var svg = spacePanel.querySelector('svg');
    svg.addEventListener('mousemove', spaceMove);
    svg.addEventListener('mouseleave', function () { setCell(null); setHover(null, 'space'); });
  }
  function openSpace() { spaceFp.open(); drawSpace(); }

  // ---- the stack ------------------------------------------------------------------------
  var costColor = null;
  function shown() {
    return TRACKS.filter(function (T) { return !T.only || (L && L.t[T.key] && L.t[T.key].some(function (v) { return v > 0; })); });
  }
  function drawTracks(g) {
    if (!L) return '';
    costColor = costColor || d3.scaleSequentialLog(d3.interpolateYlOrBr).domain([0.05, 5]).clamp(true);
    var parts = [], y = g.top, X = function (d) { return g.ox + d * g.sx; }, nS = L.d.length;
    shown().forEach(function (T) {
      var ys = (T.log ? d3.scaleLog() : d3.scaleLinear()).domain(T.dom).range([y + TRACK_H, y]).clamp(true);
      parts.push('<rect x="' + g.ox + '" y="' + y + '" width="' + g.width + '" height="' + TRACK_H + '" fill="#fafaf8" stroke="#e4e4e0"/>');
      var cost = L.t.cost[T.key] || [], bw = Math.max(1, g.width / nS + 0.6);
      for (var i = 0; i < nS; i++) {
        var v = cost[i];
        if (!(v >= 0.05)) continue;
        parts.push('<rect x="' + (X(L.d[i]) - bw / 2).toFixed(1) + '" y="' + y + '" width="' + bw.toFixed(1) + '" height="' + TRACK_H +
                   '" fill="' + costColor(v) + '" fill-opacity="0.8"/>');
      }
      if (T.zero) parts.push('<line x1="' + g.ox + '" x2="' + (g.ox + g.width) + '" y1="' + ys(0) + '" y2="' + ys(0) + '" stroke="#bbb"/>');
      if (I && I !== L) parts.push('<path d="' + path(I, T, ys, X) + '" fill="none" stroke="#999" stroke-width="0.9" stroke-dasharray="3 2"/>');
      parts.push('<path d="' + path(L, T, ys, X) + '" fill="none" stroke="#222" stroke-width="1.1"/>');
      parts.push('<text x="' + (g.ox - 4) + '" y="' + (y + 8) + '" font-size="9" text-anchor="end" fill="#777">' + T.dom[1] + '</text>');
      parts.push('<text x="' + (g.ox - 4) + '" y="' + (y + TRACK_H) + '" font-size="9" text-anchor="end" fill="#777">' + T.dom[0] + '</text>');
      var tot = L.c.contrib[T.key] || 0;
      if (T.key === 'grade') tot += L.c.contrib.falltread || 0;
      parts.push('<text x="' + (g.ox + g.width + 6) + '" y="' + (y + 12) + '" font-size="11" font-weight="600" fill="#333">' + esc(T.label) + '</text>');
      parts.push('<text x="' + (g.ox + g.width + 6) + '" y="' + (y + 26) + '" font-size="10" fill="#666">' + esc(T.unit) + ' · +' + n(tot) + ' m</text>');
      y += TRACK_H + TRACK_GAP;
    });
    return parts.join('');
  }
  function path(A, T, ys, X) {
    var v = A.t[T.key], s = '';
    for (var i = 0; i < A.d.length; i++) s += (i ? 'L' : 'M') + X(A.d[i]).toFixed(1) + ' ' + ys(v[i]).toFixed(1);
    return s;
  }
  function tip(hit) {
    var i = nearest(L, hit.d), t = L.t;
    var cf = t.cutfill[i] < 0 ? 'cut ' + n(-t.cutfill[i], 2) : 'fill ' + n(t.cutfill[i], 2);
    return { read: n(L.d[i]) + ' m · grade ' + n(t.grade[i], 1) + ' % · side ' + n(t.slope[i]) + ' % · TSA ' + n(t.tsa[i]) + '° · ' + cf +
                   ' m · TWI ' + n(t.wet[i], 1) + ' · bend r ' + (t.bend[i] >= 1000 ? '–' : n(t.bend[i], 1) + ' m') + ' · effort ×' + n(L.dens[i], 2) };
  }
  function csv() {
    var keys = TRACKS.map(function (T) { return T.key; });
    var rows = ['d_m,lon,lat,ground_m,design_m,' + keys.join(',') + ',' + EXTRA.map(function (k) { return 'extra_' + k + '_m_per_m'; }).join(',')];
    for (var i = 0; i < L.d.length; i++) {
      rows.push([L.d[i], L.t.lonlat[i][0], L.t.lonlat[i][1], L.ground[i], L.design[i]].concat(
        keys.map(function (k) { return L.t[k] ? L.t[k][i] : ""; }), EXTRA.map(function (k) { return L.t.cost[k] ? L.t.cost[k][i] : 0; })).join(','));
    }
    return { name: 'refine_factors.csv', text: rows.join('\n') + '\n' };
  }
  function paintWindow() {
    var w = chart && chart.window();
    if (!w || !L) { winEl.innerHTML = ''; winEl.style.display = 'none'; return; }
    var lines = I && I !== L ? [L, I] : [L];
    var rows = lines.map(function (A) {
      var len = 0, climb = 0, desc = 0, gmax = 0, eff = {}, prev = null;
      EXTRA.forEach(function (k) { eff[k] = 0; });
      for (var i = 0; i < A.d.length; i++) {
        if (A.d[i] < w.d0 || A.d[i] > w.d1) continue;
        len += A.ds[i]; gmax = Math.max(gmax, A.t.grade[i]);
        if (prev != null) { var dz = A.design[i] - A.design[prev]; if (dz > 0) climb += dz; else desc -= dz; }
        prev = i;
        EXTRA.forEach(function (k) { if (A.t.cost[k]) eff[k] += A.t.cost[k][i] * A.ds[i]; });
      }
      var extra = EXTRA.reduce(function (a, k) { return a + eff[k]; }, 0);
      return { A: A, len: len, climb: climb, desc: desc, gmax: gmax, eff: eff, total: len + extra };
    });
    var h = '<div class="profile-stats-head">Effort in the window (per-metre charges; the steepest-pitch charge is the whole line\'s)</div><table><thead><tr><th></th>' +
            rows.map(function (r) { return '<th>' + esc(r.A.label) + '</th>'; }).join('') + '</tr></thead><tbody>';
    function row(name, f) { h += '<tr><td>' + name + '</td>' + rows.map(function (r) { return '<td>' + f(r) + '</td>'; }).join('') + '</tr>'; }
    row('Length on the map', function (r) { return n(r.len) + ' m'; });
    row('Climb / descent', function (r) { return n(r.climb, 1) + ' / ' + n(r.desc, 1) + ' m'; });
    row('Steepest grade', function (r) { return n(r.gmax, 1) + ' %'; });
    EXTRA.forEach(function (k) { if (rows.some(function (r) { return r.eff[k] >= 0.5; })) row('+ ' + LABEL[k], function (r) { return n(r.eff[k]) + ' m'; }); });
    row('<b>Effort</b>', function (r) { return '<b>' + n(r.total) + ' m</b> ×' + n(r.total / Math.max(r.len, 1e-6), 2); });
    winEl.innerHTML = h + '</tbody></table>';
    winEl.style.display = 'block';
  }

  // ---- cost space -----------------------------------------------------------------------
  var NX = 48, NY = 32;
  function drawSpace() {
    if (!spacePanel || !L || spacePanel.classList.contains('hidden')) return;
    var svg = spacePanel.querySelector('svg'), body = spacePanel.querySelector('.tgf-space-body');
    var r = body.getBoundingClientRect(), W = Math.max(240, r.width), H = Math.max(160, r.height);
    var m = { l: 44, r: 92, t: 8, b: 30 }, w = W - m.l - m.r, h = H - m.t - m.b;
    var F = res.settings.factors, fx = curveFn(F[spX].curve), fy = curveFn(F[spY].curve), kx = F[spX].k, ky = F[spY].k;
    var dx = SPACE_DOM[spX], dy = SPACE_DOM[spY];
    var heat = d3.scaleSequentialLog(function (t) { return d3.interpolateGreys(0.04 + 0.7 * t); }).domain([0.02, 50]).clamp(true);
    var parts = ['<rect width="' + W + '" height="' + H + '" fill="#fff"/>'], a, b;
    var gw = w / NX, gh = h / NY;
    for (a = 0; a < NX; a++) for (b = 0; b < NY; b++) {
      var vx = dx[0] + (a + 0.5) / NX * (dx[1] - dx[0]), vy = dy[0] + (b + 0.5) / NY * (dy[1] - dy[0]);
      parts.push('<rect x="' + (m.l + a * gw).toFixed(1) + '" y="' + (m.t + h - (b + 1) * gh).toFixed(1) + '" width="' + (gw + 0.4).toFixed(1) +
                 '" height="' + (gh + 0.4).toFixed(1) + '" fill="' + heat(kx * fx(vx) + ky * fy(vy)) + '"/>');
    }
    // The line's length in each cell, the secondary density.
    var cells = {}, cellOf = [], mx = 0;
    for (var i = 0; i < L.d.length; i++) {
      var ca = Math.max(0, Math.min(NX - 1, Math.floor((L.t[spX][i] - dx[0]) / (dx[1] - dx[0]) * NX)));
      var cb = Math.max(0, Math.min(NY - 1, Math.floor((L.t[spY][i] - dy[0]) / (dy[1] - dy[0]) * NY)));
      var key = ca + ',' + cb;
      (cells[key] = cells[key] || { a: ca, b: cb, m: 0, i: [] }).m += L.ds[i];
      cells[key].i.push(i);
      cellOf.push(key);
    }
    Object.keys(cells).forEach(function (k) { mx = Math.max(mx, cells[k].m); });
    var dcol = d3.scaleSequentialSqrt(d3.interpolateBlues).domain([0, mx]);
    Object.keys(cells).forEach(function (k) {
      var c = cells[k];
      parts.push('<rect class="tgf-cellr" x="' + (m.l + c.a * gw + 0.6).toFixed(1) + '" y="' + (m.t + h - (c.b + 1) * gh + 0.6).toFixed(1) +
                 '" width="' + Math.max(1, gw - 1.2).toFixed(1) + '" height="' + Math.max(1, gh - 1.2).toFixed(1) + '" fill="' + dcol(c.m) +
                 '" fill-opacity="' + (0.35 + 0.6 * Math.sqrt(c.m / mx)).toFixed(2) + '" stroke="#1864ab" stroke-width="0.6"/>');
    });
    var X = d3.scaleLinear().domain(dx).range([m.l, m.l + w]), Y = d3.scaleLinear().domain(dy).range([m.t + h, m.t]);
    X.ticks(5).forEach(function (v) { parts.push('<text x="' + X(v) + '" y="' + (m.t + h + 12) + '" font-size="10" fill="#555" text-anchor="middle">' + v + '</text>'); });
    Y.ticks(4).forEach(function (v) { parts.push('<text x="' + (m.l - 4) + '" y="' + (Y(v) + 3) + '" font-size="10" fill="#555" text-anchor="end">' + v + '</text>'); });
    parts.push('<text x="' + (m.l + w / 2) + '" y="' + (H - 4) + '" font-size="10" fill="#333" text-anchor="middle">' + esc(LABEL[spX]) + ' (' + unit(spX) + ')</text>');
    parts.push('<text x="12" y="' + (m.t + h / 2) + '" font-size="10" fill="#333" text-anchor="middle" transform="rotate(-90 12 ' + (m.t + h / 2) + ')">' +
               esc(LABEL[spY]) + ' (' + unit(spY) + ')</text>');
    var lx = m.l + w + 10;
    [0.05, 0.5, 5, 50].forEach(function (v, j) {
      parts.push('<rect x="' + lx + '" y="' + (m.t + j * 16) + '" width="12" height="12" fill="' + heat(v) + '" stroke="#ccc"/>' +
                 '<text x="' + (lx + 16) + '" y="' + (m.t + j * 16 + 10) + '" font-size="9" fill="#555">+' + v + ' m/m</text>');
    });
    parts.push('<rect x="' + lx + '" y="' + (m.t + 74) + '" width="12" height="12" fill="' + dcol(mx) + '" stroke="#1864ab"/>' +
               '<text x="' + (lx + 16) + '" y="' + (m.t + 84) + '" font-size="9" fill="#555">' + n(mx) + ' m of line</text>');
    parts.push('<rect class="tgf-hcell" fill="none" stroke="#e8590c" stroke-width="2" visibility="hidden"/>');
    parts.push('<circle class="tgf-hpt" r="5" fill="none" stroke="#e8590c" stroke-width="2" visibility="hidden"/>');
    svg.setAttribute('viewBox', '0 0 ' + W + ' ' + H); svg.setAttribute('width', W); svg.setAttribute('height', H);
    svg.innerHTML = parts.join('');
    spGeom = { m: m, w: w, h: h, gw: gw, gh: gh, dx: dx, dy: dy, cells: cells, X: X, Y: Y };
    paintSpaceHover();
  }
  function unit(k) { return { grade: '%', slope: '%', tsa: '°', cutfill: 'm', wet: 'TWI', drainage: 'm', existing: 'm' }[k]; }
  function spaceMove(e) {
    if (!spGeom) return;
    var svg = spacePanel.querySelector('svg'), r = svg.getBoundingClientRect(), vb = svg.viewBox.baseVal;
    var px = (e.clientX - r.left) * vb.width / r.width, py = (e.clientY - r.top) * vb.height / r.height, g = spGeom;
    var a = Math.floor((px - g.m.l) / g.gw), b = Math.floor((g.m.t + g.h - py) / g.gh);
    if (a < 0 || a >= NX || b < 0 || b >= NY) { setCell(null); return; }
    setCell(g.cells[a + ',' + b] || { a: a, b: b, m: 0, i: [] });
  }
  function setCell(c) {
    if (!spGeom) return;
    var rect = spacePanel.querySelector('.tgf-hcell'), read = spacePanel.querySelector('.tgf-space-read'), g = spGeom;
    if (!c) { rect.setAttribute('visibility', 'hidden'); read.innerHTML = '&nbsp;'; cellOnMap([]); return; }
    rect.setAttribute('x', g.m.l + c.a * g.gw); rect.setAttribute('y', g.m.t + g.h - (c.b + 1) * g.gh);
    rect.setAttribute('width', g.gw); rect.setAttribute('height', g.gh); rect.setAttribute('visibility', 'visible');
    var x0 = g.dx[0] + c.a / NX * (g.dx[1] - g.dx[0]), x1 = x0 + (g.dx[1] - g.dx[0]) / NX;
    var y0 = g.dy[0] + c.b / NY * (g.dy[1] - g.dy[0]), y1 = y0 + (g.dy[1] - g.dy[0]) / NY;
    var F = res.settings.factors, cost = F[spX].k * curveFn(F[spX].curve)((x0 + x1) / 2) + F[spY].k * curveFn(F[spY].curve)((y0 + y1) / 2);
    read.textContent = LABEL[spX] + ' ' + n(x0, 1) + '–' + n(x1, 1) + ' ' + unit(spX) + ', ' + LABEL[spY] + ' ' + n(y0, 1) + '–' + n(y1, 1) + ' ' + unit(spY) +
                       ': +' + n(cost, 2) + ' m per m · ' + n(c.m) + ' m of the line here' + (c.i.length ? ' (shown on the map)' : '');
    cellOnMap(c.i);
  }
  function paintSpaceHover() {
    if (!spGeom) return;
    var pt = spacePanel.querySelector('.tgf-hpt');
    if (hoverI == null) { pt.setAttribute('visibility', 'hidden'); return; }
    var x = Math.max(spGeom.dx[0], Math.min(spGeom.dx[1], L.t[spX][hoverI])), y = Math.max(spGeom.dy[0], Math.min(spGeom.dy[1], L.t[spY][hoverI]));
    pt.setAttribute('cx', spGeom.X(x)); pt.setAttribute('cy', spGeom.Y(y)); pt.setAttribute('visibility', 'visible');
  }

  // ---- hover and map --------------------------------------------------------------------
  function setHover(i, from) {
    hoverI = i;
    if (from !== 'chart' && chart) chart.hoverAt(i == null ? null : L.d[i]);
    paintSpaceHover();
    var src = app && app.map.getSource(SRC_PT);
    if (src) src.setData({ type: 'FeatureCollection', features: i == null ? [] :
      [{ type: 'Feature', properties: {}, geometry: { type: 'Point', coordinates: L.t.lonlat[i] } }] });
  }
  function cellOnMap(ids) {
    var src = app && app.map.getSource(SRC_CELL);
    if (src) src.setData({ type: 'FeatureCollection', features: ids.map(function (i) {
      return { type: 'Feature', properties: {}, geometry: { type: 'Point', coordinates: L.t.lonlat[i] } };
    }) });
  }
  function addLayers() {
    var map = app.map, empty = { type: 'FeatureCollection', features: [] };
    if (!map.getSource(SRC_CELL)) map.addSource(SRC_CELL, { type: 'geojson', data: empty });
    if (!map.getSource(SRC_PT)) map.addSource(SRC_PT, { type: 'geojson', data: empty });
    if (!map.getLayer(SRC_CELL)) map.addLayer({ id: SRC_CELL, type: 'circle', source: SRC_CELL,
      paint: { 'circle-radius': 4, 'circle-color': '#e8590c', 'circle-stroke-color': '#fff', 'circle-stroke-width': 1 } });
    if (!map.getLayer(SRC_PT)) map.addLayer({ id: SRC_PT, type: 'circle', source: SRC_PT, paint: LSProfileChart.CURSOR_PAINT });
  }
  function clearMap() { setHover(null, 'close'); cellOnMap([]); }
  function onMapMove(e) {
    if (!L || !isOpen()) return;
    var map = app.map, best = -1, bd = 14 * 14;
    for (var i = 0; i < L.t.lonlat.length; i++) {
      var p = map.project(L.t.lonlat[i]), dd = (p.x - e.point.x) * (p.x - e.point.x) + (p.y - e.point.y) * (p.y - e.point.y);
      if (dd < bd) { bd = dd; best = i; }
    }
    if (best >= 0) setHover(best, 'map');
    else if (hoverI != null) setHover(null, 'map');
  }

  // ---- API ------------------------------------------------------------------------------
  function show(a, result, index, label) {
    if (!a || !result || !result.input.tracks) return;
    if (!app || app.map !== a.map) { a.map.on('mousemove', onMapMove); }
    app = a;
    res = result; idx = index;
    I = line(result.input, label || 'As drawn');
    L = index >= 0 ? line(result.candidates[index], result.candidates[index].label) : I;
    makePanels();
    addLayers();
    chart.setTitle('Factors along: ' + L.label);
    var series = [
      { id: 'ground', label: 'ground', color: '#8a7a66', width: 1, runs: [L.d.map(function (d, i) { return { d: d, z: L.ground[i] }; })] },
      { id: 'design', label: L.label + ': design grade line', short: L.label, color: '#1f6f8b', width: 2,
        runs: [L.d.map(function (d, i) { return { d: d, z: L.design[i] }; })] }
    ];
    if (I !== L) series.push({ id: 'input', label: 'As drawn', color: '#999', dash: '4 3', width: 1.2,
                               runs: [I.d.map(function (d, i) { return { d: d, z: I.design[i] }; })] });
    profOpts.tracks.height = shown().length * (TRACK_H + TRACK_GAP);
    chart.setStatus('equivalent effort ' + n(L.c.total) + ' m for ' + n(L.c.length_m) + ' m (×' + n(L.c.total / L.c.length_m, 2) + ')');
    chart.open();
    chart.setData({ length: Math.max(L.d[L.d.length - 1], I.d[I.d.length - 1]), series: series });
    paintWindow();
    drawSpace();
  }
  // Any line: a baseline job (core.refine_views.api_factors), polled like a refine job.
  var analysing = 0;
  function analyse(a, kind, key, label) {
    if (!app || app.map !== a.map) { a.map.on('mousemove', onMapMove); }
    app = a;
    makePanels();
    var my = ++analysing;
    chart.setTitle('Factors along: ' + label);
    chart.setStatus('reading the terrain and scoring… (a minute or less)');
    chart.open();
    var csrf = (document.querySelector('meta[name=csrf-token]') || {}).content || '';
    function req(method, url, body) {
      return fetch(url, { method: method, credentials: 'same-origin', body: body ? JSON.stringify(body) : undefined,
                          headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf } })
        .then(function (r) { return r.json().then(function (j) { if (r.status >= 400) throw new Error(j.error || ('Error ' + r.status)); return j; }); });
    }
    req('POST', '/api/factors/', { kind: kind, key: key }).then(function (j) {
      (function poll() {
        if (my !== analysing || !isOpen()) return;
        req('GET', '/api/refine/jobs/' + j.job + '/').then(function (st) {
          if (my !== analysing) return;
          if (st.status === 'done') { show(a, st.result, -1, label); return; }
          if (st.status === 'failed') { chart.setStatus('failed: ' + (st.error || 'unknown error')); return; }
          chart.setStatus((st.stage || 'queued') + '…');
          setTimeout(poll, 1500);
        }).catch(function (e) { chart.setStatus(e.message); });
      })();
    }).catch(function (e) { chart.setStatus(e.message); });
  }

  function close() {
    if (chart) chart.close();
    if (spaceFp) spaceFp.close();
    if (app) clearMap();
  }
  function isOpen() { return !!(chart && chart.isOpen()); }
  return {
    show: show,
    analyse: analyse,
    close: close,
    isOpen: isOpen,
    onStyleReload: function () { if (app && isOpen()) addLayers(); }
  };
})();
