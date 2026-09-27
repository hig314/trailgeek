/* tg_profile.js — the D3 elevation profile. Global `window.TgProfile`.
 *
 * Modelled on Ian's slope-segment figures (docs/TRAIL_ANALYSIS.md §2): the
 * line is coloured by grade with the diverging RdYlBu ramp and breaks at
 * ±5 / 18 / 35 %, uphill red and downhill blue.
 *
 * Two kinds of input:
 *   - quick-look samples [{d, z, lon, lat}] (GPS elevations, or terrain tiles
 *     sampled in the browser): grade is measured here over a 30 m window;
 *   - evaluator samples [{d, z, g, s, t, src, leg, lon, lat}] from the
 *     server (trailgeek_analysis): smoothed grade g, side-slope s and TSA t
 *     are used as given, and a TSA strip is drawn under the plot.
 * Samples with z = null are gaps (no DEM coverage), drawn as breaks.
 *
 *   var p = TgProfile.create(container, { onHover: function (sample|null) {} });
 *   p.render(samples, { legs: [{d_start, d_end, kind, name}], ghost: samples2 });
 *   TgProfile.stats(samples) -> { length_m, climb_m, descent_m, max_grade, min_z, max_z }
 *   p.clear();
 */
window.TgProfile = (function () {
  'use strict';

  var BREAKS = [-35, -18, -5, 5, 18, 35];       // % grade, uphill positive
  // RdYlBu reversed so that uphill (positive) is red, as in the figures.
  var COLORS = ['#4575b4', '#91bfdb', '#e0f3f8', '#ffffbf', '#fee090', '#fc8d59', '#d73027'];
  var LABELS = ['< −35 %', '−35 to −18', '−18 to −5', '−5 to 5', '5 to 18', '18 to 35', '> 35 %'];
  // TSA: one hue, darkest = closest to the fall line (worst for erosion).
  var TSA_BREAKS = [45, 60, 68];
  var TSA_COLORS = ['#1c3d4f', '#3e7390', '#86b2c8', '#d4e6ee'];
  var TSA_LABELS = ['< 45°', '45–60°', '60–68°', '≥ 68°'];
  // Leg kinds (validated categorical set; see site.css for the map twins).
  var LEG_COLORS = { existing: '#4d7c2a', 'new': '#7b3fa0', reroute: '#d9601a', restore: '#0f7fbf' };
  var LEG_LABELS = { existing: 'Existing trail', 'new': 'New construction', reroute: 'Reroute', restore: 'Restoration' };
  var GRADE_WINDOW = 30;                         // metres, quick-look samples only

  function classOf(g) {
    for (var i = 0; i < BREAKS.length; i++) if (g < BREAKS[i]) return i;
    return BREAKS.length;
  }
  function tsaClass(t) {
    for (var i = 0; i < TSA_BREAKS.length; i++) if (t < TSA_BREAKS[i]) return i;
    return TSA_BREAKS.length;
  }
  function ok(v) { return v !== null && v !== undefined && isFinite(v); }

  // Running grade (%) for quick-look samples, over ±GRADE_WINDOW/2 m.
  function grades(s) {
    var half = GRADE_WINDOW / 2, out = new Array(s.length), j0 = 0, j1 = 0;
    for (var i = 0; i < s.length; i++) {
      while (j0 < i && s[i].d - s[j0].d > half) j0++;
      while (j1 < s.length - 1 && s[j1 + 1].d - s[i].d <= half) j1++;
      // Sparse input (a GPS track with vertices hundreds of metres apart):
      // always reach at least one neighbour each side.
      var a = Math.min(j0, Math.max(0, i - 1)), b = Math.max(j1, Math.min(s.length - 1, i + 1));
      var run = s[b].d - s[a].d;
      out[i] = run > 0 && ok(s[b].z) && ok(s[a].z) ? (s[b].z - s[a].z) / run * 100 : null;
    }
    return out;
  }

  function stats(s) {
    if (!s || s.length < 2) return null;
    var up = 0, down = 0, minZ = Infinity, maxZ = -Infinity, g = grades(s), maxG = 0, prev = null;
    for (var i = 0; i < s.length; i++) {
      if (!ok(s[i].z)) continue;
      if (s[i].z < minZ) minZ = s[i].z;
      if (s[i].z > maxZ) maxZ = s[i].z;
      if (ok(g[i]) && Math.abs(g[i]) > Math.abs(maxG)) maxG = g[i];
      if (prev !== null) { var dz = s[i].z - prev; if (dz > 0) up += dz; else down -= dz; }
      prev = s[i].z;
    }
    return { length_m: s[s.length - 1].d, climb_m: up, descent_m: down, max_grade: maxG, min_z: minZ, max_z: maxZ };
  }

  // Server evaluation profile (columnar) -> samples.
  function fromEvaluation(p) {
    var out = [];
    for (var i = 0; i < p.d.length; i++) {
      out.push({ d: p.d[i], z: p.z[i], g: p.g[i], s: p.s[i], t: p.t[i], src: p.src[i], leg: p.leg[i],
                 lon: p.lon ? p.lon[i] : null, lat: p.lat ? p.lat[i] : null });
    }
    return out;
  }

  function create(container, opts) {
    opts = opts || {};
    var el = typeof container === 'string' ? document.querySelector(container) : container;
    var svg = d3.select(el).append('svg').attr('class', 'tg-profile');
    var samples = null, gradeArr = null, cursor = null, ropts = {};
    var margin = { top: 16, right: 12, bottom: 28, left: 44 };

    function draw() {
      svg.selectAll('*').remove();
      if (!samples || samples.length < 2) return;
      var hasTsa = samples.some(function (s) { return 't' in s; });
      var strip = hasTsa ? 12 : 0;
      var W = el.clientWidth || 320, H = (opts.height || 190) + strip;
      svg.attr('width', W).attr('height', H).attr('viewBox', '0 0 ' + W + ' ' + H);
      var iw = W - margin.left - margin.right, ih = H - margin.top - margin.bottom - strip;
      var dmax = samples[samples.length - 1].d;
      var x = d3.scaleLinear().domain([0, dmax]).range([0, iw]);
      var zs = samples.map(function (s) { return s.z; }).filter(ok);
      if (ropts.ghost) ropts.ghost.forEach(function (s) { if (ok(s.z)) zs.push(s.z); });
      if (!zs.length) {
        svg.append('text').attr('x', 12).attr('y', 30).attr('class', 'tg-empty').text('No elevation data along this line.');
        return;
      }
      var ext = d3.extent(zs);
      var pad = Math.max(5, (ext[1] - ext[0]) * 0.08);
      var y = d3.scaleLinear().domain([ext[0] - pad, ext[1] + pad]).range([ih, 0]).nice();
      var g = svg.append('g').attr('transform', 'translate(' + margin.left + ',' + margin.top + ')');

      g.append('g').attr('class', 'tg-grid')
        .call(d3.axisLeft(y).ticks(4).tickSize(-iw).tickFormat(''));
      g.append('g').attr('class', 'tg-axis').attr('transform', 'translate(0,' + (ih + strip) + ')')
        .call(d3.axisBottom(x).ticks(5).tickFormat(function (d) { return (d / 1000).toFixed(dmax >= 10000 ? 0 : 1) + ' km'; }));
      g.append('g').attr('class', 'tg-axis')
        .call(d3.axisLeft(y).ticks(4).tickFormat(function (d) { return d + ' m'; }));

      // Legs: a kind-coloured band above the plot and a rule at each joint.
      (ropts.legs || []).forEach(function (L, i) {
        if (!(L.d_end > L.d_start)) return;
        g.append('rect').attr('class', 'tg-legband').attr('x', x(L.d_start)).attr('y', -margin.top + 3)
          .attr('width', Math.max(1, x(L.d_end) - x(L.d_start) - 1)).attr('height', 6)
          .attr('fill', LEG_COLORS[L.kind] || '#888').append('title')
          .text((i + 1) + '. ' + (L.name || LEG_LABELS[L.kind] || L.kind));
        if (i > 0) g.append('line').attr('class', 'tg-legrule').attr('x1', x(L.d_start)).attr('x2', x(L.d_start))
          .attr('y1', 0).attr('y2', ih);
      });

      var defined = function (s) { return ok(s.z); };
      var line = d3.line().defined(defined).x(function (s) { return x(s.d); }).y(function (s) { return y(s.z); });
      if (ropts.ghost) g.append('path').datum(ropts.ghost).attr('class', 'tg-ghost').attr('d', line);
      var area = d3.area().defined(defined).x(function (s) { return x(s.d); }).y0(ih).y1(function (s) { return y(s.z); });
      g.append('path').datum(samples).attr('class', 'tg-area').attr('d', area);
      // The line as one path per grade class so each stretch wears its colour.
      var runStart = 0;
      for (var i = 1; i <= samples.length; i++) {
        var cls = ok(gradeArr[runStart]) ? classOf(gradeArr[runStart]) : -1;
        var next = i < samples.length ? (ok(gradeArr[i]) ? classOf(gradeArr[i]) : -1) : null;
        if (i === samples.length || next !== cls) {
          var seg = samples.slice(runStart, Math.min(i + 1, samples.length));
          g.append('path').datum(seg).attr('class', 'tg-line').attr('stroke', cls < 0 ? '#999' : COLORS[cls]).attr('d', line);
          runStart = i;
        }
      }
      // Stretches that came from the regional DEM, not lidar: a faint hatch.
      if (ropts.contextSrc && ropts.contextSrc.length) {
        var ctxRuns = [], st = null;
        samples.forEach(function (s, k) {
          var isCtx = ropts.contextSrc.indexOf(s.src) >= 0;
          if (isCtx && st === null) st = k;
          if ((!isCtx || k === samples.length - 1) && st !== null) { ctxRuns.push([samples[st].d, s.d]); st = null; }
        });
        ctxRuns.forEach(function (r) {
          g.append('rect').attr('class', 'tg-ctx').attr('x', x(r[0])).attr('y', ih - 4)
            .attr('width', Math.max(1, x(r[1]) - x(r[0]))).attr('height', 4)
            .append('title').text('Regional DEM here (no lidar): grade and TSA are coarse');
        });
      }
      // TSA strip under the plot.
      if (hasTsa) {
        var sg = g.append('g').attr('transform', 'translate(0,' + (ih + 2) + ')');
        for (var k = 0; k < samples.length - 1; k++) {
          var t = samples[k + 1].t;
          if (!ok(t)) continue;
          sg.append('rect').attr('x', x(samples[k].d)).attr('width', Math.max(0.6, x(samples[k + 1].d) - x(samples[k].d)))
            .attr('height', strip - 4).attr('fill', TSA_COLORS[tsaClass(t)]);
        }
        g.append('text').attr('class', 'tg-strip-label').attr('x', -4).attr('y', ih + strip - 3).attr('text-anchor', 'end').text('TSA');
      }

      // Hover: crosshair + readout, nearest sample by distance.
      var cross = g.append('g').attr('class', 'tg-cursor').style('display', 'none');
      cross.append('line').attr('y1', 0).attr('y2', ih);
      cross.append('circle').attr('r', 4);
      var tip = d3.select(el).selectAll('.tg-tip').data([0]).join('div').attr('class', 'tg-tip').style('display', 'none');
      var bisect = d3.bisector(function (s) { return s.d; }).center;
      g.append('rect').attr('class', 'tg-hit').attr('width', iw).attr('height', ih + strip)
        .on('mousemove touchmove', function (ev) {
          var mx = d3.pointer(ev, this)[0];
          var i = bisect(samples, x.invert(mx));
          var s = samples[i];
          cross.style('display', null).attr('transform', 'translate(' + x(s.d) + ',0)');
          cross.select('circle').attr('cy', ok(s.z) ? y(s.z) : ih).attr('fill', ok(gradeArr[i]) ? COLORS[classOf(gradeArr[i])] : '#999');
          var lines = [(s.d / 1000).toFixed(2) + ' km · ' + (ok(s.z) ? Math.round(s.z) + ' m' : 'no data')];
          if (ok(gradeArr[i])) lines.push('grade ' + (gradeArr[i] >= 0 ? '+' : '') + gradeArr[i].toFixed(0) + ' %');
          if (ok(s.s)) lines.push('side-slope ' + Math.round(s.s) + ' %');
          if (ok(s.t)) lines.push('TSA ' + Math.round(s.t) + '°');
          if (ropts.legs && s.leg != null && ropts.legs[s.leg]) {
            var L = ropts.legs[s.leg];
            lines.push('leg ' + (s.leg + 1) + ': ' + (L.name || LEG_LABELS[L.kind] || L.kind));
          }
          tip.style('display', null).style('left', Math.min(mx + margin.left + 10, W - 150) + 'px').html(lines.join('<br>'));
          cursor = s;
          if (opts.onHover) opts.onHover(s, i);
        })
        .on('mouseleave touchend', function () {
          cross.style('display', 'none'); tip.style('display', 'none'); cursor = null;
          if (opts.onHover) opts.onHover(null);
        });
    }

    var ro = window.ResizeObserver ? new ResizeObserver(function () { draw(); }) : null;
    if (ro) ro.observe(el);

    return {
      render: function (s, o) {
        samples = s; ropts = o || {};
        gradeArr = s.length && ('g' in s[0]) ? s.map(function (x) { return x.g; }) : grades(s);
        draw();
      },
      clear: function () { samples = null; svg.selectAll('*').remove(); d3.select(el).selectAll('.tg-tip').remove(); },
      stats: stats,
      cursor: function () { return cursor; },
      legend: function (withTsa) {
        var h = COLORS.map(function (c, i) {
          return '<span class="tg-key"><i style="background:' + c + '"></i>' + LABELS[i] + '</span>';
        }).join('');
        if (withTsa) {
          h += '<span class="tg-key-sep">TSA</span>' + TSA_COLORS.map(function (c, i) {
            return '<span class="tg-key"><i style="background:' + c + '"></i>' + TSA_LABELS[i] + '</span>';
          }).join('');
        }
        return h;
      }
    };
  }

  return { create: create, stats: stats, grades: grades, fromEvaluation: fromEvaluation,
           BREAKS: BREAKS, COLORS: COLORS, LEG_COLORS: LEG_COLORS, LEG_LABELS: LEG_LABELS };
})();
