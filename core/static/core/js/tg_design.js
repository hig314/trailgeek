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
    h += '<div class="tg-kind">Alignment · priority ' + p.priority + (p.parent ? ' · variant of <a href="#align=' + p.parent.id + '">' + esc(p.parent.name) + '</a>' : '') + '</div>';
    h += '<h2>' + esc(p.name) + '</h2>';
    h += '<div class="tg-muted">Project: <a href="#project=' + esc(p.project.slug) + '">' + esc(p.project.name) + '</a>' +
         (p.project.visibility !== 'public' ? ' <span class="tg-badge">' + esc(p.project.visibility) + '</span>' : '') +
         (p.trailhead ? ' · from ' + esc(p.trailhead) : '') + '</div>';
    if (p.can_edit) {
      h += '<div class="tg-actions"><button id="d-edit" class="tg-btn tg-primary">Edit alignment</button>' +
           '<button id="d-dup" class="tg-btn">Duplicate as variant</button>' +
           '<button id="d-new" class="tg-btn">New alignment</button>' +
           '<button id="d-del" class="tg-btn tg-danger" title="Delete this alignment">Delete</button></div>';
    }
    h += evalStatusHtml(p);
    if (p.headline) h += statsTable(p.headline);
    h += legsTableHtml(p);
    if (p.evaluation) h += sourcesNote(p.evaluation) + bandsHtml(p.evaluation);
    if (p.notes) h += '<p class="tg-desc tg-small">' + esc(p.notes) + '</p>';
    h += compareHtml(p);
    h += '<div class="tg-links"><a href="#" id="zoomto">Zoom to</a> · <a href="#" id="sideview" title="Look at it (nearly) horizontally, from the side, to see how steady the climb is">Side view</a> · ' +
         '<a href="/api/alignments/' + p.id + '.geojson" download="alignment-' + p.id + '.geojson">GeoJSON</a></div>';
    h += '<div class="tg-sub">Profile <span id="profsrc" class="tg-muted"></span></div>';
    $('detail').innerHTML = h;
    $('profstats').innerHTML = p.evaluation ? '<div class="tg-keys">' + app.profile.legend(true) + '</div>' : '';
    wireView(f);
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
    if (!p.can_edit) return;
    $('d-edit').addEventListener('click', function () { startEdit(f); });
    $('d-dup').addEventListener('click', function () {
      api('POST', '/api/alignments/' + p.id + '/duplicate', {}).then(function (g) {
        app.refreshTiles(); app.select('align', g.properties.id, false);
      }).catch(function (e) { alert(e.message); });
    });
    $('d-new').addEventListener('click', function () { newAlignment(p.project.id); });
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
          showAlignment(f);
        });
      }, 2000);
    })();
  }

  // ---- project view --------------------------------------------------------
  function showProject(data) {
    cur = null;
    var p = data.project, h = '';
    h += '<div class="tg-kind">Design project' + (p.visibility !== 'public' ? ' · <span class="tg-badge">' + esc(p.visibility) + '</span>' : '') + '</div>';
    h += '<h2>' + esc(p.name) + '</h2>';
    if (p.description) h += '<p class="tg-desc tg-small">' + esc(p.description) + '</p>';
    if (p.can_edit) h += '<div class="tg-actions"><button id="d-new" class="tg-btn tg-primary">New alignment</button></div>';
    var cols = [['name', 'Alignment'], ['length_m', 'km'], ['build_m', 'New km'], ['climb_m', 'Climb m'], ['grade_p95', 'p95 grade %'],
                ['steep_pct', '> 18 %'], ['fall_line_pct', 'TSA < 45° %'], ['construct_days', 'Build days'], ['lidar_pct', 'Lidar %']];
    h += '<div class="tg-small tg-muted">' + data.alignments.length + ' alignments. Click a column to sort; hover a row to find it on the map.</div>';
    h += '<div class="tg-scroll"><table class="tg-compare" id="proj-table"><thead><tr>' +
         cols.map(function (c) { return '<th data-k="' + c[0] + '">' + c[1] + '</th>'; }).join('') + '</tr></thead><tbody></tbody></table></div>';
    $('detail').innerHTML = h;
    $('profstats').innerHTML = '';
    app.profile.clear();
    var sortK = null, sortDir = 1;
    function val(a, k) { return k === 'name' ? a.name.toLowerCase() : k === 'length_m' ? a.length_m : (a.headline ? a.headline[k] : null); }
    function fill() {
      var rows = data.alignments.slice();
      if (sortK) rows.sort(function (a, b) {
        var x = val(a, sortK), y = val(b, sortK);
        if (x == null) return 1; if (y == null) return -1;
        return (x > y ? 1 : x < y ? -1 : 0) * sortDir;
      });
      document.querySelector('#proj-table tbody').innerHTML = rows.map(function (a) {
        var hh = a.headline || {};
        return '<tr data-id="' + a.id + '"><td><a href="#align=' + a.id + '">' + esc(a.name) + '</a>' +
               (a.evaluation_status === 'queued' ? ' <span class="tg-spin"></span>' : '') + '</td><td>' + kmn(a.length_m) + '</td><td>' + kmn(hh.build_m) +
               '</td><td>' + n(hh.climb_m) + '</td><td>' + n(hh.grade_p95) + '</td><td>' + n(hh.steep_pct) + '</td><td>' + n(hh.fall_line_pct) +
               '</td><td>' + n(hh.construct_days) + '</td><td>' + n(hh.lidar_pct) + '</td></tr>';
      }).join('');
    }
    fill();
    Array.prototype.forEach.call(document.querySelectorAll('#proj-table th'), function (th) {
      th.addEventListener('click', function () { var k = th.dataset.k; sortDir = sortK === k ? -sortDir : 1; sortK = k; fill(); });
    });
    var n_ = $('d-new'); if (n_) n_.addEventListener('click', function () { newAlignment(p.id); });
    if (p.bbox) app.fitTo(null, p.bbox);
  }

  // ---- new alignment ------------------------------------------------------
  function newAlignment(projectId) {
    api('GET', '/api/projects/').then(function (r) {
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

  // ---- edit mode ------------------------------------------------------------
  var HELP = {
    edit: 'Drag a point to move it; drag a small white handle to add a point; right-click (or Alt-click) a point to delete it. Points snap to trails; hold Shift to place freely. Click a leg to pick it.',
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
    editing: function () { return editing; },
    leave: function () { if (editing) { if (dirty && !confirm('Discard your changes to this alignment?')) return false; stopEdit(); } return true; },
    onStyleReload: function () { if (ed && editing) ed.reattach(); }
  };
})();
