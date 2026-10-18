/* tg_refine.js — the refine tool's panel (docs/REFINE_TOOL.md).
 *
 * Opened from an alignment's panel ("Refine…"). The person picks the legs
 * (a continuous stretch; existing trail unticked by default), shapes one
 * cost curve per factor with a weight k beside it, and runs. The search runs
 * in the worker (core.refine); this file starts it, shows progress, draws
 * the candidates on the map and their profiles, compares them with the
 * input scored the same way, and saves a chosen one as a variant.
 *
 * Curve editor: curves are log-linear between at most 9 nodes, y > 0 on a log
 * axis. Weights are in metres of equivalent effort (docs/REFINE_TOOL.md §13):
 * extra metres per metre at curve 1, per corner, or per right angle. Drag a
 * node; drag a flat stretch up or down; drag a ramp sideways; double-click a
 * segment to add a node; double-click a node (or drag it off the chart) to remove it. The grey
 * bars behind a curve are the selected legs' own values today (a baseline
 * run scores the input when the panel opens or the selection changes).
 *
 * Global `window.TgRefine`; tg_design.js calls open(feature, app).
 */
window.TgRefine = (function () {
  'use strict';

  var app = null, f = null, active = false;
  var meta = null, settings = null, mine = null;
  var legSel = [], baseline = null, result = null, runJob = null, chosen = 0, stale = false;
  var editors = {}, pollTimer = null, baseTimer = null, seq = 0;
  var CAND = ['#d9480f', '#1864ab', '#2b8a3e', '#7048e8'];
  var SRC = 'refine-cands', LAYERS = ['refine-cands-all', 'refine-cands-chosen'];

  function $(id) { return document.getElementById(id); }
  function esc(t) { return app.esc(t); }
  function clone(o) { return JSON.parse(JSON.stringify(o)); }
  function csrf() { var m = document.querySelector('meta[name=csrf-token]'); return m ? m.content : ''; }
  function api(method, url, body) {
    return fetch(url, { method: method, credentials: 'same-origin',
                        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf() },
                        body: body ? JSON.stringify(body) : undefined })
      .then(function (r) {
        if (r.redirected && /\/accounts\/login\//.test(r.url)) throw new Error('You have been signed out. Sign in again (in another tab, to keep this one) and retry.');
        return r.json().catch(function () { return {}; }).then(function (j) {
          if (!r.ok && r.status !== 202) throw new Error(j.error || (r.status === 403 ? 'Not allowed.' : 'Error ' + r.status));
          j._status = r.status; return j;
        });
      });
  }
  function n(v, d) { return v == null ? '–' : (+v).toFixed(d == null ? 0 : d); }
  function fmeta(name) { return meta.factors.filter(function (m) { return m.name === name; })[0]; }

  // ---- open / close ----------------------------------------------------------
  function open(feature, a) {
    app = a; f = feature; active = true; result = null; baseline = null; runJob = null; chosen = 0; stale = false;
    legSel = f.properties.legs.map(function (L) { return L.kind !== 'existing'; });
    $('panel').classList.add('tg-wide');
    $('detail').innerHTML = '<p class="tg-muted">Loading the refine tool…</p>';
    $('profstats').innerHTML = '';
    app.profile.clear();
    api('GET', '/api/refine/settings').then(function (r) {
      if (!active) return;
      meta = r; mine = r.mine; settings = clone(r.effective);
      render(); addLayers(); queueBaseline(0);
    }).catch(function (e) { $('detail').innerHTML = '<p class="tg-error">' + esc(e.message) + '</p>'; });
  }
  function close() {
    if (!active) return;
    active = false; seq++;
    clearTimeout(pollTimer); clearTimeout(baseTimer);
    var map = app.map;
    LAYERS.forEach(function (id) { if (map.getLayer(id)) map.removeLayer(id); });
    if (map.getSource(SRC)) map.removeSource(SRC);
    editors = {};
  }

  // ---- the panel --------------------------------------------------------------
  function render() {
    var p = f.properties, h = '';
    h += '<div class="tg-kind">Refine</div><h2>' + esc(p.name) + '</h2>';
    h += '<div class="tg-small tg-muted">Reads your line as a sketch of a smooth path (sharp corners are taken to mean rounder ones) and proposes versions that fit the terrain better. Nothing changes until you save a candidate, which becomes a variant of this alignment. <a href="#" id="rf-back">Back to the alignment</a></div>';
    h += '<div class="tg-sub">Legs to refine</div><div id="rf-legs" class="rf-legs"></div><div id="rf-legmsg" class="tg-small tg-error"></div>';
    h += '<div class="tg-sub">Factors</div><div class="tg-small tg-muted">Costs are in metres of <strong>equivalent effort</strong>: the length of easy trail a line is as much work as. Its length along the slope (the climb counted) is the base; each factor adds to it. A weight is the extra metres where the factor\'s curve is at 1: per metre of trail, per corner, or per right angle turned (grade at 3.3 makes 100 m at curve 1 as much as 430 m of easy trail). 0 ignores a factor. Each curve is the cost by value, on a log scale (+ and − zoom it, fit frames it), anchors joined in straight lines on that scale; there is no 0, and 0.01 is next to nothing. Drag a node; drag a flat stretch up or down, a ramp sideways; double-click a segment to add a node, a node to remove it (or drag it off the chart). Grey bars: the selected legs today.</div>';
    h += '<div id="rf-factors"></div>';
    h += '<details class="tg-more"><summary>Run parameters</summary><div id="rf-params" class="rf-params"></div></details>';
    h += '<div class="tg-actions"><button id="rf-run" class="tg-btn tg-primary">Run</button>' +
         '<button id="rf-def" class="tg-btn" title="Save these curves, weights and parameters as your own defaults">Set as my default</button>' +
         '<button id="rf-mine" class="tg-btn" title="Back to your saved defaults">My default</button>' +
         '<button id="rf-site" class="tg-btn" title="Back to the site\'s defaults">Site default</button></div>';
    h += '<div id="rf-status"></div><div id="rf-results"></div>';
    $('detail').innerHTML = h;
    renderLegs(); renderFactors(); renderParams();
    $('rf-back').addEventListener('click', function (e) { e.preventDefault(); var id = f.properties.id; close(); app.select('align', id, false); });
    $('rf-run').addEventListener('click', run);
    $('rf-def').addEventListener('click', function () {
      api('POST', '/api/refine/settings', { settings: settings }).then(function (r) { mine = r.mine; status('Saved as your default.'); })
        .catch(function (e) { status(e.message, true); });
    });
    $('rf-mine').disabled = !mine;
    $('rf-mine').addEventListener('click', function () {
      api('GET', '/api/refine/settings').then(function (r) { mine = r.mine; settings = clone(r.effective); renderFactors(); renderParams(); changed(); });
    });
    $('rf-site').addEventListener('click', function () { settings = clone(meta.site); renderFactors(); renderParams(); changed(); });
  }
  function status(msg, err) {
    var s = $('rf-status'); if (s) s.innerHTML = '<div class="tg-status' + (err ? ' tg-error' : '') + '">' + msg + '</div>';
  }

  function renderLegs() {
    var h = '';
    f.properties.legs.forEach(function (L, i) {
      h += '<label><input type="checkbox" data-i="' + i + '"' + (legSel[i] ? ' checked' : '') + '> ' + (i + 1) + '. ' +
           esc(L.name || (L.trail ? L.trail.name : TgProfile.LEG_LABELS[L.kind])) + ' <span class="tg-muted">· ' +
           TgProfile.LEG_LABELS[L.kind] + ' · ' + (L.length_m / 1000).toFixed(2) + ' km' +
           (L.kind === 'existing' ? ' · locked unless ticked' : '') + '</span></label>';
    });
    $('rf-legs').innerHTML = h;
    Array.prototype.forEach.call(document.querySelectorAll('#rf-legs input'), function (cb) {
      cb.addEventListener('change', function () { legSel[+cb.dataset.i] = cb.checked; result = null; $('rf-results').innerHTML = ''; drawMap(); checkLegs(); queueBaseline(400); });
    });
    checkLegs();
  }
  function selectedLegs() {
    var idx = []; legSel.forEach(function (v, i) { if (v) idx.push(i); }); return idx;
  }
  function checkLegs() {
    var idx = selectedLegs(), msg = '';
    if (!idx.length) msg = 'Tick at least one leg.';
    else if (idx[idx.length - 1] - idx[0] !== idx.length - 1) msg = 'Tick legs that follow one another: the refined stretch must be continuous.';
    $('rf-legmsg').textContent = msg;
    var b = $('rf-run'); if (b) b.disabled = !!msg;
    return !msg;
  }

  // Weight sliders are logarithmic, with "off" at the far left.
  var WRANGE = { metre: [0.01, 30], corner: [1, 3000], turn: [1, 3000] };
  var WUNIT = { metre: 'm per m', corner: 'm per corner', turn: 'm per right angle' };
  function wToPos(v, r) { return v > 0 ? Math.max(1, Math.round(1000 * Math.log(v / r[0]) / Math.log(r[1] / r[0]))) : 0; }
  function posToW(p, r) { return p > 0 ? +(r[0] * Math.pow(r[1] / r[0], p / 1000)).toPrecision(2) : 0; }
  function renderFactors() {
    var box = $('rf-factors'); box.innerHTML = ''; editors = {};
    meta.factors.forEach(function (m) {
      var s = settings.factors[m.name], r = WRANGE[m.per];
      var row = document.createElement('div');
      row.className = 'rf-factor';
      row.innerHTML = '<div class="rf-fhead"><strong>' + esc(m.label) + '</strong>' +
        (m.stub ? ' <span class="tg-badge" title="Deliberately primitive for now: sites are low points of the design line and ground dips; no groundwater or terrain below the trail yet">stub</span>' : '') +
        '<span class="tg-muted tg-small">' + esc(m.x) + ' (' + esc(m.unit) + ')</span></div>' +
        '<div class="rf-wrow" title="Extra metres of equivalent effort where this curve is at 1, ' + WUNIT[m.per] + ' (' + (m.per === 'metre' ? 'per metre of trail' : m.per === 'corner' ? 'for each corner' : 'for each right angle turned') + '). 0 ignores the factor.">' +
        '<input type="range" class="rf-w" min="0" max="1000" step="1"><input type="number" class="rf-wn" min="0" max="10000" step="any"><span class="tg-small tg-muted">' + WUNIT[m.per] + '</span></div>' +
        '<div class="rf-curve"></div>';
      box.appendChild(row);
      var sl = row.querySelector('.rf-w'), num = row.querySelector('.rf-wn');
      function show(v) { num.value = v; sl.value = wToPos(v, r); row.classList.toggle('rf-off', v === 0); }
      show(s.k);
      sl.addEventListener('input', function () { s.k = posToW(+sl.value, r); num.value = s.k; row.classList.toggle('rf-off', s.k === 0); changed(); });
      num.addEventListener('change', function () { s.k = Math.max(0, Math.min(10000, +num.value || 0)); show(s.k); changed(); });
      var band = m.band ? { get: function () { return settings.params[m.band]; },
                            set: function (v) { settings.params[m.band] = v; var i = document.querySelector('#rf-params input[data-p="' + m.band + '"]'); if (i) i.value = v; } } : null;
      editors[m.name] = curveEditor(row.querySelector('.rf-curve'), m, s.curve, changed, band);
    });
    applyHistograms();
  }
  function renderParams() {
    var h = '';
    meta.params.forEach(function (p) {
      h += '<label>' + esc(p.label) + ' <input type="number" data-p="' + p.name + '" min="' + p.min + '" max="' + p.max + '" step="' + (p.integer ? 1 : 'any') + '" value="' + settings.params[p.name] + '"></label>';
    });
    $('rf-params').innerHTML = h;
    Array.prototype.forEach.call(document.querySelectorAll('#rf-params input'), function (inp) {
      inp.addEventListener('change', function () {
        var p = meta.params.filter(function (q) { return q.name === inp.dataset.p; })[0];
        var v = Math.min(p.max, Math.max(p.min, +inp.value || 0)); if (p.integer) v = Math.round(v);
        inp.value = v; settings.params[p.name] = v; changed();
        Object.keys(editors).forEach(function (k) { if (editors[k].refreshBand) editors[k].refreshBand(); });
        if (p.name === 'corridor_m' || p.name === 'spacing_m') queueBaseline(400);
      });
    });
  }
  function changed() {
    if (result && !stale) { stale = true; var r = $('rf-stale'); if (r) r.style.display = ''; }
  }

  // ---- the curve editor ----------------------------------------------------------
  // y is a cost multiplier on a log axis (0.1 to 10 at first, widened by
  // whole decades to take in the curve; + and − zoom it, "fit" frames the
  // curve). There is no 0: anchors are joined log-linearly, straight lines
  // on this axis, as score.curve_fn applies them.
  // `band` ({get, set}, optional): a parameter shown as a shaded band from
  // the left edge, its edge draggable (the TSA curve's fall-line band).
  function curveEditor(el, fm, nodes, onChange, band) {
    var W = Math.max(280, Math.min(520, el.clientWidth || 480)), H = 104;
    var m = { l: 34, r: 10, t: 6, b: 18 }, w = W - m.l - m.r, h = H - m.t - m.b;
    var YMIN = 1e-4, YMAX = 1e4, FREE = 0.01;
    nodes.forEach(function (q) { if (!(q[1] > 0)) q[1] = FREE; });     // an older curve's 0
    var x = d3.scaleLinear().domain([fm.xmin, fm.xmax]).range([0, w]);
    var lo = 0.1, hi = 10;
    var y = d3.scaleLog().range([h, 0]);
    el.classList.add('rf-curvebox');
    var zb = d3.select(el).append('div').attr('class', 'rf-zoom');
    zb.append('button').attr('type', 'button').attr('title', 'Zoom in: a narrower range of costs').text('+').on('click', function () { zoom(-0.5); });
    zb.append('button').attr('type', 'button').attr('title', 'Zoom out: a wider range of costs').text('−').on('click', function () { zoom(0.5); });
    zb.append('button').attr('type', 'button').attr('title', 'Frame the curve').text('fit').on('click', fit);
    var svg = d3.select(el).append('svg').attr('width', W).attr('height', H).attr('class', 'rf-svg');
    var g = svg.append('g').attr('transform', 'translate(' + m.l + ',' + m.t + ')');
    var gb = g.append('g'), gh = g.append('g'), gm = g.append('g');
    if (fm.xmin < 0 && fm.xmax > 0) {                     // a signed x: mark 0
      gb.append('line').attr('class', 'rf-zero').attr('x1', x(0)).attr('x2', x(0)).attr('y1', 0).attr('y2', h);
      gb.append('text').attr('class', 'rf-axis').attr('x', x(0) - 4).attr('y', 10).attr('text-anchor', 'end').attr('font-size', 10).text('cut');
      gb.append('text').attr('class', 'rf-axis').attr('x', x(0) + 4).attr('y', 10).attr('font-size', 10).text('fill');
    }
    var bandRect = null, bandEdge = null;
    if (band) {
      bandRect = gb.append('rect').attr('class', 'rf-band').attr('x', 0).attr('y', 0).attr('height', h);
      bandEdge = gb.append('line').attr('class', 'rf-band-edge').attr('y1', 0).attr('y2', h)
        .call(d3.drag().on('drag', function (ev) {
          band.set(Math.max(5, Math.min(60, Math.round(x.invert(Math.max(0, Math.min(w, ev.x)))))));
          placeBand();
        }).on('end', function () { onChange(); }));
      bandEdge.append('title').text('Drag: the fall-line band, where the downhill side is undefined (a corner is a change of side across it) and the tread counts as fall-line');
    }
    function placeBand() {
      if (!band) return;
      var bx = x(band.get());
      bandRect.attr('width', Math.max(0, bx));
      bandEdge.attr('x1', bx).attr('x2', bx);
    }
    placeBand();
    g.append('g').attr('class', 'rf-axis').attr('transform', 'translate(0,' + h + ')').call(d3.axisBottom(x).ticks(6).tickSizeOuter(0));
    var gy = g.append('g').attr('class', 'rf-axis');
    var path = g.append('path').attr('class', 'rf-line');
    var segs = g.append('g'), dots = g.append('g');
    var hist = null;
    var snap = fm.snap || 1;
    function snapX(v) { return Math.round(v / snap) * snap; }
    function snapY(v) { return Math.max(YMIN, Math.min(YMAX, +v.toPrecision(2))); }
    function clampX(v) { return Math.max(fm.xmin, Math.min(fm.xmax, v)); }
    function ypx(v) { return y(Math.max(lo, Math.min(hi, v))); }
    function yval(py) { return snapY(y.invert(Math.max(0, Math.min(h, py)))); }

    function setRange(a, b) {
      lo = a; hi = b; y.domain([lo, hi]);
      // Decades; 2 and 5 as well when the range is narrow enough to read them.
      var wide = Math.log10(hi / lo) > 1.6;
      var ticks = y.ticks(10).filter(function (t) {
        var d = t / Math.pow(10, Math.floor(Math.log10(t) + 1e-9));
        return d < 1.01 || (!wide && (Math.abs(d - 2) < 0.01 || Math.abs(d - 5) < 0.01));
      });
      gy.call(d3.axisLeft(y).tickValues(ticks).tickFormat(d3.format('~g')).tickSizeOuter(0));
    }
    function zoom(dDec) {                                 // widen (+) or narrow (−) by dDec decades each side
      var c = Math.sqrt(lo * hi), half = Math.log10(hi / lo) / 2 + dDec;
      half = Math.max(0.25, Math.min(4, half));
      setRange(Math.max(YMIN, c / Math.pow(10, half)), Math.min(YMAX, c * Math.pow(10, half)));
      draw();
    }
    function fit() {
      var v = nodes.map(function (q) { return q[1]; });
      var a = Math.min.apply(null, v), b = Math.max.apply(null, v);
      if (b / a < 10) { var c = Math.sqrt(a * b); a = c / Math.sqrt(10); b = c * Math.sqrt(10); }
      setRange(Math.max(YMIN, a / 1.5), Math.min(YMAX, b * 1.5));
      draw();
    }
    // Start at 0.1 to 10, widened by whole decades if a node is outside.
    var a0 = 0.1, b0 = 10;
    nodes.forEach(function (q) { while (q[1] < a0 * 0.999) a0 /= 10; while (q[1] > b0 * 1.001) b0 *= 10; });
    setRange(a0, b0);

    function between(a, b, t) { return a[1] * Math.pow(b[1] / a[1], t); }   // log-linear
    function segPts(a, b) { return [[x(a[0]), ypx(a[1])], [x(b[0]), ypx(b[1])]]; }
    var line = d3.line();
    var nodeDrag = d3.drag()
      .on('drag', function (ev, d) {
        var i = nodes.indexOf(d); if (i < 0) return;
        var off = ev.y < -24 || ev.y > h + 24;
        d3.select(this).classed('rf-del', off && nodes.length > 2);
        var lo_ = i > 0 ? nodes[i - 1][0] : fm.xmin, hi_ = i < nodes.length - 1 ? nodes[i + 1][0] : fm.xmax;
        d[0] = Math.max(lo_, Math.min(hi_, snapX(clampX(x.invert(ev.x)))));
        if (!off) d[1] = yval(ev.y);
        draw();
      })
      .on('end', function (ev, d) {
        if ((ev.y < -24 || ev.y > h + 24) && nodes.length > 2) nodes.splice(nodes.indexOf(d), 1);
        draw(); onChange();
      });
    var segDrag = d3.drag()
      .on('start', function (ev, i) { this._x0 = ev.x; this._n = [nodes[i].slice(), nodes[i + 1].slice()]; })
      .on('drag', function (ev, i) {
        var a = nodes[i], b = nodes[i + 1], n0 = this._n;
        if (n0[0][1] === n0[1][1]) {                          // a flat stretch moves up or down
          a[1] = b[1] = yval(ev.y);
        } else {                                             // a ramp moves sideways
          var dx = x.invert(ev.x) - x.invert(this._x0);
          var lo_ = i > 0 ? nodes[i - 1][0] : fm.xmin, hi_ = i + 2 < nodes.length ? nodes[i + 2][0] : fm.xmax;
          dx = Math.max(lo_ - n0[0][0], Math.min(hi_ - n0[1][0], dx));
          a[0] = snapX(n0[0][0] + dx); b[0] = snapX(n0[1][0] + dx);
        }
        draw();
      })
      .on('end', function () { onChange(); });
    function split(ev, i) {
      if (nodes.length >= 9) return;
      var a = nodes[i], b = nodes[i + 1];
      var xv = snapX(x.invert(d3.pointer(ev, g.node())[0]));
      if (!(xv > a[0] && xv < b[0])) xv = (a[0] + b[0]) / 2;
      var yv = b[0] > a[0] ? between(a, b, (xv - a[0]) / (b[0] - a[0])) : a[1];
      nodes.splice(i + 1, 0, [xv, snapY(yv)]);
      draw(); onChange();
    }
    function remove(ev, d) {
      ev.preventDefault(); ev.stopPropagation();
      if (nodes.length <= 2) return;
      nodes.splice(nodes.indexOf(d), 1);
      draw(); onChange();
    }
    function draw() {
      gh.selectAll('rect').remove();
      if (hist && hist.counts) {
        var mx = d3.max(hist.counts) || 1, bw = w / hist.counts.length;
        gh.selectAll('rect').data(hist.counts).enter().append('rect').attr('class', 'rf-hist')
          .attr('x', function (d, i) { return i * bw; }).attr('width', Math.max(1, bw - 1))
          .attr('y', function (d) { return h - h * d / mx; }).attr('height', function (d) { return h * d / mx; })
          .append('title').text(function (d) { return n(d, 0) + ' ' + hist.unit; });
      }
      var first = nodes[0], last = nodes[nodes.length - 1];
      var pts = [[0, ypx(first[1])]].concat(nodes.map(function (q) { return [x(q[0]), ypx(q[1])]; }), [[w, ypx(last[1])]]);
      path.attr('d', line(pts));
      var sl = segs.selectAll('path').data(d3.range(nodes.length - 1));
      sl.exit().remove();
      sl.enter().append('path').attr('class', 'rf-seg').call(segDrag).on('dblclick', split)
        .merge(sl)
        .attr('d', function (i) { return line(segPts(nodes[i], nodes[i + 1])); })
        .classed('rf-flat', function (i) { return nodes[i][1] === nodes[i + 1][1]; });
      var dd = dots.selectAll('circle').data(nodes);
      dd.exit().remove();
      dd.enter().append('circle').attr('class', 'rf-node').attr('r', 5).call(nodeDrag).on('dblclick', remove)
        .merge(dd).attr('cx', function (d) { return x(d[0]); }).attr('cy', function (d) { return ypx(d[1]); })
        .classed('rf-del', false)
        .classed('rf-clip', function (d) { return d[1] < lo || d[1] > hi; })
        .each(function (d) { d3.select(this).selectAll('title').remove(); d3.select(this).append('title').text(d[0] + ' ' + fm.unit + ' → ' + d[1]); });
    }
    draw();
    return {
      refreshBand: placeBand,
      setHist: function (hh) { hist = hh; draw(); },
      setMarks: function (marks) {            // [{x, color, title}]
        var s = gm.selectAll('line').data(marks || []);
        s.exit().remove();
        s.enter().append('line').attr('class', 'rf-mark').merge(s)
          .attr('x1', function (d) { return x(Math.max(fm.xmin, Math.min(fm.xmax, d.x))); }).attr('x2', function (d) { return x(Math.max(fm.xmin, Math.min(fm.xmax, d.x))); })
          .attr('y1', 0).attr('y2', h).attr('stroke', function (d) { return d.color; });
      }
    };
  }
  function applyHistograms() {
    var hs = baseline && baseline.histograms;
    Object.keys(editors).forEach(function (k) { if (hs && hs[k] && hs[k].counts) editors[k].setHist(hs[k]); });
  }

  // ---- jobs ------------------------------------------------------------------------
  function payload(mode) { return { mode: mode, legs: selectedLegs(), settings: settings }; }
  function poll(job, mySeq, onProgress) {
    return new Promise(function (resolve, reject) {
      (function tick() {
        pollTimer = setTimeout(function () {
          if (!active || mySeq !== seq) return;
          api('GET', '/api/refine/jobs/' + job + '/').then(function (st) {
            if (!active || mySeq !== seq) return;
            if (st.status === 'done') resolve(st.result);
            else if (st.status === 'failed') reject(new Error(st.error || 'The run failed.'));
            else { if (onProgress) onProgress(st); tick(); }
          }).catch(reject);
        }, 700);
      })();
    });
  }
  function queueBaseline(delay) {
    clearTimeout(baseTimer);
    if (!checkLegs()) return;
    baseTimer = setTimeout(function () {
      var mySeq = ++seq;
      status('<span class="tg-spin"></span> Reading the terrain and scoring the selected legs…');
      api('POST', '/api/alignments/' + f.properties.id + '/refine', payload('baseline')).then(function (r) {
        return poll(r.job, mySeq);
      }).then(function (res) {
        baseline = res; applyHistograms();
        status(baselineLine(res));
      }).catch(function (e) { if (active && mySeq === seq) status(esc(e.message), true); });
    }, delay || 0);
  }
  function terrainNote(res) {
    var t = res.terrain || {}, s = (t.sources || []).map(function (u) { return esc(u.title) + ' ' + n(u.pct) + ' %'; }).join(', ');
    var h = 'Terrain: ' + (s || 'none') + '.';
    if (t.lidar_pct != null && t.lidar_pct < 90) h += ' <strong>Only ' + n(t.lidar_pct) + ' % lidar: refinement needs lidar to mean much.</strong>';
    (t.warnings || []).forEach(function (w) { h += ' ' + esc(w); });
    return h;
  }
  function baselineLine(res) {
    var s = res.input.stats;
    return 'Today (scored with a ' + n(res.input.t, 2) + ' m cut/fill tube): max grade ' + n(s.grade_max, 1) + ' %, 95th percentile ' +
      n(s.grade_p95, 1) + ' %; equivalent effort ' + n(res.input.total) + ' m for ' + n(res.input.length_m) + ' m along the slope (' + n(res.input.total / res.input.length_m, 2) + '×); ' + s.corners + ' corner' + (s.corners === 1 ? '' : 's') +
      (s.corner_min_r != null ? ' (tightest ' + n(s.corner_min_r, 1) + ' m)' : '') +
      (s.corners ? ': ' + s.climbing_turns + ' climbing turn' + (s.climbing_turns === 1 ? '' : 's') + ', ' + s.switchbacks + ' switchback-like' : '') +
      ', ' + n(s.fall_m) + ' m of fall-line tread, ' + n(s.wet_m) + ' m on wet ground, ' + s.reversals + ' reversal site' + (s.reversals === 1 ? '' : 's') +
      '.<div class="tg-small tg-muted">' + terrainNote(res) + '</div>';
  }
  function run() {
    if (!checkLegs()) return;
    var mySeq = ++seq;
    result = null; $('rf-results').innerHTML = ''; drawMap();
    $('rf-run').disabled = true;
    status('<span class="tg-spin"></span> Starting…');
    api('POST', '/api/alignments/' + f.properties.id + '/refine', payload('run')).then(function (r) {
      runJob = r.job;
      return poll(r.job, mySeq, function (st) {
        var pct = Math.round(100 * (st.progress || 0));
        status((st.stage || 'Searching') + '… ' + pct + ' %' + (st.best != null ? ' · best so far ' + n(st.best) + ' m' : '') +
               (baseline ? ' · input ' + n(baseline.input.total) + ' m' : '') +
               '<div class="rf-bar"><i style="width:' + pct + '%"></i></div>');
      });
    }).then(function (res) {
      result = res; chosen = 0; stale = false;
      if (!baseline) baseline = res;
      status('Done in ' + n(res.elapsed_s, 1) + ' s. Seed ' + res.seed + ' (set it under Run parameters to repeat this run).');
      renderResults(); drawMap(); applyHistograms();
    }).catch(function (e) { if (active && mySeq === seq) status(esc(e.message), true); })
      .then(function () { var b = $('rf-run'); if (b) b.disabled = !checkLegs(); });
  }

  // ---- results -------------------------------------------------------------------------
  function renderResults() {
    var res = result, cs = res.candidates, inp = res.input, h = '';
    h += '<div id="rf-stale" class="tg-small tg-note" style="display:none">Settings changed since this run: run again to see their effect.</div>';
    if (!cs.length) {
      h += '<div class="tg-status">No candidate beat the input without making some factor notably worse. Try more iterations, a wider corridor, or different weights.</div>';
    }
    var factors = meta.factors.filter(function (m) {
      return (settings.factors[m.name].k > 0) || inp.contrib[m.name] || cs.some(function (c) { return c.contrib[m.name]; });
    });
    h += '<div class="tg-sub">Compare</div><div class="tg-scroll"><table class="tg-metrics rf-results"><tr><th></th><th title="Your line exactly as drawn, scored the same way">As drawn</th>';
    cs.forEach(function (c, i) {
      h += '<th><label title="Show this candidate"><input type="radio" name="rf-cand" value="' + i + '"' + (i === chosen ? ' checked' : '') + '> <i class="rf-sw" style="background:' + CAND[i] + '"></i>' + esc(c.label) + '</label></th>';
    });
    h += '</tr>';
    function row(label, get, fmt, lowerBetter, title) {
      var a = get(inp), t = '<tr' + (title ? ' title="' + esc(title) + '"' : '') + '><th>' + label + '</th><td>' + fmt(a) + '</td>';
      cs.forEach(function (c) {
        var b = get(c), cls = '';
        if (lowerBetter != null && a != null && b != null && Math.abs(b - a) > Math.max(1e-3, Math.abs(a) * 0.01)) cls = ((b < a) === lowerBetter) ? 'tg-better' : 'tg-worse';
        t += '<td class="' + cls + '">' + fmt(b) + '</td>';
      });
      return t + '</tr>';
    }
    h += row('<strong>Equivalent effort</strong>', function (d) { return d.total; }, function (v) { return '<strong>' + n(v) + ' m</strong>'; }, true,
             'The length of easy trail this line is as much work as: its length along the slope plus every factor\'s extra metres');
    h += row('&nbsp;&nbsp;Length along the slope', function (d) { return d.contrib.length; }, function (v) { return n(v) + ' m'; }, true, 'The base: the design line\'s length, climb included');
    factors.forEach(function (m) {
      h += row('&nbsp;&nbsp;+ ' + esc(m.label), function (d) { return d.contrib[m.name]; }, function (v) { return n(v) + ' m'; }, true, 'Extra metres from this factor: weight × curve, summed');
    });
    h += row('Effort per metre', function (d) { return d.total / d.length_m; }, function (v) { return n(v, 2) + '×'; }, true, 'Equivalent effort over length along the slope: 1.00× is easy trail all the way');
    h += row('Max grade', function (d) { return d.stats.grade_max; }, function (v) { return n(v, 1) + ' %'; }, true);
    h += row('Grade, 95th pct', function (d) { return d.stats.grade_p95; }, function (v) { return n(v, 1) + ' %'; }, true);
    h += row('Length on the map', function (d) { return d.length_map_m; }, function (v) { return n(v) + ' m'; }, null);
    h += row('From your line, max', function (d) { return d.stats.offset_max; }, function (v) { return n(v, 1) + ' m'; }, null, 'Straight-line distance to the nearest point of your line, before any allowance for rounding its corners');
    h += row('From your line, mean', function (d) { return d.stats.offset_mean; }, function (v) { return n(v, 1) + ' m'; }, null);
    h += row('Cut, max', function (d) { return d.stats.cut_max; }, function (v) { return n(v, 2) + ' m'; }, null, 'Deepest cut below the ground, corners dug in included');
    h += row('Fill, max', function (d) { return d.stats.fill_max; }, function (v) { return n(v, 2) + ' m'; }, null);
    h += row('Cut allowed along the line', function (d) { return d.t; }, function (v) { return n(v, 2) + ' m'; }, null, 'The search sets this; fill may go as far as costs the same on the cut and fill curve. Corners may dig deeper, to the parameter\'s limit');
    h += row('Deepest corner dig', function (d) { return d.stats.corner_dig_max; }, function (v) { return v == null ? '–' : n(v, 2) + ' m'; }, null, 'Where a corner is cut into the slope to hold its grade, the deepest cut there');
    h += row('Corners', function (d) { return d.stats.corners; }, function (v) { return n(v); }, null);
    h += row('Tightest corner', function (d) { return d.stats.corner_min_r; }, function (v) { return v == null ? '–' : n(v, 1) + ' m'; }, false);
    h += row('Climbing turns', function (d) { return d.stats.climbing_turns; }, function (v) { return n(v); }, null, 'Corners that cross the fall line on gentle ground (15 % or less): the trail can climb straight through');
    h += row('Switchback-like corners', function (d) { return d.stats.switchbacks; }, function (v) { return n(v); }, true, 'Corners that cross the fall line on ground of 25 % or more: a landing would have to be cut');
    h += row('Fall-line tread', function (d) { return d.stats.fall_m; }, function (v) { return n(v) + ' m'; }, null, 'Length within the set angle of the fall line, where water cannot leave the tread sideways');
    h += row('Wet ground', function (d) { return d.stats.wet_m; }, function (v) { return n(v) + ' m'; }, null, 'Length where the topographic wetness index is 10 or more (hollows, benches, channels, filled basins): likely mud');
    h += row('Tightest bend anywhere', function (d) { return d.stats.bend_min_r; }, function (v) { return v == null ? '–' : n(v, 1) + ' m'; }, false, 'Smallest radius over any 5 m of the line, corners included: kinks show here');
    h += row('Reversal sites (stub)', function (d) { return d.stats.reversals; }, function (v) { return n(v); }, null);
    h += '</table></div>';
    h += '<div class="tg-small tg-muted">' + terrainNote(res) + ' Your line is scored as drawn with the same cut/fill tube as the candidates. "Your line, smoothed" is the smooth reading of it every search starts from: the gain from rounding alone.</div>';
    if (cs.length) {
      h += '<div class="tg-sub">Profile <span class="tg-muted">ground thin, design grade line thick, cut and fill shaded; the input dashed</span></div><div id="rf-prof" class="rf-prof"></div>';
      h += '<div class="tg-actions"><button id="rf-save" class="tg-btn tg-primary">Save as variant</button> <a href="#" id="rf-zoom">Zoom to</a></div>';
    }
    $('rf-results').innerHTML = h;
    Array.prototype.forEach.call(document.querySelectorAll('input[name=rf-cand]'), function (r) {
      r.addEventListener('change', function () { chosen = +r.value; drawMap(); drawProfile(); });
    });
    if (!cs.length) return;
    drawProfile();
    $('rf-zoom').addEventListener('click', function (e) { e.preventDefault(); app.fitTo(candGeom(cs[chosen])); });
    $('rf-save').addEventListener('click', function () {
      var b = $('rf-save'); b.disabled = true;
      api('POST', '/api/alignments/' + f.properties.id + '/refine/accept', { job: runJob, candidate: chosen }).then(function (g) {
        close(); app.refreshTiles(); app.select('align', g.properties.id, false);
      }).catch(function (e) { b.disabled = false; status(esc(e.message), true); });
    });
  }
  function candGeom(c) {
    var cs = [];
    c.legs.forEach(function (L) { cs = cs.concat(cs.length ? L.slice(1) : L); });
    return { type: 'LineString', coordinates: cs };
  }

  function drawProfile() {
    var el = $('rf-prof'); if (!el || !result) return;
    el.innerHTML = '';
    var c = result.candidates[chosen], ip = result.input.profile, cp = c.profile, col = CAND[chosen];
    var W = Math.max(300, el.clientWidth || 500), H = 170, m = { l: 40, r: 8, t: 8, b: 22 };
    var w = W - m.l - m.r, h = H - m.t - m.b;
    var all = ip.ground.concat(ip.design, cp.ground, cp.design);
    var x = d3.scaleLinear().domain([0, Math.max(ip.d[ip.d.length - 1], cp.d[cp.d.length - 1])]).range([0, w]);
    var y = d3.scaleLinear().domain(d3.extent(all)).nice().range([h, 0]);
    var svg = d3.select(el).append('svg').attr('width', W).attr('height', H);
    var g = svg.append('g').attr('transform', 'translate(' + m.l + ',' + m.t + ')');
    g.append('g').attr('class', 'rf-axis').attr('transform', 'translate(0,' + h + ')').call(d3.axisBottom(x).ticks(6));
    g.append('g').attr('class', 'rf-axis').call(d3.axisLeft(y).ticks(4));
    function pts(p, key) { return p.d.map(function (d, i) { return [x(d), y(p[key][i])]; }); }
    var area = d3.area().x(function (i) { return x(cp.d[i]); }).y0(function (i) { return y(cp.ground[i]); }).y1(function (i) { return y(cp.design[i]); });
    g.append('path').attr('d', area(d3.range(cp.d.length))).attr('fill', col).attr('opacity', 0.25);
    var line = d3.line();
    g.append('path').attr('d', line(pts(ip, 'design'))).attr('fill', 'none').attr('stroke', '#777').attr('stroke-width', 1.5).attr('stroke-dasharray', '4 3');
    g.append('path').attr('d', line(pts(cp, 'ground'))).attr('fill', 'none').attr('stroke', '#999').attr('stroke-width', 1);
    g.append('path').attr('d', line(pts(cp, 'design'))).attr('fill', 'none').attr('stroke', col).attr('stroke-width', 2.2);
    (c.sites_d || []).forEach(function (d) {
      var i = d3.bisectLeft(cp.d, d);
      g.append('path').attr('d', d3.symbol(d3.symbolTriangle, 30)()).attr('transform', 'translate(' + x(d) + ',' + (y(cp.design[Math.min(i, cp.d.length - 1)]) + 9) + ') rotate(180)')
        .attr('fill', '#0f7fbf').append('title').text('Reversal site (stub) at ' + n(d) + ' m');
    });
    (c.corners || []).forEach(function (k) {
      g.append('line').attr('x1', x(k.d)).attr('x2', x(k.d)).attr('y1', 0).attr('y2', h).attr('stroke', '#7b3fa0').attr('stroke-dasharray', '2 2');
      g.append('text').attr('x', x(k.d) + 3).attr('y', 10).attr('class', 'rf-axis').attr('font-size', 10).attr('fill', '#7b3fa0')
        .text('r ' + n(k.radius_m) + ' m · ' + (k.kind === 'climbing turn' ? 'climbing' : k.kind === 'switchback-like' ? 'switchback' : 'between'))
        .append('title').text(k.kind + ': crosses the fall line on ' + n(k.crossing_pct) + ' % ground; turns ' + n(k.turn_deg) + '°' + (k.dig_m > 0.05 ? '; dug in ' + n(k.dig_m, 2) + ' m' : ''));
    });
  }

  // ---- map -------------------------------------------------------------------------------
  function addLayers() {
    var map = app.map;
    if (!map.getSource(SRC)) map.addSource(SRC, { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
    var colors = ['match', ['get', 'i'], 0, CAND[0], 1, CAND[1], 2, CAND[2], CAND[3]];
    if (!map.getLayer(LAYERS[0])) map.addLayer({ id: LAYERS[0], type: 'line', source: SRC,
      layout: { 'line-cap': 'round', 'line-join': 'round' },
      paint: { 'line-color': colors, 'line-width': 2, 'line-opacity': 0.55, 'line-dasharray': [2, 1.5] } });
    if (!map.getLayer(LAYERS[1])) map.addLayer({ id: LAYERS[1], type: 'line', source: SRC, filter: ['==', ['get', 'chosen'], true],
      layout: { 'line-cap': 'round', 'line-join': 'round' },
      paint: { 'line-color': colors, 'line-width': 4.5 } });
    drawMap();
  }
  function drawMap() {
    var src = app && app.map.getSource(SRC); if (!src) return;
    var feats = [];
    if (result) result.candidates.forEach(function (c, i) {
      feats.push({ type: 'Feature', properties: { i: i, chosen: i === chosen }, geometry: candGeom(c) });
    });
    src.setData({ type: 'FeatureCollection', features: feats });
  }

  return {
    open: open,
    close: close,
    active: function () { return active; },
    onStyleReload: function () { if (active) addLayers(); }
  };
})();
