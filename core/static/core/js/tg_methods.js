/* The methods page's route sandbox (core/templates/core/methods_sandbox.html).
 *
 * Reads one precomputed file per surface (manage.py build_methods analytic):
 * the terrain on a coarse grid; at each node of the weight grid the engine's
 * line and the formula's; and along each edge of the grid ("rails") the
 * lines traced in steps between the two nodes, each with whether it is a
 * jump from the one before. The position is (u, v) in grid index space,
 * u along grade, v along corners; one of them is always whole (the point
 * rides a rail). Nothing is computed here but drawing, morphing between
 * consecutive steps of one route, and the cost-per-metre-climbed curve,
 * which is the formula on the page (trailgeek_analysis/sandbox.py,
 * per_metre and climb_cost) drawn for the reader.
 */
(function () {
  'use strict';
  var files = [], cache = {}, doc = null;
  var state = { name: null, u: 2, v: 2, dig: true };
  var $ = function (id) { return document.getElementById(id); };
  var NS = 'http://www.w3.org/2000/svg';
  var colours = {};
  var css = function (v) {
    if (!(v in colours)) colours[v] = getComputedStyle(document.querySelector('.mt-page')).getPropertyValue(v).trim();
    return colours[v];
  };
  var DIG_SCALE = 8;            // a tick is this many metres long per metre of cut or fill
  var MORPH_N = 300;

  function load(name) {
    if (cache[name]) return Promise.resolve(cache[name]);
    var f = files.filter(function (x) { return x.name === name; })[0];
    return fetch(f.url).then(function (r) {
      if (!r.ok) throw new Error(r.status + ' ' + r.statusText);
      return r.json();
    }).then(function (d) { d._shade = null; d._contours = null; d._base = null; cache[name] = d; return d; });
  }

  // ---- terrain: hillshade and contours, once per surface ----

  function zAt(t, c, r) { return t.z[r * t.w + c]; }

  function shade(t) {
    var cv = document.createElement('canvas');
    cv.width = t.w; cv.height = t.h;
    var ctx = cv.getContext('2d'), img = ctx.createImageData(t.w, t.h);
    var zmin = Infinity, zmax = -Infinity, i;
    for (i = 0; i < t.z.length; i++) { zmin = Math.min(zmin, t.z[i]); zmax = Math.max(zmax, t.z[i]); }
    var az = 315 * Math.PI / 180, alt = 45 * Math.PI / 180;
    var lx = Math.cos(alt) * Math.sin(az), ly = Math.cos(alt) * Math.cos(az), lz = Math.sin(alt);
    for (var r = 0; r < t.h; r++) {
      for (var c = 0; c < t.w; c++) {
        var cl = Math.max(c - 1, 0), cr = Math.min(c + 1, t.w - 1), rd = Math.max(r - 1, 0), ru = Math.min(r + 1, t.h - 1);
        var dzdx = (zAt(t, cr, r) - zAt(t, cl, r)) / ((cr - cl) * t.res);
        var dzdy = (zAt(t, c, ru) - zAt(t, c, rd)) / ((ru - rd) * t.res);
        var nl = Math.sqrt(dzdx * dzdx + dzdy * dzdy + 1);
        var hs = Math.max(0, (-dzdx * lx - dzdy * ly + lz) / nl);
        var e = (zAt(t, c, r) - zmin) / Math.max(zmax - zmin, 1e-9);
        // A pale green-to-tan tint by elevation, under the shade.
        var base = [222 + 18 * e, 230 + 4 * e, 206 + 6 * e];
        var k = 0.55 + 0.45 * hs;
        var p = ((t.h - 1 - r) * t.w + c) * 4;       // north up
        img.data[p] = base[0] * k; img.data[p + 1] = base[1] * k; img.data[p + 2] = base[2] * k; img.data[p + 3] = 255;
      }
    }
    ctx.putImageData(img, 0, 0);
    return { canvas: cv, zmin: zmin, zmax: zmax };
  }

  // Marching squares: segments [x1, y1, x2, y2] in metres, per level.
  function contours(t, step) {
    var out = [], zmin = Infinity, zmax = -Infinity, i;
    for (i = 0; i < t.z.length; i++) { zmin = Math.min(zmin, t.z[i]); zmax = Math.max(zmax, t.z[i]); }
    for (var lz = Math.ceil(zmin / step) * step; lz <= zmax; lz += step) {
      // Nudged off the level, so a level that falls exactly on grid values
      // (a plane's rows) still crosses cell edges.
      var lev = lz + 1e-6, segs = [];
      for (var r = 0; r < t.h - 1; r++) {
        for (var c = 0; c < t.w - 1; c++) {
          var a = zAt(t, c, r), b = zAt(t, c + 1, r), d = zAt(t, c + 1, r + 1), e = zAt(t, c, r + 1);
          var lo = Math.min(a, b, d, e), hi = Math.max(a, b, d, e);
          if (lev < lo || lev >= hi) continue;
          var pts = [];
          var edge = function (z1, z2, x1, y1, x2, y2) {
            if ((z1 < lev) !== (z2 < lev)) {
              var f = (lev - z1) / (z2 - z1);
              pts.push(t.x0 + (x1 + f * (x2 - x1)) * t.res, t.y0 + (y1 + f * (y2 - y1)) * t.res);
            }
          };
          edge(a, b, c, r, c + 1, r); edge(b, d, c + 1, r, c + 1, r + 1);
          edge(d, e, c + 1, r + 1, c, r + 1); edge(e, a, c, r + 1, c, r);
          if (pts.length >= 4) segs.push(pts.slice(0, 4));
          if (pts.length === 8) segs.push(pts.slice(4, 8));
        }
      }
      out.push({ z: lz, segs: segs });
    }
    return out;
  }


  // ---- where we are: the lines at (u, v) ----

  var NI = function () { return doc.axes[0].values.length; };
  var NJ = function () { return doc.axes[1].values.length; };

  // The weight t of the way between two grid values, as sandbox.weights_at:
  // geometric between two positive weights, else linear.
  function between(a, b, t) { return a > 0 && b > 0 ? a * Math.pow(b / a, t) : a + (b - a) * t; }

  function weightAt(axis, x) {
    var vals = doc.axes[axis].values, i = Math.min(Math.floor(x), vals.length - 2), t = x - i;
    if (x >= vals.length - 1) return vals[vals.length - 1];
    return between(vals[i], vals[i + 1], t);
  }

  // The steps along the rail through the point, nodes included:
  // [{t, s (sample or node record), jump (from the one before)}].
  function railSteps(axis, i, j) {
    var r = doc.rails[axis + '_' + i + '_' + j];
    var a = doc.nodes[i + '_' + j], b = doc.nodes[(axis ? i + '_' + (j + 1) : (i + 1) + '_' + j)];
    var out = [{ t: 0, s: a, jump: false }];
    r.samples.forEach(function (s) { out.push({ t: s.t, s: s, jump: s.jump }); });
    out.push({ t: 1, s: b, jump: r.end_jump });
    return out;
  }

  // What to show at the current point: {engine, formula, rec (the nearer
  // step's record), morph: f or null, a, b (the two steps), jump}.
  function here() {
    var u = state.u, v = state.v, axis, i, j, t;
    if (Math.abs(u - Math.round(u)) < 1e-6 && Math.abs(v - Math.round(v)) < 1e-6) {
      var n = doc.nodes[Math.round(u) + '_' + Math.round(v)];
      return { rec: n, engine: n.engine, formula: n.formula, a: n, b: n, f: 0, jump: false, node: true };
    }
    if (Math.abs(v - Math.round(v)) < 1e-6) { axis = 0; i = Math.floor(u); j = Math.round(v); t = u - i; }
    else { axis = 1; i = Math.round(u); j = Math.floor(v); t = v - j; }
    var steps = railSteps(axis, i, j), k = 1;
    while (k < steps.length - 1 && steps[k].t < t) k++;
    var A = steps[k - 1], B = steps[k], f = (t - A.t) / Math.max(B.t - A.t, 1e-9);
    var near = f < 0.5 ? A : B;
    var out = { rec: near.s, a: A.s, b: B.s, f: f, jump: B.jump, node: false };
    if (B.jump) {
      out.engine = near.s.engine; out.formula = near.s.formula;
    } else {
      out.engine = morph(A.s.engine, B.s.engine, f, near.s.engine);
      out.formula = morph(A.s.formula, B.s.formula, f, near.s.formula);
    }
    return out;
  }

  function resample(xy, n) {
    var d = [0], i;
    for (i = 1; i < xy.length; i++) d.push(d[i - 1] + Math.hypot(xy[i][0] - xy[i - 1][0], xy[i][1] - xy[i - 1][1]));
    var L = d[d.length - 1], out = [], k = 0;
    for (i = 0; i < n; i++) {
      var s = L * i / (n - 1);
      while (k < d.length - 2 && d[k + 1] < s) k++;
      var f = (s - d[k]) / Math.max(d[k + 1] - d[k], 1e-9);
      out.push([xy[k][0] + f * (xy[k + 1][0] - xy[k][0]), xy[k][1] + f * (xy[k + 1][1] - xy[k][1])]);
    }
    return out;
  }

  // Where each of a line's ticks lies along it, as a share of its length
  // (its nearest vertex's), worked out once per line.
  function digShares(line) {
    if (line._digS) return line._digS;
    var xy = line.xy, d = [0], i;
    for (i = 1; i < xy.length; i++) d.push(d[i - 1] + Math.hypot(xy[i][0] - xy[i - 1][0], xy[i][1] - xy[i - 1][1]));
    var L = d[d.length - 1] || 1;
    line._digS = line.dig.map(function (t) {
      var best = 0, bd = Infinity;
      for (var k = 0; k < xy.length; k++) {
        var dd = (xy[k][0] - t[0]) * (xy[k][0] - t[0]) + (xy[k][1] - t[1]) * (xy[k][1] - t[1]);
        if (dd < bd) { bd = dd; best = k; }
      }
      return d[best] / L;
    });
    return line._digS;
  }

  // Between two steps of one route the plan slides from one to the other;
  // the numbers and the profile are the nearer step's. Its ticks move with
  // the line (owner, 2026-10-20: drawn where the nearer step's line was,
  // they sat up to 7 m off the line on the plane): each at its share of the
  // way along, across the slid line there.
  function morph(a, b, f, near) {
    if (!a || !b) return near || a || b;
    if (f <= 0) return a;
    if (f >= 1) return b;
    var A = resample(a.xy, MORPH_N), B = resample(b.xy, MORPH_N);
    var xy = A.map(function (p, i) { return [p[0] + f * (B[i][0] - p[0]), p[1] + f * (B[i][1] - p[1])]; });
    var src = f < 0.5 ? a : b, shares = digShares(src);
    var dig = src.dig.map(function (t, k) {
      var q = shares[k] * (MORPH_N - 1), i = Math.min(Math.floor(q), MORPH_N - 2), r = q - i;
      var x = xy[i][0] + r * (xy[i + 1][0] - xy[i][0]), y = xy[i][1] + r * (xy[i + 1][1] - xy[i][1]);
      var tx = xy[i + 1][0] - xy[i][0], ty = xy[i + 1][1] - xy[i][1], n = Math.hypot(tx, ty) || 1;
      return [x, y, -ty / n, tx / n, t[4]];
    });
    return Object.assign({}, near, { xy: xy, dig: dig });
  }

  // ---- the map ----

  function view(cv) {
    var t = doc.terrain;
    var span = (t.w - 1) * t.res, s = cv.width / span;
    return {
      x: function (x) { return (x - t.x0) * s; },
      y: function (y) { return (t.y0 + (t.h - 1) * t.res - y) * s; },
      s: s
    };
  }

  function path(ctx, v, xy) {
    ctx.beginPath();
    xy.forEach(function (p, i) { ctx[i ? 'lineTo' : 'moveTo'](v.x(p[0]), v.y(p[1])); });
  }

  // Every surface is symmetric about its input line (the x axis for the
  // round ones, x = 0 for the plane), so a line and its mirror image cost the
  // same: the formula's line is drawn on the side the engine's went.
  function side(xy, k) {
    var s = 0;
    xy.forEach(function (p) { s += p[k]; });
    return s;
  }

  function mirrored(eng, line) {
    var k = doc.surface.name === 'plane' ? 0 : 1;
    if (!eng || !line || side(eng.xy, k) * side(line.xy, k) >= 0) return line;
    var flip = function (p) { return k ? [p[0], -p[1]] : [-p[0], p[1]]; };
    return Object.assign({}, line, {
      xy: line.xy.map(flip),
      dig: line.dig.map(function (d) { return k ? [d[0], -d[1], d[2], -d[3], d[4]] : [-d[0], d[1], -d[2], d[3], d[4]]; })
    });
  }

  function ticks(ctx, v, line, dpr, alpha) {
    if (!line || !line.dig) return;
    ctx.lineWidth = 1.6 * dpr;
    ctx.globalAlpha = alpha;
    [[css('--mt-cut'), function (e) { return e < 0; }], [css('--mt-fill'), function (e) { return e > 0; }]].forEach(function (c) {
      ctx.strokeStyle = c[0];
      ctx.beginPath();                       // one path per colour: hundreds of strokes were slow
      line.dig.forEach(function (d) {
        if (!c[1](d[4])) return;
        var half = Math.abs(d[4]) * DIG_SCALE / 2;
        ctx.moveTo(v.x(d[0] - d[2] * half), v.y(d[1] - d[3] * half));
        ctx.lineTo(v.x(d[0] + d[2] * half), v.y(d[1] + d[3] * half));
      });
      ctx.stroke();
    });
    ctx.globalAlpha = 1;
  }

  // The shaded relief and contours, drawn once per surface and size.
  function base(cv, v, dpr) {
    if (doc._base && doc._base.width === cv.width) return doc._base;
    var t = doc.terrain;
    if (!doc._shade) doc._shade = shade(t);
    var relief = doc._shade.zmax - doc._shade.zmin;
    var step = relief > 120 ? 5 : 2, index = step * 5;
    if (!doc._contours) doc._contours = contours(t, step);
    var b = document.createElement('canvas');
    b.width = cv.width; b.height = cv.height;
    var ctx = b.getContext('2d');
    ctx.imageSmoothingEnabled = true;
    ctx.drawImage(doc._shade.canvas, 0, 0, b.width, b.height);
    doc._contours.forEach(function (L) {
      var major = Math.abs(L.z / index - Math.round(L.z / index)) < 1e-6;
      ctx.strokeStyle = major ? 'rgba(90,70,40,0.55)' : 'rgba(90,70,40,0.25)';
      ctx.lineWidth = (major ? 1.1 : 0.6) * dpr;
      ctx.beginPath();
      L.segs.forEach(function (sg) { ctx.moveTo(v.x(sg[0]), v.y(sg[1])); ctx.lineTo(v.x(sg[2]), v.y(sg[3])); });
      ctx.stroke();
    });
    b.step = step;
    doc._base = b;
    return b;
  }

  // The canvas's pixel size, set on load and resize only: reading its
  // layout size every frame forced a layout of the page each time.
  function sizeCanvas() {
    var cv = $('mt-canvas'), dpr = window.devicePixelRatio || 1, px = Math.round(cv.clientWidth * dpr) || 560;
    if (cv.width !== px) { cv.width = px; cv.height = px; }
  }

  function drawMap(h) {
    var cv = $('mt-canvas'), ctx = cv.getContext('2d');
    var dpr = window.devicePixelRatio || 1;
    var v = view(cv), b = base(cv, v, dpr), step = b.step;
    ctx.drawImage(b, 0, 0);
    ctx.lineJoin = 'round'; ctx.lineCap = 'round';
    ctx.strokeStyle = css('--mt-input'); ctx.lineWidth = 1.5 * dpr; ctx.setLineDash([2 * dpr, 4 * dpr]);
    path(ctx, v, doc.surface.line); ctx.stroke();
    var fl = mirrored(h.engine, h.formula);
    if (fl) {
      ctx.strokeStyle = css('--mt-exact'); ctx.lineWidth = 2.2 * dpr; ctx.setLineDash([7 * dpr, 5 * dpr]);
      path(ctx, v, fl.xy); ctx.stroke();
    }
    ctx.setLineDash([]);
    if (h.engine) {
      ctx.strokeStyle = 'rgba(255,255,255,0.85)'; ctx.lineWidth = 5 * dpr;
      path(ctx, v, h.engine.xy); ctx.stroke();
      ctx.strokeStyle = css('--mt-engine'); ctx.lineWidth = 2.6 * dpr;
      path(ctx, v, h.engine.xy); ctx.stroke();
    }
    if (state.dig) { ticks(ctx, v, fl, dpr, 0.5); ticks(ctx, v, h.engine, dpr, 1); }
    doc.surface.line.forEach(function (p, i) {
      ctx.fillStyle = i ? '#1f2a1f' : '#fff'; ctx.strokeStyle = '#1f2a1f'; ctx.lineWidth = 1.5 * dpr;
      ctx.beginPath(); ctx.arc(v.x(p[0]), v.y(p[1]), 5 * dpr, 0, 2 * Math.PI); ctx.fill(); ctx.stroke();
    });
    var bar = 50 * v.s, x0 = 12 * dpr, y0 = cv.height - 14 * dpr;
    ctx.fillStyle = 'rgba(255,255,255,0.85)'; ctx.fillRect(x0 - 6 * dpr, y0 - 17 * dpr, Math.max(bar, 150 * dpr) + 12 * dpr, 25 * dpr);
    ctx.strokeStyle = '#1f2a1f'; ctx.lineWidth = 2 * dpr;
    ctx.beginPath(); ctx.moveTo(x0, y0); ctx.lineTo(x0 + bar, y0); ctx.stroke();
    ctx.fillStyle = '#1f2a1f'; ctx.font = (11 * dpr) + 'px system-ui, sans-serif';
    ctx.fillText('50 m, contours every ' + step + ' m', x0, y0 - 5 * dpr);
  }

  // ---- the weight pad ----

  var PAD = { x0: 44, x1: 328, y0: 214, y1: 14 };

  function padX(u) { return PAD.x0 + u / (NI() - 1) * (PAD.x1 - PAD.x0); }
  function padY(v) { return PAD.y0 - v / (NJ() - 1) * (PAD.y0 - PAD.y1); }

  function drawPad() {
    var svg = $('mt-pad');
    svg.textContent = '';
    var i, j;
    for (j = 0; j < NJ(); j++) el('line', { x1: padX(0), x2: padX(NI() - 1), y1: padY(j), y2: padY(j), class: 'mt-rail' }, svg);
    for (i = 0; i < NI(); i++) el('line', { x1: padX(i), x2: padX(i), y1: padY(0), y2: padY(NJ() - 1), class: 'mt-rail' }, svg);
    // Jumps: a bar across the rail between the two steps that jump.
    Object.keys(doc.rails).forEach(function (key) {
      var p = key.split('_').map(Number), axis = p[0];
      railSteps(axis, p[1], p[2]).forEach(function (s, k, all) {
        if (!k || !s.jump) return;
        var t = (all[k - 1].t + s.t) / 2;
        var x = axis ? padX(p[1]) : padX(p[1] + t), y = axis ? padY(p[2] + t) : padY(p[2]);
        el('line', axis ? { x1: x - 6, x2: x + 6, y1: y, y2: y, class: 'mt-jump' } : { x1: x, x2: x, y1: y - 6, y2: y + 6, class: 'mt-jump' }, svg);
      });
    });
    for (i = 0; i < NI(); i++) for (j = 0; j < NJ(); j++) el('circle', { cx: padX(i), cy: padY(j), r: 2.5, class: 'mt-node' }, svg);
    doc.axes[0].values.forEach(function (w, k) { el('text', { x: padX(k), y: PAD.y0 + 16, 'text-anchor': 'middle' }, svg).textContent = w; });
    doc.axes[1].values.forEach(function (w, k) { el('text', { x: PAD.x0 - 8, y: padY(k) + 4, 'text-anchor': 'end' }, svg).textContent = w; });
    el('text', { x: (PAD.x0 + PAD.x1) / 2, y: 246, 'text-anchor': 'middle' }, svg).textContent = 'grade weight';
    el('text', { x: 10, y: (PAD.y0 + PAD.y1) / 2, transform: 'rotate(-90 10 ' + (PAD.y0 + PAD.y1) / 2 + ')', 'text-anchor': 'middle' }, svg).textContent = 'corner weight';
    el('circle', { cx: padX(state.u), cy: padY(state.v), r: 7, class: 'mt-here', id: 'mt-here' }, svg);
  }

  // Snap a point in index space onto the nearer rail.
  function snap(u, v) {
    u = Math.max(0, Math.min(NI() - 1, u)); v = Math.max(0, Math.min(NJ() - 1, v));
    if (Math.abs(u - Math.round(u)) < Math.abs(v - Math.round(v))) u = Math.round(u); else v = Math.round(v);
    return [u, v];
  }

  function padEvents() {
    var svg = $('mt-pad'), dragging = false;
    var at = function (e) {
      var r = svg.getBoundingClientRect(), sx = 340 / r.width, sy = 250 / r.height;
      var x = (e.clientX - r.left) * sx, y = (e.clientY - r.top) * sy;
      var u = (x - PAD.x0) / (PAD.x1 - PAD.x0) * (NI() - 1), v = (PAD.y0 - y) / (PAD.y0 - PAD.y1) * (NJ() - 1);
      var s = snap(u, v);
      state.u = s[0]; state.v = s[1];
      render(true);
    };
    svg.addEventListener('pointerdown', function (e) { dragging = true; svg.setPointerCapture(e.pointerId); at(e); });
    svg.addEventListener('pointermove', function (e) { if (dragging) at(e); });
    svg.addEventListener('pointerup', function () { dragging = false; });
    svg.addEventListener('pointercancel', function () { dragging = false; });
  }

  // ---- sliders ----

  var LABEL = { grade: 'Grade weight', corner: 'Corner weight' };
  var UNIT = { grade: 'm per m at curve 1', corner: 'm per corner at curve 1' };

  function sliders() {
    var box = $('mt-sliders');
    box.textContent = '';
    doc.axes.forEach(function (ax, n) {
      var key = n ? 'v' : 'u', id = 'mt-s-' + ax.key;
      var wrap = document.createElement('div');
      wrap.innerHTML = '<label for="' + id + '">' + LABEL[ax.key] + ' <span class="mt-val"></span></label>' +
        '<input type="range" id="' + id + '" min="0" max="' + (ax.values.length - 1) + '" step="0.01" value="' + state[key] + '">' +
        '<div class="mt-ticks">' + ax.values.map(function (v) { return '<span>' + v + '</span>'; }).join('') + '</div>';
      box.appendChild(wrap);
      wrap.querySelector('input').addEventListener('input', function (e) {
        // Moving one weight puts the other on its nearest grid value.
        state[key] = +e.target.value;
        var other = n ? 'u' : 'v';
        state[other] = Math.round(state[other]);
        render(true);
      });
    });
  }

  function syncSliders() {
    doc.axes.forEach(function (ax, n) {
      var key = n ? 'v' : 'u', input = $('mt-s-' + ax.key);
      if (+input.value !== state[key]) input.value = state[key];
      var w = weightAt(n, state[key]);
      input.parentNode.querySelector('.mt-val').textContent = fmt(w, w < 10 ? 2 : 0) + ' ' + UNIT[ax.key];
    });
    var here_ = $('mt-here');
    if (here_) { here_.setAttribute('cx', padX(state.u)); here_.setAttribute('cy', padY(state.v)); }
  }

  // ---- the numbers ----

  function fmt(v, nd) { return v == null ? '—' : Number(v).toLocaleString(undefined, { maximumFractionDigits: nd || 0, minimumFractionDigits: nd || 0 }); }

  function exactName() {
    if (doc.surface.formula === false) return 'No formula (real ground)';
    if (doc.surface.name === 'plane') return 'Reference zigzag';
    return 'Formula (turns free)';
  }

  function sum(c, keys) { return keys.reduce(function (a, k) { return a + (c[k] || 0); }, 0); }

  function table(h) {
    var e = h.rec.engine, x = h.rec.formula, input = h.node ? h.rec.input : null;
    var other = ['tsa', 'falltread', 'slope', 'cutfill', 'climb', 'drainage'];
    var rows = [
      ['Total, equivalent m', e && e.total, x && x.total, input, 0],
      ['… length', e && e.contrib.length, x && x.contrib.length, null, 0],
      ['… grade', e && (e.contrib.grade || 0), x && (x.contrib.grade || 0), null, 0],
      ['… corners', e && (e.contrib.corner || 0), x && (x.contrib.corner || 0), null, 0],
      ['… bends', e && (e.contrib.bend || 0), x && (x.contrib.bend || 0), null, 0],
      ['… all the rest', e && sum(e.contrib, other), x && sum(x.contrib, other), null, 0],
      ['Length along the slope, m', e && e.length_m, x && x.length_m, null, 0],
      ['Mean grade, %', e && e.grade_mean, x && x.grade_mean, null, 1],
      ['Steepest 10 m, %', e && e.grade_max, x && x.grade_max, null, 1],
      ['Corners', e && e.corners, x && x.corners, null, 0],
      ['Deepest cut, m', e && e.cut_max, x && x.cut_max, null, 2]
    ];
    var best = e && x ? (e.total <= x.total ? 1 : 2) : 0;
    var t = '<thead><tr><th></th><th>Engine</th><th>' + exactName() + '</th><th>Straight</th></tr></thead><tbody>';
    rows.forEach(function (r, i) {
      t += '<tr><td>' + r[0] + '</td>';
      for (var j = 1; j <= 3; j++) t += '<td' + (i === 0 && j === best ? ' class="mt-best"' : '') + '>' + fmt(r[j], r[4]) + '</td>';
      t += '</tr>';
    });
    $('mt-table').innerHTML = t + '</tbody>';
    var msg = '';
    if (e && x) {
      var gap = 100 * (e.total - x.total) / x.total, what = doc.surface.name === 'plane' ? 'reference zigzag' : 'turn-free formula';
      if (Math.abs(gap) < 1) msg = 'The engine matches the ' + what + ' to within 1 %.';
      else if (gap > 0) msg = 'The engine falls ' + fmt(gap, 1) + ' % short of the ' + what + '.';
      else msg = 'The engine beats the ' + what + ' by ' + fmt(-gap, 1) + ' %' +
        (doc.surface.name === 'plane' ? '.' : ': it turns less, which the formula does not weigh.');
    }
    if (doc.surface.formula === false && e && input) {
      msg = 'Real ground has no formula to check the engine against; its line costs ' + fmt(100 * (input - e.total) / input, 0) +
        ' % less than the straight line.';
    }
    $('mt-verdict').textContent = msg;
    var tr = '';
    if (!h.node && h.rec.from) {
      tr = 'Traced from the ' + (h.rec.from === 'low' ? 'lower' : 'higher') + ' setting';
      if (h.rec.other_total != null) tr += '; the tracing from the other end costs ' + fmt(h.rec.other_total, 0);
      tr += '.';
      if (h.jump) tr += ' The route changes between this step and the next.';
    } else if (h.node) tr = 'A grid setting, run thoroughly.';
    $('mt-trace').textContent = tr;
    $('mt-best').textContent = x && x.best_grade_pct != null ? 'Cheapest grade on the steepest ground here, turns aside: ' +
      fmt(x.best_grade_pct, 1) + ' % (the ground is up to ' + fmt(doc.surface.max_slope_pct, 0) + ' %).' :
      doc.surface.formula === false ? 'The cost chart is drawn for ground of ' + fmt(doc.surface.max_slope_pct, 0) +
        ' %, the steepest tenth of the corridor.' : '';
  }

  function factorsTable() {
    var t = '<thead><tr><th>Factor</th><th>Weight</th><th>Note</th></tr></thead><tbody>';
    doc.factors.forEach(function (f) {
      var k = f.varies ? '<strong>varies</strong>' : fmt(f.k, f.k < 10 ? 2 : 0);
      t += '<tr><td>' + f.label + '</td><td>' + k + '</td><td class="mt-fnote">' + (f.note || '') + '</td></tr>';
    });
    $('mt-factors').innerHTML = t + '</tbody>';
  }

  // ---- charts ----

  function el(tag, attrs, parent) {
    var n = document.createElementNS(NS, tag);
    for (var k in attrs) n.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(n);
    return n;
  }

  // A curve as score.curve_fn reads it: log-linear between nodes, flat
  // beyond the ends.
  function curveFn(curve) {
    return function (x) {
      if (x <= curve[0][0]) return curve[0][1];
      for (var i = 0; i < curve.length - 1; i++) {
        var a = curve[i], b = curve[i + 1];
        if (x <= b[0]) {
          var t = b[0] > a[0] ? (x - a[0]) / (b[0] - a[0]) : 1;
          return Math.exp(Math.log(a[1]) + t * (Math.log(b[1]) - Math.log(a[1])));
        }
      }
      return curve[curve.length - 1][1];
    };
  }

  // sandbox.per_metre, for the chart.
  function perMetre(g, s, kGrade) {
    if (!doc._F) {
      doc._F = {};
      doc.factors.forEach(function (f) { doc._F[f.key] = Object.assign({ fn: curveFn(f.curve) }, f); });
    }
    var F = doc._F;
    var fn = function (n, x) { return F[n].fn(x); };
    var tsa = 100 * s < 4 ? 90 : Math.acos(Math.min(1, g / Math.max(s, 1e-12))) * 180 / Math.PI;
    return 1 + kGrade * fn('grade', 100 * g) + F.tsa.k * fn('tsa', tsa) +
      F.falltread.k * fn('falltread', 100 * g) * (tsa < doc.params.fall_tsa_deg ? 1 : 0) +
      F.slope.k * fn('slope', 100 * s) + F.cutfill.k * fn('cutfill', 0);
  }

  function axes(svg, X, Y, xt, yt, xl, yl) {
    xt.forEach(function (v) {
      el('line', { x1: X(v), x2: X(v), y1: Y.r[0], y2: Y.r[1], class: 'mt-axis' }, svg);
      el('text', { x: X(v), y: Y.r[0] + 14, 'text-anchor': 'middle' }, svg).textContent = v;
    });
    yt.forEach(function (v) {
      el('line', { x1: X.r[0], x2: X.r[1], y1: Y(v), y2: Y(v), class: 'mt-axis' }, svg);
      el('text', { x: X.r[0] - 4, y: Y(v) + 4, 'text-anchor': 'end' }, svg).textContent = v;
    });
    el('text', { x: (X.r[0] + X.r[1]) / 2, y: 214, 'text-anchor': 'middle' }, svg).textContent = xl;
    el('text', { x: 12, y: (Y.r[0] + Y.r[1]) / 2, transform: 'rotate(-90 12 ' + (Y.r[0] + Y.r[1]) / 2 + ')', 'text-anchor': 'middle' }, svg).textContent = yl;
  }

  function scale(d0, d1, r0, r1) {
    var f = function (v) { return r0 + (v - d0) / (d1 - d0) * (r1 - r0); };
    f.r = [r0, r1];
    return f;
  }

  function niceTicks(lo, hi, n) {
    var step = Math.pow(10, Math.floor(Math.log10((hi - lo) / n)));
    [1, 2, 5, 10].some(function (m) { if ((hi - lo) / (step * m) <= n) { step *= m; return true; } return false; });
    var out = [];
    for (var v = Math.ceil(lo / step) * step; v <= hi + 1e-9; v += step) out.push(+v.toFixed(6));
    return out;
  }

  function costChart(h) {
    var svg = $('mt-cost');
    svg.textContent = '';
    var k = weightAt(0, state.u), s = doc.surface.max_slope_pct / 100, pts = [], cmin = Infinity;
    for (var g = 2; g <= 100; g += 0.5) {
      var gf = g / 100, c = perMetre(gf, Math.max(s, gf), k) * Math.sqrt(1 + gf * gf) / gf;
      pts.push([g, c]); cmin = Math.min(cmin, c);
    }
    var ymax = cmin * 4;
    var X = scale(0, 100, 46, 450), Y = scale(0, ymax, 190, 10);
    axes(svg, X, Y, [0, 20, 40, 60, 80, 100], niceTicks(0, ymax, 5), 'grade, %', 'equivalent m per m climbed');
    el('line', { x1: X(doc.surface.max_slope_pct), x2: X(doc.surface.max_slope_pct), y1: 190, y2: 10, stroke: '#1f2a1f', 'stroke-dasharray': '4 4' }, svg);
    var d = pts.filter(function (p) { return p[1] <= ymax; }).map(function (p, i) { return (i ? 'L' : 'M') + X(p[0]).toFixed(1) + ' ' + Y(p[1]).toFixed(1); }).join('');
    el('path', { d: d, fill: 'none', stroke: css('--mt-engine'), 'stroke-width': 2 }, svg);
    var x = h.rec.formula;
    if (x && x.best_grade_pct != null) {
      var gb = x.best_grade_pct / 100, cb = perMetre(gb, Math.max(s, gb), k) * Math.sqrt(1 + gb * gb) / gb;
      el('circle', { cx: X(100 * gb), cy: Y(Math.min(cb, ymax)), r: 4.5, fill: css('--mt-exact') }, svg);
      el('text', { x: X(100 * gb) + 7, y: Y(Math.min(cb, ymax)) - 7 }, svg).textContent = 'best ' + fmt(100 * gb, 1) + ' %';
    }
  }

  function profileChart(h) {
    var svg = $('mt-profile');
    svg.textContent = '';
    var e = h.rec.engine, x = h.rec.formula;
    var lines = [];
    if (x) lines.push([x.profile.d, x.profile.design, css('--mt-exact'), '6 4', 2]);
    if (e) {
      lines.push([e.profile.d, e.profile.ground, '#9ca3af', null, 1.5]);
      lines.push([e.profile.d, e.profile.design, css('--mt-engine'), null, 2]);
    }
    if (!lines.length) return;
    var dmax = 0, zmin = Infinity, zmax = -Infinity;
    lines.forEach(function (l) {
      dmax = Math.max(dmax, l[0][l[0].length - 1]);
      l[1].forEach(function (z) { zmin = Math.min(zmin, z); zmax = Math.max(zmax, z); });
    });
    var X = scale(0, dmax, 46, 450), Y = scale(zmin, zmax, 190, 10);
    axes(svg, X, Y, niceTicks(0, dmax, 6), niceTicks(zmin, zmax, 5), 'distance along the line, m', 'elevation, m');
    lines.forEach(function (l) {
      var d = l[0].map(function (v, i) { return (i ? 'L' : 'M') + X(v).toFixed(1) + ' ' + Y(l[1][i]).toFixed(1); }).join('');
      var a = { d: d, fill: 'none', stroke: l[2], 'stroke-width': l[4] };
      if (l[3]) a['stroke-dasharray'] = l[3];
      el('path', a, svg);
    });
  }

  // ---- putting it together ----

  var pending = false;
  function render(fromInput) {
    if (pending) return;
    pending = true;
    requestAnimationFrame(function () {
      pending = false;
      var h = here();
      syncSliders();
      drawMap(h); table(h); costChart(h); profileChart(h);
      $('mt-exact-name').textContent = exactName();
    });
  }

  function pick(name) {
    state.name = name;
    Array.prototype.forEach.call($('mt-surfaces').children, function (b) { b.setAttribute('aria-selected', b.dataset.name === name ? 'true' : 'false'); });
    $('mt-blurb').textContent = 'Loading…';
    load(name).then(function (d) {
      if (state.name !== name) return;
      doc = d;
      state.u = Math.min(state.u, NI() - 1); state.v = Math.min(state.v, NJ() - 1);
      $('mt-blurb').textContent = d.surface.blurb;
      $('mt-built').textContent = 'Lines computed ' + d.built + ' with manage.py build_methods analytic' +
        (d.quick ? ' (a quick build: short searches, one seed).' : '.');
      sliders(); drawPad(); factorsTable(); sizeCanvas(); render();
    }).catch(function (err) {
      $('mt-blurb').textContent = 'Could not load this surface (' + err.message + '). It may not have been built yet.';
    });
  }

  function start(list) {
    files = list;
    var bar = $('mt-surfaces');
    files.forEach(function (f) {
      var b = document.createElement('button');
      b.type = 'button'; b.role = 'tab'; b.dataset.name = f.name; b.textContent = f.label;
      b.addEventListener('click', function () { pick(f.name); });
      bar.appendChild(b);
    });
    $('mt-dig').addEventListener('change', function (e) { state.dig = e.target.checked; if (doc) render(); });
    window.addEventListener('resize', function () { sizeCanvas(); if (doc) render(); });
    padEvents();
    pick(files[0].name);
  }

  window.TgMethods = { start: start };
})();
