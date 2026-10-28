/* tg_design.js — the trail design side of the home map: an alignment's
 * panel (legs, evaluation, profile, compare with its siblings), a project's
 * compare table, starting a new alignment, and edit mode with a live
 * evaluation of every change against the saved version.
 * Global `window.TgDesign`; map.js calls TgDesign.init(app) once.
 *
 * Evaluations come from the server (trailgeek_analysis in a Huey job) for
 * both the saved alignment and the live edit, so the numbers you tweak
 * against are the numbers that get saved.
 */
window.TgDesign = (function () {
  'use strict';

  var app = null, ed = null;
  var cur = null;            // the alignment feature being shown or edited
  var editing = false, newEmpty = false, dirty = false;
  var live = null;           // latest live evaluation result
  var seq = 0, evalTimer = null, pollTimer = null;
  var LEG_COLORS = TgProfile.LEG_COLORS, LEG_LABELS = TgProfile.LEG_LABELS;
  var KINDS = ['existing', 'new', 'reroute', 'restore'];

  function $(id) { return document.getElementById(id); }
  function esc(t) { return app.esc(t); }
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

  // ---- number formatting --------------------------------------------------
  function n(v, d) { return v == null ? '–' : (+v).toFixed(d == null ? 0 : d); }
  function km(m) { return m == null ? '–' : (m / 1000).toFixed(m < 10000 ? 2 : 1) + ' km'; }
  function kmn(m) { return m == null ? '–' : (m / 1000).toFixed(m < 10000 ? 2 : 1); }   // unit in the column header
  function mi(m) { return m == null ? '' : (m / 1609.344).toFixed(2) + ' mi'; }

  // The rows of the tradeoff table: [label, key, formatter, lowerIsBetter]
  function metricRows(h) {
    var st = h && h.steep_threshold != null ? h.steep_threshold : 18;
    var fl = h && h.fall_line_threshold != null ? h.fall_line_threshold : 45;
    var hs = h && h.heavy_slope_threshold != null ? h.heavy_slope_threshold : 73;
    return [
      ['Length', 'length_m', km, null],
      ['New construction', 'build_m', km, null],
      ['On existing trail', 'existing_m', km, null],
      ['Climb (smoothed)', 'climb_m', function (v) { return n(v) + ' m'; }, null],
      ['Grade, 95th percentile', 'grade_p95', function (v) { return n(v) + ' %'; }, true],
      ['Steeper than ' + st + ' %', 'steep_pct', function (v) { return n(v, 1) + ' %'; }, true],
      ['Longest run > ' + st + ' %', 'steep_longest_m', function (v) { return n(v) + ' m'; }, true],
      ['Near fall line (TSA < ' + fl + '°)', 'fall_line_pct', function (v) { return n(v, 1) + ' %'; }, true],
      ['Side-slope > ' + hs + ' %', 'heavy_slope_pct', function (v) { return n(v, 1) + ' %'; }, true],
      ['Build effort', 'construct_days', function (v) { return n(v, 1) + ' crew-days'; }, true],
      ['Maintenance per pass', 'maintain_days', function (v) { return n(v, 1) + ' days'; }, true],
      ['Lidar coverage', 'lidar_pct', function (v) { return n(v) + ' %'; }, false]
    ];
  }
  function statsTable(h) {
    if (!h) return '';
    var t = '<table class="tg-metrics">';
    metricRows(h).forEach(function (r) { t += '<tr><th>' + r[0] + '</th><td>' + r[2](h[r[1]]) + '</td></tr>'; });
    return t + '</table>';
  }
  function tradeoffTable(saved, now) {
    var h = now || saved;
    var t = '<table class="tg-metrics tg-trade"><tr><th></th><th>Saved</th><th>Now</th><th>Change</th></tr>';
    metricRows(h).forEach(function (r) {
      var a = saved ? saved[r[1]] : null, b = now ? now[r[1]] : null, d = '';
      if (a != null && b != null) {
        var delta = b - a, tol = Math.max(Math.abs(a) * 0.005, 0.05);
        if (Math.abs(delta) > tol) {
          var better = r[3] === null ? null : (r[3] ? delta < 0 : delta > 0);
          var cls = better === null ? '' : (better ? ' tg-better' : ' tg-worse');
          d = '<span class="tg-delta' + cls + '">' + (delta > 0 ? '▲ ' : '▼ ') + r[2](Math.abs(delta)).replace(/^-/, '') + '</span>';
        }
      }
      t += '<tr><th>' + r[0] + '</th><td>' + (saved ? r[2](a) : '–') + '</td><td>' + (now ? r[2](b) : '…') + '</td><td>' + d + '</td></tr>';
    });
    return t + '</table>';
  }

  function sourcesNote(ev) {
    if (!ev || !ev.sources) return '';
    var used = ev.sources.filter(function (s) { return s.used; });
    var s = ev.summary || {};
    var txt = used.length ? used.map(function (u) { return esc(u.title) + (u.kind === 'lidar' ? ' (' + u.res_m + ' m lidar)' : ' (regional)'); }).join(', ') : 'none';
    var h = '<div class="tg-small tg-muted">Terrain: ' + txt + ' · lidar ' + n(s.lidar_pct) + ' % of length';
    if (s.coverage_pct != null && s.coverage_pct < 99.5) h += ' · <strong>no data for ' + n(100 - s.coverage_pct) + ' %</strong>';
    h += ' · sampled every ' + n(ev.spacing_m, 2) + ' m</div>';
    if (s.lidar_pct != null && s.lidar_pct < 50) {
      h += '<div class="tg-small tg-note">Mostly regional DEM (about 60 m data in Alaska): climb is fair, but grade and TSA at trail scale need lidar.</div>';
    }
    (ev.warnings || []).forEach(function (w) { h += '<div class="tg-small tg-error">' + esc(w) + '</div>'; });
    return h;
  }

  function profileOpts(ev, ghostEv) {
    var o = { legs: (ev.legs || []).map(function (L) { return { d_start: L.d_start, d_end: L.d_end, kind: L.kind, name: L.name }; }),
              contextSrc: (ev.sources || []).filter(function (s) { return s.kind === 'context'; }).map(function (s) { return s.index; }) };
    if (ghostEv && ghostEv.profile) o.ghost = TgProfile.fromEvaluation(ghostEv.profile);
    return o;
  }
  function drawProfile(ev, ghostEv) {
    if (!ev || !ev.profile) { app.profile.clear(); return; }
    app.profile.render(TgProfile.fromEvaluation(ev.profile), profileOpts(ev, ghostEv));
  }

  // ---- view mode -----------------------------------------------------------
  function showAlignment(f) {
    cur = f;
    var p = f.properties, h = '';
    if (p.trail) h += '<div class="tg-crumb">Trail <a href="#trail=' + esc(p.trail.slug) + '">' + esc(p.trail.name) + '</a> ›' + (p.parent ? ' <a href="#align=' + p.parent.id + '">' + esc(p.parent.name) + '</a> ›' : '') + '</div>';
    h += '<div class="tg-kind">Alignment · priority ' + p.priority + (p.parent ? ' · variant of <a href="#align=' + p.parent.id + '">' + esc(p.parent.name) + '</a>' : '') + '</div>';
    h += '<h2>' + esc(p.name) + '</h2>';
    h += '<div class="tg-muted">Project: <a href="#project=' + esc(p.project.slug) + '">' + esc(p.project.name) + '</a>' +
         (p.project.visibility !== 'public' ? ' <span class="tg-badge">' + esc(p.project.visibility) + '</span>' : '') +
         (p.trailhead ? ' · from ' + esc(p.trailhead) : '') + '</div>';
    if (p.can_edit) {
      h += '<div class="tg-actions"><button id="d-edit" class="tg-btn tg-primary">Edit alignment</button>' +
           '<button id="d-dup" class="tg-btn">Duplicate as variant</button>' +
           (p.legs && p.legs.length ? '<button id="d-refine" class="tg-btn" title="Propose modest tweaks that fit the lidar better: grade, corners, side slope, cut and fill, distance from this line">Refine…</button>' : '') +
           (p.legs && p.legs.length && window.TG_CONFIG && TG_CONFIG.signedIn ? ' <button id="d-factors" class="tg-btn" title="The refine tool\'s factors along this alignment on the lidar, with your refine settings, without a search">Factors along the line…</button>' : '') +
           '<button id="d-new" class="tg-btn">New alignment</button>' +
           '<button id="d-del" class="tg-btn tg-danger" title="Delete this alignment">Delete</button></div>';
    } else if (p.legs && p.legs.length && window.TG_CONFIG && TG_CONFIG.signedIn) {
      h += '<div class="tg-actions"><button id="d-factors" class="tg-btn" title="The refine tool\'s factors along this alignment on the lidar, with your refine settings">Factors along the line…</button></div>';
    }
    if (p.assembly) h += assemblyHtml(p);
    h += evalStatusHtml(p);
    if (p.headline) h += statsTable(p.headline);
    h += legsTableHtml(p);
    if (p.evaluation) h += sourcesNote(p.evaluation) + bandsHtml(p.evaluation);
    if (p.notes) h += '<p class="tg-desc tg-small">' + esc(p.notes) + '</p>';
    h += compareHtml(p);
    h += '<div class="tg-links"><a href="#" id="zoomto">Zoom to</a> · <a href="#" id="sideview" title="Look at it (nearly) horizontally, from the side, to see how steady the climb is">Side view</a> · ' +
         '<a href="/api/alignments/' + p.id + '.gpx" title="One track per leg, with waypoints where legs join; opens in GaiaGPS and the field map app">GPX</a> · ' +
         '<a href="/api/alignments/' + p.id + '.geojson" download="alignment-' + p.id + '.geojson">GeoJSON</a></div>';
    if (p.can_edit) h += '<div id="notes-box"></div>';
    h += '<div class="tg-sub">Profile <span id="profsrc" class="tg-muted"></span></div>';
    $('detail').innerHTML = h;
    $('profstats').innerHTML = p.evaluation ? '<div class="tg-keys">' + app.profile.legend(true) + '</div>' : '';
    wireView(f);
    hoverAlignments(document.querySelectorAll('#detail li.asm-from'), function (li) { return li.dataset.id; });
    var rc = $('d-reassemble');
    if (rc) rc.addEventListener('click', function () {
      api('GET', '/api/projects/' + encodeURIComponent(p.project.slug) + '.json').then(function (d) { assembleTool(d, p); })
        .catch(function (e) { alert(e.message); });
    });
    TgNotes.setProject(p.can_edit ? { slug: p.project.slug, name: p.project.name } : null, $('notes-box'), false);
    if (p.evaluation) drawProfile(p.evaluation);
    else app.quickProfile(f);
    if (p.evaluation_status === 'queued') pollSaved(p.id);
  }

  function evalStatusHtml(p) {
    if (p.evaluation_status === 'queued') return '<div class="tg-status">Evaluating against the DEM… <span class="tg-spin"></span></div>';
    if (p.evaluation_status === 'failed') return '<div class="tg-status tg-error">Evaluation failed: ' + esc(p.evaluation_error) +
      (p.can_edit ? ' <button id="d-eval" class="tg-btn">Try again</button>' : '') + '</div>';
    if (p.evaluation_status === 'none') return '<div class="tg-status">Not evaluated yet.' + (p.can_edit ? ' <button id="d-eval" class="tg-btn">Evaluate</button>' : '') + '</div>';
    return '';
  }

  function legsTableHtml(p) {
    if (!p.legs || !p.legs.length) return '<p class="tg-muted">No legs yet.</p>';
    var t = '<div class="tg-sub">Legs</div><div class="tg-scroll"><table class="tg-legs"><tr><th></th><th>Leg</th><th>km</th><th title="95th percentile of smoothed |grade|">p95 %</th><th title="Share of the leg with TSA under 45°">TSA&lt;45</th><th title="Construction crew-days from the rubric">Build d</th></tr>';
    p.legs.forEach(function (L, i) {
      var s = L.stats || {}, e = s.effort || {};
      var tu = s.tsa_under && s.tsa_under['45'];
      t += '<tr data-leg="' + i + '"><td><i class="tg-swatch" style="background:' + LEG_COLORS[L.kind] + '"></i></td>' +
           '<td>' + (i + 1) + '. ' + esc(L.name || (L.trail ? L.trail.name : LEG_LABELS[L.kind])) +
           ((L.name || L.trail) ? '<div class="tg-muted tg-small">' + LEG_LABELS[L.kind] + '</div>' : '') +
           (L.kind !== 'existing' && L.effort_factor !== 1 ? '<div class="tg-muted tg-small">effort × ' + L.effort_factor + '</div>' : '') + '</td>' +
           '<td class="tg-num">' + kmn(L.length_m) + '</td><td class="tg-num">' + (s.grade ? n(s.grade.p95) : '–') + '</td>' +
           '<td class="tg-num">' + (tu ? n(tu.pct) : '–') + '</td><td class="tg-num">' + (e.construct_days != null ? n(e.construct_days, 1) : '–') + '</td></tr>';
    });
    return t + '</table></div>';
  }

  function bandsHtml(ev) {
    var s = ev.summary || {}, h = '<details class="tg-more"><summary>Bands and effort detail</summary>';
    [['Grade (|smoothed|)', s.grade_bands, '%'], ['Side-slope', s.slope_bands, '%'], ['TSA', s.tsa_bands, '°']].forEach(function (b) {
      if (!b[1]) return;
      h += '<table class="tg-bands"><tr><th>' + b[0] + '</th><th>Share</th><th>Length</th><th>Longest run</th></tr>';
      b[1].forEach(function (r) {
        h += '<tr><td>' + r.min + (r.max == null ? '+' : '–' + r.max) + ' ' + b[2] + '</td><td>' + n(r.pct, 1) + ' %</td><td>' + n(r.length_m) + ' m</td><td>' + n(r.longest_m) + ' m</td></tr>';
      });
      h += '</table>';
    });
    var eff = [];
    (ev.legs || []).forEach(function (L) { if (L.effort) eff.push(L); });
    if (eff.length) {
      h += '<table class="tg-bands"><tr><th>Leg effort</th><th>Build</th><th>Maintain</th><th>Not assessed</th></tr>';
      eff.forEach(function (L) {
        h += '<tr><td>' + (L.index + 1) + '. ' + esc(L.name || LEG_LABELS[L.kind]) + '</td><td>' + n(L.effort.construct_days, 1) + ' d</td><td>' + n(L.effort.maintain_days, 2) + ' d</td><td>' + n(L.effort.unassessed_m) + ' m</td></tr>';
      });
      h += '</table><div class="tg-small tg-muted">Rubric: TRAIL_ANALYSIS.md §1.7, set per project. Existing-trail legs cost maintenance only.</div>';
    }
    return h + '</details>';
  }

  function compareHtml(p) {
    if (!p.siblings || !p.siblings.length) return '';
    var rows = [{ id: p.id, name: p.name, headline: p.headline, self: true }].concat(p.siblings.slice(0, 12));
    var t = '<div class="tg-sub">Compare within the project</div><div class="tg-scroll"><table class="tg-compare"><tr><th>Alignment</th><th>km</th><th>New km</th><th title="95th percentile |grade|, %">p95 %</th><th title="Share steeper than 18 %">&gt;18 %</th><th title="Share with TSA under 45°">TSA&lt;45</th><th title="Construction crew-days">Build d</th></tr>';
    rows.forEach(function (r) {
      var h = r.headline || {};
      t += '<tr' + (r.self ? ' class="tg-self"' : '') + '><td>' + (r.self ? esc(r.name) : '<a href="#align=' + r.id + '">' + esc(r.name) + '</a>') + '</td>' +
           '<td>' + kmn(h.length_m != null ? h.length_m : r.length_m) + '</td><td>' + kmn(h.build_m) + '</td><td>' + n(h.grade_p95) + '</td>' +
           '<td>' + n(h.steep_pct) + '</td><td>' + n(h.fall_line_pct) + '</td><td>' + n(h.construct_days) + '</td></tr>';
    });
    t += '</table></div>';
    if (p.siblings.length > 12) t += '<a class="tg-small" href="#project=' + esc(p.project.slug) + '">All ' + (p.siblings.length + 1) + ' alignments</a>';
    return t;
  }

  function wireView(f) {
    var p = f.properties;
    var z = $('zoomto'); if (z) z.addEventListener('click', function (e) { e.preventDefault(); app.fitTo(f.geometry); });
    var sv = $('sideview'); if (sv && f.geometry) sv.addEventListener('click', function (e) { e.preventDefault(); app.sideView(f.geometry); });
    Array.prototype.forEach.call(document.querySelectorAll('.tg-legs tr[data-leg]'), function (tr) {
      tr.addEventListener('mouseenter', function () { app.highlight(legFeature(p.legs[+tr.dataset.leg])); });
      tr.addEventListener('mouseleave', function () { app.highlight(null); });
      tr.addEventListener('click', function () { app.fitTo(legFeature(p.legs[+tr.dataset.leg]).geometry); });
    });
    var fa = $('d-factors'); if (fa) fa.addEventListener('click', function () { TgFactors.analyse(app, 'align', p.id, p.name); });
    if (!p.can_edit) return;
    $('d-edit').addEventListener('click', function () { startEdit(f); });
    $('d-dup').addEventListener('click', function () {
      api('POST', '/api/alignments/' + p.id + '/duplicate', {}).then(function (g) {
        app.refreshTiles(); app.select('align', g.properties.id, false);
      }).catch(function (e) { alert(e.message); });
    });
    $('d-new').addEventListener('click', function () { newAlignment(p.project.id); });
    var rf = $('d-refine'); if (rf) rf.addEventListener('click', function () { TgRefine.open(f, app); });
    $('d-del').addEventListener('click', function () {
      if (!confirm('Delete "' + p.name + '" and its legs? This cannot be undone.')) return;
      api('POST', '/api/alignments/' + p.id + '/delete').then(function (r) {
        app.refreshTiles(); app.select('project', r.project, false);
      }).catch(function (e) { alert(e.message); });
    });
    var ev = $('d-eval');
    if (ev) ev.addEventListener('click', function () {
      api('POST', '/api/alignments/' + p.id + '/evaluate').then(function () { app.select('align', p.id, false); });
    });
  }
  function legFeature(L) { return { type: 'Feature', properties: {}, geometry: { type: 'LineString', coordinates: L.coords } }; }

  function pollSaved(id) {
    clearTimeout(pollTimer);
    var tries = 0;
    (function tick() {
      pollTimer = setTimeout(function () {
        if (!cur || cur.properties.id !== id || editing) return;
        fetch('/api/alignments/' + id + '.geojson', { credentials: 'same-origin' }).then(function (r) { return r.json(); }).then(function (f) {
          if (!cur || cur.properties.id !== id || editing) return;
          if (f.properties.evaluation_status === 'queued' && ++tries < 150) { tick(); return; }
          // The Refine panel is open over this alignment: leave it be. Its
          // "Back to the alignment" fetches the alignment afresh (owner,
          // 2026-10-19: a new study's evaluation finished during Refine's
          // first terrain read and threw the panel back to the alignment).
          if (window.TgRefine && TgRefine.active()) return;
          showAlignment(f);
        });
      }, 2000);
    })();
  }

  // ---- project view --------------------------------------------------------
  var collapsed = {};          // per project slug: which sections are folded, for this page
  var projShown = null;        // the project panel now showing (a token), so a stale refresh does nothing
  function showProject(data, fit) {
    cur = null;
    var p = data.project, h = '';
    h += '<div class="tg-kind">Design project' + (p.visibility !== 'public' ? ' · <span class="tg-badge">' + esc(p.visibility) + '</span>' : '') + '</div>';
    h += '<h2>' + esc(p.name) + '</h2>';
    if (p.parents && p.parents.length) {
      h += '<div class="tg-muted">Part of ' + p.parents.map(function (q) { return '<a href="#project=' + esc(q.slug) + '">' + esc(q.name) + '</a>'; }).join(' › ') + '</div>';
    }
    if (p.description) h += '<p class="tg-desc tg-small">' + esc(p.description) + '</p>';
    if (p.can_edit) h += '<div class="tg-actions"><button id="d-new" class="tg-btn tg-primary">New alignment</button>' +
                         (data.sections.length ? ' <button id="d-assemble" class="tg-btn" title="One option per section, joined into one continuous alignment">Assemble a route…</button>' : '') + '</div>';
    else h += '<div class="tg-muted tg-small">Read-only view: anyone with this page\'s link can see and download it. Editing needs a sign-in with the trail_editors role and membership of the project.</div>';
    if (p.can_edit) h += '<div id="notes-box"></div>';
    if (p.children && p.children.length) {
      h += '<div class="tg-sub">Projects within this one</div><ul class="tg-list">' +
           p.children.map(function (q) { return '<li><a href="#project=' + esc(q.slug) + '">' + esc(q.name) + '</a></li>'; }).join('') + '</ul>';
    }
    // Few columns, so the tree fits the panel (owner, 2026-10-27); the rest
    // are in each row's tooltip and in the alignment's own panel.
    var cols = [['name', 'Alignment'], ['length_m', 'km'], ['climb_m', 'Climb m'], ['grade_p95', 'p95 grade %'], ['construct_days', 'Build days']];
    h += '<div class="tg-small tg-muted">' + data.alignments.length + ' alignments' + (data.sections.length ? ' in ' + data.sections.length + ' sections, in order along the route; each section\'s alignments are its options, variants under them' : '') +
         '. Click a column to sort within each section.</div>';
    h += '<div class="tg-scroll"><table class="tg-compare tg-tree" id="proj-table"><thead><tr>' +
         cols.map(function (c, i) { return '<th data-k="' + c[0] + '">' + c[1] + '</th>' + (i === 0 && p.can_edit ? '<th>Section</th>' : ''); }).join('') + '</tr></thead><tbody></tbody></table></div>';
    if (p.can_edit) h += '<div class="tg-actions"><input id="sec-name" type="text" placeholder="New section name"> <button id="sec-add" class="tg-btn">Add a section</button>' +
                         ' <span class="tg-small tg-muted">Sections are the parts of the route, in order. An existing trail becomes an option with "Add as an option…" on its panel.</span></div>';
    if (data.tracks && data.tracks.length) {
      h += '<div class="tg-sub">Scouting tracks</div><ul class="tg-list">' + data.tracks.map(function (t) {
        return '<li><a href="#track=' + t.id + '">' + esc(t.name) + '</a>' + (t.taken_at ? ' <span class="tg-muted">' + esc(t.taken_at.slice(0, 10)) + '</span>' : '') +
               ' <span class="tg-muted">' + kmn(t.length_m) + ' km</span></li>';
      }).join('') + '</ul>';
    }
    h += '<div class="tg-links"><a href="' + esc(p.gpx_url) + '" title="Every alignment as GPX tracks (preferred red, alternates blue, future grey), with waypoints at leg joints; for GaiaGPS or the field map app">GPX for the field</a> · ' +
         '<a href="' + esc(p.zip_url) + '" title="A zip with one GPX file per alignment, numbered in priority order, plus the combined file">GPX zip</a> · ' +
         '<a href="' + esc(p.geojson_url) + '" title="One feature per leg; the input for an offline map product (ogr2ogr -f MBTILES)">GeoJSON of legs</a></div>';
    $('detail').innerHTML = h;
    $('profstats').innerHTML = '';
    app.profile.clear();
    var sortK = null, sortDir = 1, ncol = cols.length + (p.can_edit ? 1 : 0);
    var folded = collapsed[p.slug] = collapsed[p.slug] || {};
    function val(a, k) { return k === 'name' ? a.name.toLowerCase() : k === 'length_m' ? a.length_m : (a.headline ? a.headline[k] : null); }
    function byCol(a, b) {
      var x = val(a, sortK), y = val(b, sortK);
      if (x == null) return 1; if (y == null) return -1;
      return (x > y ? 1 : x < y ? -1 : 0) * sortDir;
    }
    var secIds = {}; data.sections.forEach(function (q) { secIds[q.id] = true; });
    var groups = data.sections.map(function (q) { return { sec: q, rows: [] }; });
    var unsorted = { sec: null, rows: [] }, routes = { sec: null, routes: true, rows: [] };
    var byId = {}; data.alignments.forEach(function (a) { byId[a.id] = a; });
    function isRoute(a) {            // assembled, or a variant of one (a fine-tune), and in no section
      for (var x = a, k = 0; x && k < 50; x = byId[x.parent_id], k++) if (x.assembled) return !a.section_id;
      return false;
    }
    data.alignments.forEach(function (a) {
      var g = secIds[a.section_id] ? groups[data.sections.findIndex(function (q) { return q.id === a.section_id; })]
            : isRoute(a) ? routes : unsorted;
      g.rows.push(a);
    });
    if (routes.rows.length) groups.push(routes);
    if (unsorted.rows.length || !groups.length) groups.push(unsorted);
    function secSelect(a) {
      return '<select class="sec-pick" data-id="' + a.id + '"><option value="">Unsorted</option>' + data.sections.map(function (q) {
        return '<option value="' + q.id + '"' + (q.id === a.section_id ? ' selected' : '') + '>' + esc(q.name) + '</option>';
      }).join('') + '</select>';
    }
    function rowHtml(a, depth) {
      var hh = a.headline || {};
      var tip = 'New construction ' + kmn(hh.build_m) + ' km · steeper than 18 %: ' + n(hh.steep_pct) + ' % · TSA under 45°: ' + n(hh.fall_line_pct) +
                ' % · on lidar: ' + n(hh.lidar_pct) + ' %';
      return '<tr data-id="' + a.id + '" title="' + esc(tip) + '"><td style="padding-left:' + (6 + 16 * depth) + 'px">' + (depth ? '<span class="tg-muted">↳ </span>' : '') +
             '<a href="#align=' + a.id + '">' + esc(a.name) + '</a>' + (a.trail ? ' <span class="tg-badge" title="An existing trail: ' + esc(a.trail) + '">trail</span>' : '') +
             (a.evaluation_status === 'queued' ? ' <span class="tg-spin"></span>' : '') + '</td>' + (p.can_edit ? '<td>' + secSelect(a) + '</td>' : '') +
             '<td>' + kmn(a.length_m) + '</td><td>' + n(hh.climb_m) + '</td><td>' + n(hh.grade_p95) + '</td><td>' + n(hh.construct_days) + '</td></tr>';
    }
    function headHtml(g, i) {
      var key = g.sec ? g.sec.id : g.routes ? 'routes' : 'none', shut = !!folded[key];
      var h2 = '<tr class="sec-head" data-sec="' + key + '"><th colspan="' + ncol + '"><span class="sec-toggle">' + (shut ? '▸' : '▾') + ' ' +
               (g.sec ? (i + 1) + '. ' + esc(g.sec.name) : g.routes ? 'Whole routes (assembled)' : (data.sections.length ? 'Unsorted' : 'Alignments')) + '</span>' +
               ' <span class="tg-muted">' + g.rows.length + (g.rows.length === 1 ? ' alignment' : ' alignments') + '</span>';
      if (g.sec && p.can_edit) {
        h2 += ' <span class="sec-acts"><button class="tg-link" data-act="up" title="Earlier along the route">↑</button><button class="tg-link" data-act="down" title="Later along the route">↓</button>' +
              ' <button class="tg-link" data-act="rename">rename</button> <button class="tg-link" data-act="del">delete</button></span>';
      }
      if (g.sec && g.sec.notes) h2 += '<div class="tg-small tg-muted">' + esc(g.sec.notes) + '</div>';
      return h2 + '</th></tr>';
    }
    function fill() {
      var out = [];
      groups.forEach(function (g, i) {
        out.push(headHtml(g, i));
        if (folded[g.sec ? g.sec.id : g.routes ? 'routes' : 'none']) return;
        // Options are the alignments whose parent is not in this section;
        // each one's variants follow it, indented.
        var ids = {}, kids = {};
        g.rows.forEach(function (a) { ids[a.id] = true; });
        g.rows.forEach(function (a) { var k = ids[a.parent_id] ? a.parent_id : 'root'; (kids[k] = kids[k] || []).push(a); });
        (function walk(k, depth) {
          var list = (kids[k] || []).slice();
          if (sortK) list.sort(byCol);
          list.forEach(function (a) { out.push(rowHtml(a, depth)); walk(a.id, depth + 1); });
        })('root', 0);
      });
      document.querySelector('#proj-table tbody').innerHTML = out.join('');
      wire();
    }
    function reload() {
      api('GET', '/api/projects/' + encodeURIComponent(p.slug) + '.json').then(function (d) { showProject(d, false); })
        .catch(function (e) { alert(e.message); });
    }
    function wire() {
      hoverAlignments(document.querySelectorAll('#proj-table tbody tr[data-id]'), function (tr) { return tr.dataset.id; });
      Array.prototype.forEach.call(document.querySelectorAll('#proj-table .sec-toggle'), function (el) {
        el.addEventListener('click', function () { var k = el.closest('tr').dataset.sec; folded[k] = !folded[k]; fill(); });
      });
      Array.prototype.forEach.call(document.querySelectorAll('#proj-table .sec-pick'), function (sel) {
        sel.addEventListener('change', function () {
          api('POST', '/api/alignments/' + sel.dataset.id + '/section', { section_id: sel.value ? +sel.value : null }).then(reload)
            .catch(function (e) { alert(e.message); });
        });
      });
      Array.prototype.forEach.call(document.querySelectorAll('#proj-table .sec-acts button'), function (b) {
        b.addEventListener('click', function () {
          var tr = b.closest('tr'), id = tr.dataset.sec, q = data.sections.find(function (x) { return String(x.id) === id; });
          var act = b.dataset.act, acts = b.parentNode;
          if (act === 'up' || act === 'down') {
            api('POST', '/api/sections/' + id + '/save', { move: act === 'up' ? -1 : 1 }).then(reload).catch(function (e) { alert(e.message); });
          } else if (act === 'rename') {
            acts.innerHTML = '<input type="text" class="sec-rename" value="' + esc(q.name) + '"> <button class="tg-link" data-x="ok">save</button> <button class="tg-link" data-x="no">cancel</button>';
            acts.querySelector('[data-x=no]').addEventListener('click', fill);
            acts.querySelector('[data-x=ok]').addEventListener('click', function () {
              api('POST', '/api/sections/' + id + '/save', { name: acts.querySelector('input').value }).then(reload).catch(function (e) { alert(e.message); });
            });
          } else if (act === 'del') {
            acts.innerHTML = 'Delete this section? Its alignments stay, unsorted. <button class="tg-link" data-x="ok">delete</button> <button class="tg-link" data-x="no">keep</button>';
            acts.querySelector('[data-x=no]').addEventListener('click', fill);
            acts.querySelector('[data-x=ok]').addEventListener('click', function () {
              api('POST', '/api/sections/' + id + '/delete', {}).then(reload).catch(function (e) { alert(e.message); });
            });
          }
        });
      });
    }
    fill();
    // While an evaluation is queued (a new option, a save), look again in a
    // few seconds, as long as this panel is still the one showing.
    var shown = projShown = {};
    if (data.alignments.some(function (a) { return a.evaluation_status === 'queued'; })) {
      setTimeout(function () { if (projShown === shown && document.getElementById('proj-table')) reload(); }, 3000);
    }
    Array.prototype.forEach.call(document.querySelectorAll('#proj-table thead th[data-k]'), function (th) {
      th.addEventListener('click', function () { var k = th.dataset.k; sortDir = sortK === k ? -sortDir : 1; sortK = k; fill(); });
    });
    var sa = $('sec-add');
    if (sa) sa.addEventListener('click', function () {
      var nm = $('sec-name').value.trim();
      if (!nm) { $('sec-name').focus(); return; }
      api('POST', '/api/projects/' + encodeURIComponent(p.slug) + '/sections/new', { name: nm }).then(reload).catch(function (e) { alert(e.message); });
    });
    var n_ = $('d-new'); if (n_) n_.addEventListener('click', function () { newAlignment(p.id); });
    var as_ = $('d-assemble'); if (as_) as_.addEventListener('click', function () { assembleTool(data, null); });
    TgNotes.setProject(p.can_edit ? { slug: p.slug, name: p.name } : null, $('notes-box'), true);
    // Fit to the project unless the caller keeps the view: a link with its own
    // map= (the other kinds have always honoured it), or a save / delete that
    // reopens the panel over the place being worked on.
    if (fit !== false && p.bbox) app.fitTo(null, p.bbox);
  }

  // ---- new alignment ------------------------------------------------------
  function newAlignment(projectId) {
    api('GET', '/api/projects/').then(function (r) {
      r.projects = r.projects.filter(function (p) { return p.can_edit; });
      var h = '<div class="tg-kind">New alignment</div><h2>Start an alignment</h2><div class="tg-form">';
      h += '<div class="tg-field"><label>Name</label><input id="na-name" type="text" value="New alignment"></div>';
      h += '<div class="tg-field"><label>Project</label><select id="na-proj">' +
           r.projects.map(function (p) { return '<option value="' + p.id + '"' + (p.id === projectId ? ' selected' : '') + '>' + esc(p.name) + '</option>'; }).join('') +
           (r.can_create ? '<option value="">New project…</option>' : '') + '</select></div>';
      h += '<div class="tg-field" id="na-newproj" style="display:none"><label>New project name</label><input id="na-pname" type="text">' +
           '<div class="tg-help">Private: only you and the members you add in /admin/ can see it.</div></div>';
      h += '<div class="tg-actions"><button id="na-go" class="tg-btn tg-primary">Create and draw</button><button id="na-cancel" class="tg-btn">Cancel</button></div>';
      h += '<p class="tg-small tg-muted">You will start in draw mode: click to lay out new construction, or switch to "Follow trails" to start on an existing trail.</p></div>';
      $('panel').classList.add('open');
      $('detail').innerHTML = h; $('profstats').innerHTML = ''; app.profile.clear();
      var sel = $('na-proj');
      function toggle() { $('na-newproj').style.display = sel.value ? 'none' : ''; }
      sel.addEventListener('change', toggle); toggle();
      $('na-cancel').addEventListener('click', function () { app.clearSelection(); });
      $('na-go').addEventListener('click', function () {
        var body = { name: $('na-name').value };
        if (sel.value) body.project_id = +sel.value; else body.project_name = $('na-pname').value;
        api('POST', '/api/alignments/', body).then(function (f) {
          newEmpty = true;
          app.setSelected('align', f.properties.id);
          startEdit(f);
        }).catch(function (e) { alert(e.message); });
      });
    }).catch(function (e) { alert(e.message); });
  }

  // ---- refine study of an existing trail (owner, 2026-10-18) ----------------
  // One existing-trail leg along the trail, in a project: the one that
  // already follows the trail if there is one, any editable one, or a new
  // one. The refine tool then opens on it; "Off existing tread" weighs
  // leaving the trail (weight 0 ignores it).
  function trailStudy(tf) {
    var tp = tf.properties, already = tp.study_projects || [];
    api('GET', '/api/projects/').then(function (r) {
      var mine = r.projects.filter(function (p) { return p.can_edit; });
      var first = already.length ? already[0].id : null;
      var ids = {}; already.forEach(function (p) { ids[p.id] = true; });
      var h = '<div class="tg-kind">Refine study</div><h2>' + esc(tp.name) + '</h2><div class="tg-form">';
      h += '<p class="tg-small">A new alignment that follows this trail as one existing-trail leg, then the refine tool on it. A candidate you keep is saved as a reroute variant of it.</p>';
      h += '<div class="tg-field"><label>Name</label><input id="ts-name" type="text" value="' + esc(tp.name + ': refine study') + '"></div>';
      h += '<div class="tg-field"><label>Project</label><select id="ts-proj">' +
           mine.map(function (p) {
             return '<option value="' + p.id + '"' + (p.id === first ? ' selected' : '') + '>' + esc(p.name) + (ids[p.id] ? ' (already follows this trail)' : '') + '</option>';
           }).join('') + (r.can_create ? '<option value=""' + (!first && !mine.length ? ' selected' : '') + '>New project…</option>' : '') + '</select></div>';
      h += '<div class="tg-field" id="ts-newproj" style="display:none"><label>New project name</label><input id="ts-pname" type="text" value="' + esc(tp.name) + '">' +
           '<div class="tg-help">Private: only you and the members you add in /admin/ can see it.</div></div>';
      h += '<div class="tg-actions"><button id="ts-go" class="tg-btn tg-primary">Create and refine</button><button id="ts-cancel" class="tg-btn">Cancel</button></div>';
      h += '<div id="ts-msg" class="tg-small"></div></div>';
      $('panel').classList.add('open');
      $('detail').innerHTML = h; $('profstats').innerHTML = ''; app.profile.clear();
      var sel = $('ts-proj');
      function toggle() { $('ts-newproj').style.display = sel.value ? 'none' : ''; }
      sel.addEventListener('change', toggle); toggle();
      $('ts-cancel').addEventListener('click', function () { app.select('trail', tp.slug, false); });
      $('ts-go').addEventListener('click', function () {
        var body = { name: $('ts-name').value }, b = $('ts-go');
        if (sel.value) body.project_id = +sel.value; else body.project_name = $('ts-pname').value;
        b.disabled = true;
        api('POST', '/api/trails/' + encodeURIComponent(tp.slug) + '/study', body).then(function (f) {
          app.refreshTiles();
          app.setSelected('align', f.properties.id);
          app.setSelectedGeometry(f);
          showAlignment(f);
          TgRefine.open(f, app);
          if (f.properties.note) setTimeout(function () { alert(f.properties.note); }, 0);
        }).catch(function (e) { b.disabled = false; $('ts-msg').innerHTML = '<span class="tg-error">' + esc(e.message) + '</span>'; });
      });
    }).catch(function (e) { alert(e.message); });
  }

  // Hovering a row picks its alignment out on the map (owner, 2026-10-27).
  function hoverAlignments(els, idOf) {
    if (!app.highlightAlignment) return;
    Array.prototype.forEach.call(els, function (el) {
      el.addEventListener('mouseenter', function () { app.highlightAlignment(idOf(el)); });
      el.addEventListener('mouseleave', function () { app.highlightAlignment(null); });
    });
  }

  // ---- assembling one route from one option per section (owner, 2026-10-26) --
  function joinText(j) {
    var what = j.kind === 'leaves' ? 'leaves ' + esc(j.after) + ' ' + n(j.cut_prev_m) + ' m along it (the rest of it is dropped)'
             : j.kind === 'joins' ? 'joins ' + esc(j.before) + ' ' + n(j.cut_next_m) + ' m along it (its start is dropped)'
             : 'end to end';
    var gap = j.bridge_m ? ', a ' + n(j.bridge_m, 1) + ' m new-construction leg bridges the gap'
            : j.snapped_m ? ', joint moved ' + n(j.snapped_m, 1) + ' m to meet' : '';
    return esc(j.after) + ' → ' + esc(j.before) + ': ' + what + gap;
  }
  function assemblyHtml(p) {
    var A = p.assembly, h = '<div class="tg-sub">Assembled from</div><ol class="tg-list tg-small">';
    (A.pieces || []).forEach(function (pc) {
      h += '<li class="asm-from" data-id="' + pc.alignment_id + '">' + esc(pc.section || '') + ': <a href="#align=' + pc.alignment_id + '">' + esc(pc.alignment) + '</a>' +
           (pc.reversed ? ' (reversed)' : '') + (pc.kept_m && (pc.kept_m[0] > 0.5 || pc.kept_m[1] < pc.length_m - 0.5) ?
             ' <span class="tg-muted">' + kmn(pc.kept_m[1] - pc.kept_m[0]) + ' of ' + kmn(pc.length_m) + ' km</span>' : '') + '</li>';
    });
    h += '</ol>';
    if ((A.joins || []).length) h += '<ul class="tg-list tg-small tg-muted">' + A.joins.map(function (j) { return '<li>' + joinText(j) + '</li>'; }).join('') + '</ul>';
    if (p.can_edit) h += '<div class="tg-actions"><button id="d-reassemble" class="tg-btn" title="Choose other options; saved as a variant of this route">Change the choices…</button></div>';
    return h;
  }
  function assembleTool(data, from) {
    var p = data.project, asmSeq = 0, timer = null, last = null;
    var prev = {};                  // section id -> {alignment_id, reverse} from the route being changed
    if (from && from.assembly) from.assembly.choices.forEach(function (c) { prev[c.section_id] = c; });
    var bySec = {};
    data.alignments.forEach(function (a) { if (a.section_id) (bySec[a.section_id] = bySec[a.section_id] || []).push(a); });
    function optionList(sid) {      // each section's options, each followed by its variants
      var rows = bySec[sid] || [], ids = {}, kids = {}, out = [];
      rows.forEach(function (a) { ids[a.id] = true; });
      rows.forEach(function (a) { var k = ids[a.parent_id] ? a.parent_id : 'root'; (kids[k] = kids[k] || []).push(a); });
      (function walk(k, depth) { (kids[k] || []).forEach(function (a) { out.push({ a: a, depth: depth }); walk(a.id, depth + 1); }); })('root', 0);
      return out;
    }
    var h = '<div class="tg-kind">Assemble a route · ' + esc(p.name) + '</div><h2>' + (from ? 'Change the choices' : 'One option per section') + '</h2>';
    h += '<div class="tg-small tg-muted">Pick an option (or a variant) for each section, in order along the route. The pieces are joined as a first guess, and each join is reported below: ' +
         'a branch leaving the middle of the section before cuts it there; a gap up to 3 m is closed by moving the joint, a longer one bridged by new construction. ' +
         'Each piece runs the way that makes the joins shortest; set its direction to override. Nothing is saved until you save.</div>';
    h += '<table class="tg-metrics asm-pick">';
    data.sections.forEach(function (q, i) {
      var opts = optionList(q.id), was = prev[q.id];
      if (!opts.length) {
        h += '<tr><th>' + (i + 1) + '. ' + esc(q.name) + '</th><td class="tg-muted" colspan="2">no options yet</td></tr>';
        return;
      }
      var pick = was ? was.alignment_id : (from ? null : opts[0].a.id);
      h += '<tr><th>' + (i + 1) + '. ' + esc(q.name) + '</th><td><select class="asm-opt" data-sec="' + q.id + '"><option value="">(skip)</option>' +
           opts.map(function (o) {
             return '<option value="' + o.a.id + '"' + (o.a.id === pick ? ' selected' : '') + '>' + (o.depth ? '\u00a0\u00a0'.repeat(o.depth) + '↳ ' : '') +
                    esc(o.a.name) + ' (' + kmn(o.a.length_m) + ' km)</option>';
           }).join('') + '</select></td><td><select class="asm-rev" data-sec="' + q.id + '" title="Which way this piece runs">' +
           ['auto', 'as drawn', 'reversed'].map(function (t, k) {
             var v = [null, false, true][k], sel = was ? was.reverse === v : k === 0;
             return '<option value="' + k + '"' + (sel ? ' selected' : '') + '>' + t + '</option>';
           }).join('') + '</select></td></tr>';
    });
    h += '</table>';
    h += '<div id="asm-joins" class="tg-small"></div><div id="asm-eval" class="tg-small"></div><div id="asm-stats"></div>';
    h += '<div class="tg-field"><label>Name</label><input id="asm-name" type="text" value="' + esc(from ? from.name + ' (re-assembled)' : p.name + ': whole route') + '"></div>';
    h += '<div class="tg-actions"><button id="asm-save" class="tg-btn tg-primary" disabled>Save as an alignment</button> <button id="asm-cancel" class="tg-btn">Cancel</button></div>';
    h += '<div class="tg-small tg-muted">Saved, it is an ordinary alignment: edit it, refine it, export it. It keeps which option it took for each section.' + (from ? ' It is saved as a variant of ' + esc(from.name) + '.' : '') + '</div>';
    h += '<div class="tg-sub">Profile</div>';
    $('panel').classList.add('open');
    $('detail').innerHTML = h; $('profstats').innerHTML = ''; app.profile.clear();
    function choices() {
      var out = [];
      Array.prototype.forEach.call(document.querySelectorAll('.asm-opt'), function (sel) {
        if (!sel.value) return;
        var r = document.querySelector('.asm-rev[data-sec="' + sel.dataset.sec + '"]');
        out.push({ section_id: +sel.dataset.sec, alignment_id: +sel.value, reverse: [null, false, true][+r.value] });
      });
      return out;
    }
    function preview() {
      var my = ++asmSeq, ch = choices();
      $('asm-save').disabled = true;
      if (!ch.length) { $('asm-joins').innerHTML = '<span class="tg-muted">Choose at least one option.</span>'; app.setSelectedGeometry(null); app.profile.clear(); return; }
      $('asm-joins').innerHTML = 'Joining… <span class="tg-spin"></span>';
      api('POST', '/api/projects/' + encodeURIComponent(p.slug) + '/assemble/preview', { choices: ch }).then(function (r) {
        if (my !== asmSeq) return;
        last = r;
        var R = r.report;
        $('asm-joins').innerHTML = '<div class="tg-sub">Joins</div>' + (R.joins.length ? '<ul class="tg-list">' + R.joins.map(function (j) { return '<li>' + joinText(j) + '</li>'; }).join('') + '</ul>' : '<span class="tg-muted">One piece: no joins.</span>') +
          '<div class="tg-muted">' + R.pieces.map(function (pc) { return esc(pc.alignment) + (pc.reversed ? ' (reversed)' : ''); }).join(' → ') + ' · ' + kmn(R.length_m) + ' km</div>';
        var geom = { type: 'MultiLineString', coordinates: r.legs.map(function (L) { return L.coords; }) };
        app.setSelectedGeometry({ type: 'Feature', properties: {}, geometry: geom });
        $('asm-save').disabled = false;
        $('asm-eval').innerHTML = 'Evaluating the route… <span class="tg-spin"></span>';
        return api('POST', '/api/evaluate/', { legs: r.legs, project_id: p.id, seq: my }).then(function (e) {
          return e.status === 'done' ? e : pollEval(e.task, function () { return my === asmSeq; });
        }).then(function (e) {
          if (my !== asmSeq) return;
          if (!e.ok) throw new Error(e.error || 'evaluation failed');
          $('asm-eval').innerHTML = sourcesNote(e.result);
          $('asm-stats').innerHTML = statsTable(e.result.headline);
          drawProfile(e.result);
          $('profstats').innerHTML = '<div class="tg-keys">' + app.profile.legend(true) + '</div>';
        });
      }).catch(function (e) {
        if (my !== asmSeq || e.message === 'superseded') return;
        $('asm-joins').innerHTML = '<span class="tg-error">' + esc(e.message) + '</span>';
        $('asm-eval').innerHTML = ''; $('asm-stats').innerHTML = '';
        app.setSelectedGeometry(null);
      });
    }
    function soon() { clearTimeout(timer); timer = setTimeout(preview, 300); }
    hoverAlignments(document.querySelectorAll('.asm-pick tr'), function (tr) {
      var sel = tr.querySelector('.asm-opt');
      return sel && sel.value ? sel.value : null;
    });
    Array.prototype.forEach.call(document.querySelectorAll('.asm-opt, .asm-rev'), function (el) { el.addEventListener('change', soon); });
    Array.prototype.forEach.call(document.querySelectorAll('.asm-opt'), function (el) {
      el.addEventListener('change', function () { if (app.highlightAlignment) app.highlightAlignment(el.value || null); });
    });
    $('asm-cancel').addEventListener('click', function () { asmSeq++; app.setSelectedGeometry(null); if (from) app.select('align', from.id, false); else app.select('project', p.slug, false); });
    $('asm-save').addEventListener('click', function () {
      var b = $('asm-save'); b.disabled = true;
      var body = { choices: choices(), name: $('asm-name').value };
      if (from) body.parent_id = from.id;
      api('POST', '/api/projects/' + encodeURIComponent(p.slug) + '/assemble', body).then(function (f) {
        asmSeq++;
        app.refreshTiles();
        app.select('align', f.properties.id, false);
      }).catch(function (e) { b.disabled = false; $('asm-joins').insertAdjacentHTML('afterbegin', '<div class="tg-error">' + esc(e.message) + '</div>'); });
    });
    preview();
    if (data.project.bbox) app.fitTo(null, data.project.bbox);
  }
  function pollEval(task, current) {
    return new Promise(function (resolve, reject) {
      var t0 = Date.now();
      (function tick() {
        if (!current()) return reject(new Error('superseded'));
        fetch('/api/evaluate/' + task + '/', { credentials: 'same-origin' }).then(function (r) {
          if (r.status === 200) return r.json().then(resolve);
          if (Date.now() - t0 > 120000) return reject(new Error('the evaluation is taking too long; is the worker running?'));
          setTimeout(tick, 500);
        }, reject);
      })();
    });
  }

  // ---- an existing trail as a section's option (owner, 2026-10-26) ----------
  // The same alignment a refine study makes (one existing-trail leg along the
  // trail), put in a section of an editable project; the project opens.
  function trailOption(tf) {
    var tp = tf.properties;
    api('GET', '/api/projects/').then(function (r) {
      var mine = r.projects.filter(function (p) { return p.can_edit; });
      if (!mine.length) { alert('You have no project you can edit.'); return; }
      var h = '<div class="tg-kind">Existing trail as an option</div><h2>' + esc(tp.name) + '</h2><div class="tg-form">';
      h += '<p class="tg-small">An alignment that follows this trail as one existing-trail leg, as an option for a section of a project\'s route.</p>';
      h += '<div class="tg-field"><label>Name</label><input id="to-name" type="text" value="' + esc(tp.name) + '"></div>';
      h += '<div class="tg-field"><label>Project</label><select id="to-proj">' + mine.map(function (p) {
        return '<option value="' + p.id + '" data-slug="' + esc(p.slug) + '">' + esc(p.name) + '</option>';
      }).join('') + '</select></div>';
      h += '<div class="tg-field"><label>Section</label><select id="to-sec"></select></div>';
      h += '<div class="tg-actions"><button id="to-go" class="tg-btn tg-primary">Add</button><button id="to-cancel" class="tg-btn">Cancel</button></div>';
      h += '<div id="to-msg" class="tg-small"></div></div>';
      $('panel').classList.add('open');
      $('detail').innerHTML = h; $('profstats').innerHTML = ''; app.profile.clear();
      var ps = $('to-proj'), ss = $('to-sec');
      function sections() {
        var slug = ps.options[ps.selectedIndex].dataset.slug;
        ss.innerHTML = '<option value="">Unsorted</option>';
        api('GET', '/api/projects/' + encodeURIComponent(slug) + '.json').then(function (d) {
          ss.innerHTML = '<option value="">Unsorted</option>' + d.sections.map(function (q, i) {
            return '<option value="' + q.id + '"' + (i === 0 ? ' selected' : '') + '>' + (i + 1) + '. ' + esc(q.name) + '</option>';
          }).join('');
        });
      }
      ps.addEventListener('change', sections); sections();
      $('to-cancel').addEventListener('click', function () { app.select('trail', tp.slug, false); });
      $('to-go').addEventListener('click', function () {
        var b = $('to-go'), slug = ps.options[ps.selectedIndex].dataset.slug;
        var body = { name: $('to-name').value, project_id: +ps.value, section_id: ss.value ? +ss.value : null };
        b.disabled = true;
        api('POST', '/api/trails/' + encodeURIComponent(tp.slug) + '/study', body).then(function (f) {
          // Evaluated, so the section compares it with the other options.
          return api('POST', '/api/alignments/' + f.properties.id + '/evaluate', {}).then(function () { return f; });
        }).then(function (f) {
          app.refreshTiles();
          app.select('project', slug, false);
          if (f.properties.note) setTimeout(function () { alert(f.properties.note); }, 0);
        }).catch(function (e) { b.disabled = false; $('to-msg').innerHTML = '<span class="tg-error">' + esc(e.message) + '</span>'; });
      });
    }).catch(function (e) { alert(e.message); });
  }

  // ---- edit mode ------------------------------------------------------------
  var HELP = {
    edit: 'Drag a point to move it; click or drag a small white handle to add a point; double-click (or right-click) a point to delete it. Points snap to trails; hold Shift to place freely. Click a leg to pick it.',
    draw: 'Click to extend the end of the line with new construction. Double-click, Enter or Esc to finish.',
    follow: 'Click a point on an existing trail: the path along the trail network from the end of the line is added as an existing-trail leg.',
    split: 'Click a point inside a leg to split it in two there, so the two parts can be different build efforts.'
  };

  function startEdit(f) {
    cur = f; editing = true; dirty = false; live = null;
    $('panel').classList.add('tg-wide');          // room for Saved / Now / Change
    app.highlight(null); app.setSelectedGeometry(null);
    if (!ed) ed = TgEditor.create(app.map, {
      beforeLayer: 'cursor',
      onChange: function () { dirty = true; renderEditPanel(); scheduleEval(); },
      onMode: function () { renderToolbar(); },
      onSelectLeg: function (j) { highlightLegRow(j); },
      onMessage: function (t, err) { var m = $('ed-msg'); if (m) { m.textContent = t; m.className = 'tg-small ' + (err ? 'tg-error' : 'tg-muted'); } },
      onLayers: function () { app.rescaleLines(); }
    });
    renderEditShell();
    ed.start(f.properties.legs);
    dirty = false;
    if (f.properties.legs.length) scheduleEval(0);
  }

  function renderEditShell() {
    var p = cur.properties, h = '';
    h += '<div class="tg-kind">Editing alignment · ' + esc(p.project.name) + '</div>';
    h += '<input id="ed-name" class="tg-title-input" type="text" value="' + esc(p.name) + '">';
    h += '<div class="tg-toolbar" id="ed-tools"></div>';
    h += '<div id="ed-help" class="tg-small tg-muted"></div><div id="ed-msg" class="tg-small"></div>';
    h += '<div class="tg-actions"><button id="ed-save" class="tg-btn tg-primary">Save</button>' +
         (p.legs.length ? '<button id="ed-variant" class="tg-btn" title="Keep the saved alignment and save this as a new variant beside it">Save as variant</button>' : '') +
         '<button id="ed-cancel" class="tg-btn">Cancel</button></div>';
    h += '<div id="ed-eval" class="tg-small tg-muted"></div>';
    h += '<div class="tg-sub">Tradeoffs</div><div id="ed-trade"></div>';
    h += '<div class="tg-sub">Legs</div><div id="ed-legs"></div>';
    h += '<div class="tg-sub">Profile <span class="tg-muted tg-small">(saved version in grey)</span></div>';
    $('detail').innerHTML = h;
    $('profstats').innerHTML = '<div class="tg-keys">' + app.profile.legend(true) + '</div>';
    $('ed-save').addEventListener('click', function () { save(false); });
    if ($('ed-variant')) $('ed-variant').addEventListener('click', function () { save(true); });
    $('ed-cancel').addEventListener('click', cancel);
    $('ed-name').addEventListener('input', function () { dirty = true; });
    renderToolbar();
    renderEditPanel();
  }

  function renderToolbar() {
    var el = $('ed-tools'); if (!el || !ed) return;
    var m = ed.mode();
    var tools = [['edit', 'Edit points'], ['draw', 'Extend: draw'], ['follow', 'Extend: follow trails'], ['split', 'Split leg']];
    el.innerHTML = tools.map(function (t) { return '<button class="tg-btn tg-tool' + (m === t[0] ? ' tg-on' : '') + '" data-m="' + t[0] + '">' + t[1] + '</button>'; }).join('') +
      '<span class="tg-sep"></span><button class="tg-btn" data-a="undo" title="Ctrl/Cmd-Z"' + (ed.canUndo() ? '' : ' disabled') + '>Undo</button>' +
      '<button class="tg-btn" data-a="redo"' + (ed.canRedo() ? '' : ' disabled') + '>Redo</button>' +
      '<button class="tg-btn" data-a="reverse" title="Reverse the direction of the whole line">Reverse</button>' +
      '<button class="tg-btn" data-a="fit">Fit</button>';
    Array.prototype.forEach.call(el.querySelectorAll('button[data-m]'), function (b) { b.addEventListener('click', function () { ed.setMode(b.dataset.m); }); });
    Array.prototype.forEach.call(el.querySelectorAll('button[data-a]'), function (b) {
      b.addEventListener('click', function () { var a = b.dataset.a; if (a === 'fit') ed.fitBounds(); else ed[a](); });
    });
    var help = $('ed-help'); if (help) help.textContent = HELP[m];
  }

  function renderEditPanel() {
    renderToolbar();
    var legs = ed ? ed.legs() : [];
    var liveLegs = live && live.legs ? live.legs : [];
    var h = '';
    if (!legs.length) h = '<p class="tg-muted tg-small">No legs yet: draw or follow a trail to start.</p>';
    legs.forEach(function (L, i) {
      var s = liveLegs[i] || {}, e = s.effort || {};
      var tu = s.tsa_under && s.tsa_under['45'];
      h += '<div class="tg-legrow" data-leg="' + i + '">' +
        '<div class="tg-legrow-top"><i class="tg-swatch" style="background:' + LEG_COLORS[L.kind] + '"></i><strong>' + (i + 1) + '</strong>' +
        '<input class="tg-legname" type="text" placeholder="' + esc(L.trail_name || LEG_LABELS[L.kind]) + '" value="' + esc(L.name) + '">' +
        '<select class="tg-legkind">' + KINDS.map(function (k) { return '<option value="' + k + '"' + (k === L.kind ? ' selected' : '') + '>' + LEG_LABELS[k] + '</option>'; }).join('') + '</select></div>' +
        '<div class="tg-legrow-stats tg-small">' + (s.length_m != null ? km(s.length_m) : '') +
        (s.grade ? ' · p95 grade ' + n(s.grade.p95) + ' %' : '') + (tu ? ' · TSA&lt;45° ' + n(tu.pct) + ' %' : '') +
        (L.kind !== 'existing' && e.construct_days != null ? ' · build ' + n(e.construct_days, 1) + ' d' : '') + '</div>' +
        '<div class="tg-legrow-tools tg-small">' +
        (L.kind !== 'existing' ? '<label title="Multiplier on the rubric\'s construction days">effort × <input class="tg-legfactor" type="number" min="0" max="100" step="0.1" value="' + L.effort_factor + '"></label>' : '') +
        (i < legs.length - 1 ? ' <button class="tg-link" data-act="merge">merge with next</button>' : '') +
        ((i === 0 || i === legs.length - 1) && legs.length > 1 ? ' <button class="tg-link" data-act="remove">remove</button>' : '') +
        '</div></div>';
    });
    var box = $('ed-legs'); if (!box) return;
    box.innerHTML = h;
    Array.prototype.forEach.call(box.querySelectorAll('.tg-legrow'), function (row) {
      var i = +row.dataset.leg;
      row.querySelector('.tg-legkind').addEventListener('change', function (e) { ed.setLeg(i, { kind: e.target.value }); });
      row.querySelector('.tg-legname').addEventListener('change', function (e) { ed.setLeg(i, { name: e.target.value }); });
      var fct = row.querySelector('.tg-legfactor');
      if (fct) fct.addEventListener('change', function (e) { var v = parseFloat(e.target.value); if (isFinite(v) && v >= 0) ed.setLeg(i, { effort_factor: v }); });
      Array.prototype.forEach.call(row.querySelectorAll('button[data-act]'), function (b) {
        b.addEventListener('click', function () { if (b.dataset.act === 'merge') ed.mergeWithNext(i); else ed.removeEndLeg(i); });
      });
      row.addEventListener('mouseenter', function () { ed.selectLeg(i); });
    });
    $('ed-trade').innerHTML = tradeoffTable(cur.properties.headline, live ? live.headline : null);
  }
  function highlightLegRow(j) {
    Array.prototype.forEach.call(document.querySelectorAll('.tg-legrow'), function (r) { r.classList.toggle('tg-sel', +r.dataset.leg === j); });
    var r = document.querySelector('.tg-legrow[data-leg="' + j + '"]'); if (r) r.scrollIntoView({ block: 'nearest' });
  }

  function scheduleEval(delay) {
    clearTimeout(evalTimer);
    evalTimer = setTimeout(runLiveEval, delay == null ? 600 : delay);
  }
  function runLiveEval() {
    var legs = ed.legs();
    var status = $('ed-eval');
    if (!legs.length) { live = null; if (status) status.textContent = ''; app.profile.clear(); renderEditPanel(); return; }
    var my = ++seq;
    if (status) status.innerHTML = 'Evaluating the edit… <span class="tg-spin"></span>';
    api('POST', '/api/evaluate/', { legs: legs, project_id: cur.properties.project.id, seq: my }).then(function (r) {
      return r.status === 'done' ? r : poll(r.task, my);
    }).then(function (r) {
      if (my !== seq || !editing) return;
      if (!r.ok) throw new Error(r.error || 'evaluation failed');
      live = r.result;
      if (status) status.innerHTML = sourcesNote(live);
      drawProfile(live, cur.properties.evaluation);
      renderEditPanel();
    }).catch(function (e) { if (my === seq && status) status.innerHTML = '<span class="tg-error">' + esc(e.message) + '</span>'; });
  }
  function poll(task, my) {
    return new Promise(function (resolve, reject) {
      var t0 = Date.now();
      (function tick() {
        if (my !== seq) return reject(new Error('superseded'));
        fetch('/api/evaluate/' + task + '/', { credentials: 'same-origin' }).then(function (r) {
          if (r.status === 200) return r.json().then(resolve);
          if (Date.now() - t0 > 90000) return reject(new Error('the evaluation is taking too long; is the worker running?'));
          setTimeout(tick, 400);
        }, reject);
      })();
    });
  }

  function save(asVariant) {
    var legs = ed.legs();
    if (!legs.length) { alert('Draw at least one leg first.'); return; }
    var p = cur.properties;
    var body = { legs: legs, name: $('ed-name').value };
    var go = asVariant
      ? api('POST', '/api/alignments/' + p.id + '/duplicate', { name: body.name + ' (variant)' }).then(function (g) {
          body.name = g.properties.name;
          return api('POST', '/api/alignments/' + g.properties.id + '/save', body);
        })
      : api('POST', '/api/alignments/' + p.id + '/save', body);
    $('ed-save').disabled = true;
    go.then(function (f) {
      stopEdit();
      app.refreshTiles();
      app.select('align', f.properties.id, false);
    }).catch(function (e) { $('ed-save').disabled = false; alert('Could not save: ' + e.message); });
  }
  function cancel() {
    if (dirty && !confirm('Discard your changes to this alignment?')) return;
    var p = cur.properties, wasNew = newEmpty && !p.legs.length;
    stopEdit();
    if (wasNew) {
      api('POST', '/api/alignments/' + p.id + '/delete').then(function (r) { app.select('project', r.project, false); });
    } else {
      app.select('align', p.id, false);
    }
  }
  function stopEdit() {
    editing = false; newEmpty = false; dirty = false; live = null; seq++;
    $('panel').classList.remove('tg-wide');
    clearTimeout(evalTimer);
    if (ed) ed.stop();
  }

  window.addEventListener('beforeunload', function (e) { if (editing && dirty) { e.preventDefault(); e.returnValue = ''; } });

  return {
    init: function (a) { app = a; },
    showAlignment: showAlignment,
    showProject: showProject,
    newAlignment: newAlignment,
    trailStudy: trailStudy,
    trailOption: trailOption,
    assembleTool: assembleTool,
    editing: function () { return editing; },
    leave: function () { if (window.TgRefine) TgRefine.close(); if (editing) { if (dirty && !confirm('Discard your changes to this alignment?')) return false; stopEdit(); } return true; },
    onStyleReload: function () { if (ed && editing) ed.reattach(); if (window.TgRefine) TgRefine.onStyleReload(); if (window.TgNotes) TgNotes.onStyleReload(); }
  };
})();
