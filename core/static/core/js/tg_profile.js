/* tg_profile.js — the D3 elevation profile. Global `window.TgProfile`.
 *
 * Modelled on Ian's slope-segment figures (docs/TRAIL_ANALYSIS.md §2): the
 * line is coloured by grade with the diverging RdYlBu ramp and breaks at
 * ±5 / 18 / 35 %, uphill red and downhill blue. Grade is measured over a
 * running window (default 30 m) so GPS and terrain-tile noise does not paint
 * every other segment a different colour. A hover crosshair reports distance,
 * elevation and grade and calls back so the map can show where that is.
 *
 *   var p = TgProfile.create(container, { onHover: function (sample|null) {} });
 *   p.render(samples, { source: 'GPS elevation' });   // [{d, z, lon, lat}, …]
 *   p.stats(samples) -> { length_m, climb_m, descent_m, max_grade, min_z, max_z }
 *   p.clear();
 */
window.TgProfile = (function () {
  'use strict';

  var BREAKS = [-35, -18, -5, 5, 18, 35];       // % grade, uphill positive
  // RdYlBu reversed so that uphill (positive) is red, as in the figures.
  var COLORS = ['#4575b4', '#91bfdb', '#e0f3f8', '#ffffbf', '#fee090', '#fc8d59', '#d73027'];
  var LABELS = ['< −35 %', '−35 to −18', '−18 to −5', '−5 to 5', '5 to 18', '18 to 35', '> 35 %'];
  var GRADE_WINDOW = 30;                         // metres, ± half either side

  function classOf(g) {
    for (var i = 0; i < BREAKS.length; i++) if (g < BREAKS[i]) return i;
    return BREAKS.length;
  }

  // Running grade (%) at each sample, from the elevation change across a
  // window of ±GRADE_WINDOW/2 m centred on it.
  function grades(s) {
    var half = GRADE_WINDOW / 2, out = new Array(s.length), j0 = 0, j1 = 0;
    for (var i = 0; i < s.length; i++) {
      while (j0 < i && s[i].d - s[j0].d > half) j0++;
      while (j1 < s.length - 1 && s[j1 + 1].d - s[i].d <= half) j1++;
      // Sparse input (a GPS track with vertices hundreds of metres apart):
      // always reach at least one neighbour each side, else the window is
      // a single point and every grade reads 0.
      var a = Math.min(j0, Math.max(0, i - 1)), b = Math.max(j1, Math.min(s.length - 1, i + 1));
      var run = s[b].d - s[a].d;
      out[i] = run > 0 ? (s[b].z - s[a].z) / run * 100 : 0;
    }
    return out;
  }

  function stats(s) {
    if (!s || s.length < 2) return null;
    var up = 0, down = 0, minZ = Infinity, maxZ = -Infinity, g = grades(s), maxG = 0;
    for (var i = 0; i < s.length; i++) {
      if (s[i].z < minZ) minZ = s[i].z;
      if (s[i].z > maxZ) maxZ = s[i].z;
      if (Math.abs(g[i]) > Math.abs(maxG)) maxG = g[i];
      if (i) { var dz = s[i].z - s[i - 1].z; if (dz > 0) up += dz; else down -= dz; }
    }
    return { length_m: s[s.length - 1].d, climb_m: up, descent_m: down, max_grade: maxG, min_z: minZ, max_z: maxZ };
  }

  function create(container, opts) {
    opts = opts || {};
    var el = typeof container === 'string' ? document.querySelector(container) : container;
    var svg = d3.select(el).append('svg').attr('class', 'tg-profile');
    var samples = null, gradeArr = null, cursor = null;
    var margin = { top: 10, right: 12, bottom: 28, left: 44 };

    function draw() {
      svg.selectAll('*').remove();
      if (!samples || samples.length < 2) return;
      var W = el.clientWidth || 320, H = opts.height || 180;
      svg.attr('width', W).attr('height', H).attr('viewBox', '0 0 ' + W + ' ' + H);
      var iw = W - margin.left - margin.right, ih = H - margin.top - margin.bottom;
      var x = d3.scaleLinear().domain([0, samples[samples.length - 1].d]).range([0, iw]);
      var ext = d3.extent(samples, function (s) { return s.z; });
      var pad = Math.max(5, (ext[1] - ext[0]) * 0.08);
      var y = d3.scaleLinear().domain([ext[0] - pad, ext[1] + pad]).range([ih, 0]).nice();
      var g = svg.append('g').attr('transform', 'translate(' + margin.left + ',' + margin.top + ')');

      g.append('g').attr('class', 'tg-grid')
        .call(d3.axisLeft(y).ticks(4).tickSize(-iw).tickFormat(''));
      g.append('g').attr('class', 'tg-axis').attr('transform', 'translate(0,' + ih + ')')
        .call(d3.axisBottom(x).ticks(5).tickFormat(function (d) { return (d / 1000).toFixed(d >= 10000 ? 0 : 1) + ' km'; }));
      g.append('g').attr('class', 'tg-axis')
        .call(d3.axisLeft(y).ticks(4).tickFormat(function (d) { return d + ' m'; }));

      // Fill under the line, then the line as one path per grade class so
      // each stretch wears its class colour.
      var area = d3.area().x(function (s) { return x(s.d); }).y0(ih).y1(function (s) { return y(s.z); });
      g.append('path').datum(samples).attr('class', 'tg-area').attr('d', area);
      var line = d3.line().x(function (s) { return x(s.d); }).y(function (s) { return y(s.z); });
      var runStart = 0;
      for (var i = 1; i <= samples.length; i++) {
        var cls = classOf(gradeArr[runStart]);
        if (i === samples.length || classOf(gradeArr[i]) !== cls) {
          var seg = samples.slice(runStart, Math.min(i + 1, samples.length));
          g.append('path').datum(seg).attr('class', 'tg-line').attr('stroke', COLORS[cls]).attr('d', line);
          runStart = i;
        }
      }

      // Hover: crosshair + readout, nearest sample by distance.
      var cross = g.append('g').attr('class', 'tg-cursor').style('display', 'none');
      cross.append('line').attr('y1', 0).attr('y2', ih);
      cross.append('circle').attr('r', 4);
      var tip = d3.select(el).selectAll('.tg-tip').data([0]).join('div').attr('class', 'tg-tip').style('display', 'none');
      var bisect = d3.bisector(function (s) { return s.d; }).center;
      g.append('rect').attr('class', 'tg-hit').attr('width', iw).attr('height', ih)
        .on('mousemove touchmove', function (ev) {
          var mx = d3.pointer(ev, this)[0];
          var i = bisect(samples, x.invert(mx));
          var s = samples[i];
          cross.style('display', null).attr('transform', 'translate(' + x(s.d) + ',0)');
          cross.select('circle').attr('cy', y(s.z)).attr('fill', COLORS[classOf(gradeArr[i])]);
          tip.style('display', null)
            .style('left', Math.min(mx + margin.left + 10, W - 130) + 'px')
            .html((s.d / 1000).toFixed(2) + ' km · ' + Math.round(s.z) + ' m<br>grade ' +
                  (gradeArr[i] >= 0 ? '+' : '') + gradeArr[i].toFixed(0) + ' %');
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
      render: function (s) { samples = s; gradeArr = grades(s); draw(); },
      clear: function () { samples = null; svg.selectAll('*').remove(); d3.select(el).selectAll('.tg-tip').remove(); },
      stats: stats,
      cursor: function () { return cursor; },
      legend: function () {
        return COLORS.map(function (c, i) {
          return '<span class="tg-key"><i style="background:' + c + '"></i>' + LABELS[i] + '</span>';
        }).join('');
      }
    };
  }

  return { create: create, stats: stats, grades: grades, BREAKS: BREAKS, COLORS: COLORS };
})();
