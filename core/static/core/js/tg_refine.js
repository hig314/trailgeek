/* tg_refine.js — the refine tool's panel (docs/REFINE_TOOL.md).
 *
 * Opened from an alignment's panel ("Refine…"). The person picks the legs
 * (a continuous stretch; existing trail unticked by default), shapes one
 * cost curve per factor with a weight k beside it, and runs. The search runs
 * in the worker (core.refine); this file starts it, shows progress, draws
 * the candidates on the map and their profiles, compares them with the
 * input scored the same way, and saves a chosen one as a variant.
 *
 * Curve editor: curves are piecewise linear, at most 9 nodes, y 0..1. Drag a
 * node; drag a flat stretch up or down; drag a ramp sideways; double-click a
 * segment to add a node; drag a node off the chart to remove it. The grey
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
  var CAND = ['#d9480f', '#1864ab', '#2b8a3e'];
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
    h += '<div class="tg-small tg-muted">Proposes modest tweaks that fit the terrain better. Nothing changes until you save a candidate, which becomes a variant of this alignment. <a href="#" id="rf-back">Back to the alignment</a></div>';
    h += '<div class="tg-sub">Legs to refine</div><div id="rf-legs" class="rf-legs"></div><div id="rf-legmsg" class="tg-small tg-error"></div>';
    h += '<div class="tg-sub">Factors</div><div class="tg-small tg-muted">Each curve gives the cost, 0 to 1, by value; k weights it, and 0 ignores the factor. Drag a node; drag a flat stretch up or down, a ramp sideways; double-click a segment to add a node; drag a node off the chart to remove it. Grey bars: the selected legs today.</div>';
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

  function renderFactors() {
    var box = $('rf-factors'); box.innerHTML = ''; editors = {};
    meta.factors.forEach(function (m) {
      var s = settings.factors[m.name];
      var row = document.createElement('div');
      row.className = 'rf-factor';
      row.innerHTML = '<div class="rf-fhead"><strong>' + esc(m.label) + '</strong>' +
        (m.stub ? ' <span class="tg-badge" title="Deliberately primitive for now: sites are low points of the design line and ground dips; no groundwater or terrain below the trail yet">stub</span>' : '') +
        '<span class="tg-muted tg-small">' + esc(m.x) + ' (' + esc(m.unit) + ') · ' +
        (m.per === 'metre' ? 'per metre' : m.per === 'corner' ? 'per corner' : 'once') + '</span>' +
        '<label class="rf-k" title="Weight of this factor; 0 ignores it">k <input type="number" min="0" max="1000" step="0.5" value="' + s.k + '"></label></div>' +
        '<div class="rf-curve"></div>';
      box.appendChild(row);
      row.querySelector('.rf-k input').addEventListener('change', function () {
        var v = Math.max(0, +this.value || 0); this.value = v; s.k = v; changed();
      });
      editors[m.name] = curveEditor(row.querySelector('.rf-curve'), m, s.curve, changed);
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
        if (p.name === 'corridor_m' || p.name === 'spacing_m') queueBaseline(400);
      });
    });
  }
  function changed() {
    if (result && !stale) { stale = true; var r = $('rf-stale'); if (r) r.style.display = ''; }
  }

  // ---- the curve editor ----------------------------------------------------------
  function curveEditor(el, fm, nodes, onChange) {
    var W = Math.max(280, Math.min(520, el.clientWidth || 480)), H = 92;
    var m = { l: 28, r: 10, t: 6, b: 18 }, w = W - m.l - m.r, h = H - m.t - m.b;
    var x = d3.scaleLinear().domain([fm.xmin, fm.xmax]).range([0, w]);
    var y = d3.scaleLinear().domain([0, 1]).range([h, 0]);
    var svg = d3.select(el).append('svg').attr('width', W).attr('height', H).attr('class', 'rf-svg');
    var g = svg.append('g').attr('transform', 'translate(' + m.l + ',' + m.t + ')');
    var gh = g.append('g'), gm = g.append('g');
    g.append('g').attr('class', 'rf-axis').attr('transform', 'translate(0,' + h + ')').call(d3.axisBottom(x).ticks(6).tickSizeOuter(0));
    g.append('g').attr('class', 'rf-axis').call(d3.axisLeft(y).tickValues([0, 0.5, 1]).tickSizeOuter(0));
    var path = g.append('path').attr('class', 'rf-line');
    var segs = g.append('g'), dots = g.append('g');
    var hist = null;
    var snap = fm.snap || 1;
    function snapX(v) { return Math.round(v / snap) * snap; }
    function snapY(v) { return Math.round(v * 20) / 20; }
    function clampX(v) { return Math.max(fm.xmin, Math.min(fm.xmax, v)); }

    var nodeDrag = d3.drag()
      .on('drag', function (ev, d) {
        var i = nodes.indexOf(d); if (i < 0) return;
        var off = ev.y < -24 || ev.y > h + 24;
        d3.select(this).classed('rf-del', off && nodes.length > 2);
        var lo = i > 0 ? nodes[i - 1][0] : fm.xmin, hi = i < nodes.length - 1 ? nodes[i + 1][0] : fm.xmax;
        d[0] = Math.max(lo, Math.min(hi, snapX(clampX(x.invert(ev.x)))));
        if (!off) d[1] = Math.max(0, Math.min(1, snapY(y.invert(ev.y))));
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
        if (Math.abs(n0[0][1] - n0[1][1]) < 1e-9) {          // a flat stretch moves up or down
          a[1] = b[1] = Math.max(0, Math.min(1, snapY(y.invert(ev.y))));
        } else {                                             // a ramp moves sideways
          var dx = x.invert(ev.x) - x.invert(this._x0);
          var lo = i > 0 ? nodes[i - 1][0] : fm.xmin, hi = i + 2 < nodes.length ? nodes[i + 2][0] : fm.xmax;
          dx = Math.max(lo - n0[0][0], Math.min(hi - n0[1][0], dx));
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
      var yv = b[0] > a[0] ? a[1] + (b[1] - a[1]) * (xv - a[0]) / (b[0] - a[0]) : a[1];
      nodes.splice(i + 1, 0, [xv, snapY(yv)]);
      draw(); onChange();
    }
    function draw() {
      gh.selectAll('rect').remove();
      if (hist && hist.counts) {
        var mx = d3.max(hist.counts) || 1, bw = w / hist.counts.length;
        gh.selectAll('rect').data(hist.counts).enter().append('rect').attr('class', 'rf-hist')
          .attr('x', function (d, i) { return i * bw; }).attr('width', Math.max(1, bw - 1))
          .attr('y', function (d) { return h - h * d / mx; }).attr('height', function (d) { return h * d / mx; })
          .append('title').text(function (d) { return n(d, hist.unit === 'm' ? 0 : 0) + ' ' + hist.unit; });
      }
      var pts = nodes.map(function (q) { return [x(q[0]), y(q[1])]; });
      path.attr('d', d3.line()([[0, pts[0][1]]].concat(pts, [[w, pts[pts.length - 1][1]]])));
      var sl = segs.selectAll('line').data(d3.range(nodes.length - 1));
      sl.exit().remove();
      sl.enter().append('line').attr('class', 'rf-seg').call(segDrag).on('dblclick', split)
        .merge(sl)
        .attr('x1', function (i) { return x(nodes[i][0]); }).attr('y1', function (i) { return y(nodes[i][1]); })
        .attr('x2', function (i) { return x(nodes[i + 1][0]); }).attr('y2', function (i) { return y(nodes[i + 1][1]); })
        .classed('rf-flat', function (i) { return Math.abs(nodes[i][1] - nodes[i + 1][1]) < 1e-9; });
      var dd = dots.selectAll('circle').data(nodes);
      dd.exit().remove();
      dd.enter().append('circle').attr('class', 'rf-node').attr('r', 5).call(nodeDrag)
        .merge(dd).attr('cx', function (d) { return x(d[0]); }).attr('cy', function (d) { return y(d[1]); })
        .classed('rf-del', false)
        .each(function (d) { d3.select(this).selectAll('title').remove(); d3.select(this).append('title').text(d[0] + ' ' + fm.unit + ' → ' + d[1]); });
    }
    draw();
    return {
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
    if (editors['length']) {
      var marks = [{ x: 1, color: '#555' }];
      if (result) result.candidates.forEach(function (c, i) { marks.push({ x: c.length_m / result.input.length_m, color: CAND[i] }); });
      editors['length'].setMarks(marks);
    }
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
      n(s.grade_p95, 1) + ' %, ' + s.corners + ' corner' + (s.corners === 1 ? '' : 's') +
      (s.corner_min_r != null ? ' (tightest ' + n(s.corner_min_r, 1) + ' m)' : '') + ', ' + s.reversals + ' reversal site' + (s.reversals === 1 ? '' : 's') +
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
        status((st.stage || 'Searching') + '… ' + pct + ' %' + (st.best != null ? ' · best so far ' + n(st.best, 3) : '') +
               (baseline ? ' · input ' + n(baseline.input.total, 3) : '') +
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
    h += '<div class="tg-sub">Compare</div><div class="tg-scroll"><table class="tg-metrics rf-results"><tr><th></th><th>Input</th>';
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
    h += row('<strong>Total cost</strong>', function (d) { return d.total; }, function (v) { return n(v, 3); }, true);
    factors.forEach(function (m) {
      h += row('&nbsp;&nbsp;' + esc(m.label), function (d) { return d.contrib[m.name]; }, function (v) { return n(v, 3); }, true, 'k × cost');
    });
    h += row('Max grade', function (d) { return d.stats.grade_max; }, function (v) { return n(v, 1) + ' %'; }, true);
    h += row('Grade, 95th pct', function (d) { return d.stats.grade_p95; }, function (v) { return n(v, 1) + ' %'; }, true);
    h += row('Length', function (d) { return d.length_m; }, function (v) { return n(v) + ' m'; }, null);
    h += row('Offset, max', function (d) { return d.stats.offset_max; }, function (v) { return n(v, 1) + ' m'; }, null);
    h += row('Offset, mean', function (d) { return d.stats.offset_mean; }, function (v) { return n(v, 1) + ' m'; }, null);
    h += row('Cut or fill, max', function (d) { return d.stats.cut_max; }, function (v) { return n(v, 2) + ' m'; }, null);
    h += row('Tube half-width', function (d) { return d.t; }, function (v) { return n(v, 2) + ' m'; }, null);
    h += row('Corners', function (d) { return d.stats.corners; }, function (v) { return n(v); }, null);
    h += row('Tightest corner', function (d) { return d.stats.corner_min_r; }, function (v) { return v == null ? '–' : n(v, 1) + ' m'; }, false);
    h += row('Reversal sites (stub)', function (d) { return d.stats.reversals; }, function (v) { return n(v); }, null);
    h += '</table></div>';
    h += '<div class="tg-small tg-muted">' + terrainNote(res) + ' The input is scored with the same cut/fill tube as the candidates, so the comparison is fair.</div>';
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
      g.append('text').attr('x', x(k.d) + 3).attr('y', 10).attr('class', 'rf-axis').attr('font-size', 10).attr('fill', '#7b3fa0').text('r ' + n(k.radius_m) + ' m');
    });
  }

  // ---- map -------------------------------------------------------------------------------
  function addLayers() {
    var map = app.map;
    if (!map.getSource(SRC)) map.addSource(SRC, { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
    var colors = ['match', ['get', 'i'], 0, CAND[0], 1, CAND[1], CAND[2]];
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
