/* tg_notes.js — project notes: points on the map, each with a note.
 * Global `window.TgNotes`; map.js calls TgNotes.init(app) once.
 *
 * The notes of the project being looked at (its panel, or one of its
 * alignments) show as amber points, for those who can edit the project
 * only (core/notes_views.py). Click a point to read, edit, move or delete
 * its note; "Add a note" in the panel, then click the map to place one.
 *
 *   TgNotes.setProject({slug, name} | null, boxEl, withList)
 *   TgNotes.claims(e)    // map.js: is this click or hover the notes'?
 */
window.TgNotes = (function () {
  'use strict';

  var EMPTY = { type: 'FeatureCollection', features: [] };
  var app = null, map = null;
  var proj = null;            // {slug, name} whose notes are on the map
  var data = EMPTY;
  var box = null, withList = false;
  var placing = null;         // {id: note id to move, or null for a new note}
  var popup = null;
  var loadSeq = 0;

  function esc(t) { return app.esc(t); }
  function csrf() { var m = document.querySelector('meta[name=csrf-token]'); return m ? m.content : ''; }
  function api(method, url, body) {
    return fetch(url, { method: method, credentials: 'same-origin',
                        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf() },
                        body: body ? JSON.stringify(body) : undefined })
      .then(function (r) {
        if (r.redirected && /\/accounts\/login\//.test(r.url)) throw new Error('You have been signed out. Sign in again (in another tab, to keep this one) and retry.');
        return r.json().catch(function () { return {}; }).then(function (j) {
          if (!r.ok) throw new Error(j.error || (r.status === 403 ? 'Not allowed.' : 'Error ' + r.status));
          return j;
        });
      });
  }

  function addLayers() {
    if (!map.getSource('notes')) map.addSource('notes', { type: 'geojson', data: data });
    if (!map.getLayer('notes-pt')) map.addLayer({ id: 'notes-pt', type: 'circle', source: 'notes', paint: {
      'circle-radius': 7, 'circle-color': '#f2a900', 'circle-stroke-color': '#1f2a1f', 'circle-stroke-width': 2,
      'circle-pitch-scale': 'viewport', 'circle-pitch-alignment': 'viewport' } });
  }
  function setData(d) {
    data = d;
    var s = map.getSource('notes'); if (s) s.setData(data);
    renderBox();
  }
  function noteById(id) { return data.features.filter(function (f) { return f.properties.id === id; })[0]; }

  function setProject(p, el, list) {
    closePopup(); stopPlacing();
    box = el || null; withList = !!list;
    if (!p) { proj = null; setData(EMPTY); return; }
    proj = p;
    var my = ++loadSeq;
    renderBox();
    api('GET', '/api/projects/' + encodeURIComponent(p.slug) + '/notes.geojson')
      .then(function (d) { if (my === loadSeq) setData(d); })
      .catch(function () { if (my === loadSeq) { proj = null; setData(EMPTY); } });
  }

  // ---- the panel's Notes block -----------------------------------------------
  function firstLine(t) { t = t.split('\n')[0]; return t.length > 80 ? t.slice(0, 77) + '…' : t; }
  function renderBox() {
    if (!box || !document.body.contains(box)) return;
    if (!proj) { box.innerHTML = ''; return; }
    var fs = data.features, h = '<div class="tg-sub">Notes <span class="tg-muted tg-small">' +
      (fs.length ? fs.length + ' on the map (amber points) · ' : '') + 'only those who can edit the project see them</span></div>';
    if (placing) h += '<div class="tg-small tg-notes-hint">Click the map to place the note. Esc cancels.</div>';
    else h += '<div class="tg-actions"><button class="tg-btn" data-act="add">Add a note</button></div>';
    if (withList && fs.length) {
      h += '<ul class="tg-list tg-notes-list">' + fs.map(function (f) {
        return '<li><a href="#" data-id="' + f.properties.id + '">' + esc(firstLine(f.properties.text)) + '</a> <span class="tg-muted">' +
               esc((f.properties.updated || '').slice(0, 10)) + '</span></li>';
      }).join('') + '</ul>';
    }
    box.innerHTML = h;
    var add = box.querySelector('[data-act=add]');
    if (add) add.addEventListener('click', function () { startPlacing(null); });
    Array.prototype.forEach.call(box.querySelectorAll('a[data-id]'), function (a) {
      a.addEventListener('click', function (e) {
        e.preventDefault();
        var f = noteById(+a.dataset.id); if (!f) return;
        map.easeTo({ center: f.geometry.coordinates, zoom: Math.max(map.getZoom(), 16) });
        openNote(f);
      });
    });
  }

  // ---- placing a new note, or moving one -----------------------------------
  function startPlacing(id) {
    closePopup();
    placing = { id: id };
    map.getCanvas().style.cursor = 'crosshair';
    renderBox();
  }
  function stopPlacing() {
    if (!placing) return;
    placing = null;
    map.getCanvas().style.cursor = '';
    renderBox();
  }
  function onKey(e) { if (e.key === 'Escape' && placing) stopPlacing(); }

  function hitAt(point) {
    if (!map.getLayer('notes-pt')) return null;
    var fs = map.queryRenderedFeatures([[point.x - 5, point.y - 5], [point.x + 5, point.y + 5]], { layers: ['notes-pt'] });
    return fs.length ? noteById(fs[0].properties.id) : null;
  }
  function busy() { return window.TgDesign && TgDesign.editing(); }   // the alignment editor owns the map
  function claims(e) { return !busy() && !!(placing || hitAt(e.point)); }

  function onClick(e) {
    if (busy()) return;
    if (placing) {
      var ll = [e.lngLat.lng, e.lngLat.lat], id = placing.id;
      stopPlacing();
      if (id == null) { openEditor(null, ll); return; }
      api('POST', '/api/notes/' + id, { lon: ll[0], lat: ll[1] })
        .then(function (f) { replace(f); openNote(f); })
        .catch(function (err) { alertIn(err.message); });
      return;
    }
    var f = hitAt(e.point);
    if (f) openNote(f);
  }
  function replace(f) {
    var fs = data.features.filter(function (g) { return g.properties.id !== f.properties.id; });
    fs.push(f);
    setData({ type: 'FeatureCollection', features: fs });
  }
  function alertIn(msg) {
    if (box) { var d = document.createElement('div'); d.className = 'tg-error tg-small'; d.textContent = msg; box.appendChild(d); }
  }

  // ---- the popup ------------------------------------------------------------
  function closePopup() { if (popup) { var p = popup; popup = null; p.remove(); } }
  function showPopup(ll, el) {
    closePopup();
    popup = new maplibregl.Popup({ closeOnClick: false, maxWidth: '320px', className: 'tg-note-pop' })
      .setLngLat(ll).setDOMContent(el).addTo(map);
  }
  function openNote(f) {
    var p = f.properties, el = document.createElement('div');
    var who = p.author || '', when = (p.updated || '').slice(0, 10);
    el.innerHTML = '<div class="tg-note-text">' + esc(p.text) + '</div>' +
      '<div class="tg-muted tg-small">' + esc(who) + (who ? ' · ' : '') + esc(when) +
      (p.updated_by && p.updated_by !== p.author ? ' (edited by ' + esc(p.updated_by) + ')' : '') + '</div>' +
      '<div class="tg-note-acts"><button class="tg-btn" data-a="edit">Edit</button> <button class="tg-btn" data-a="move">Move</button> ' +
      '<button class="tg-btn" data-a="del">Delete</button></div>';
    el.querySelector('[data-a=edit]').addEventListener('click', function () { openEditor(f, f.geometry.coordinates); });
    el.querySelector('[data-a=move]').addEventListener('click', function () { startPlacing(p.id); });
    el.querySelector('[data-a=del]').addEventListener('click', function () {
      var acts = el.querySelector('.tg-note-acts');
      acts.innerHTML = 'Delete this note? <button class="tg-btn" data-a="yes">Delete</button> <button class="tg-btn" data-a="no">Keep</button>';
      acts.querySelector('[data-a=no]').addEventListener('click', function () { openNote(f); });
      acts.querySelector('[data-a=yes]').addEventListener('click', function () {
        api('POST', '/api/notes/' + p.id + '/delete').then(function () {
          closePopup();
          setData({ type: 'FeatureCollection', features: data.features.filter(function (g) { return g.properties.id !== p.id; }) });
        }).catch(function (err) { acts.textContent = err.message; });
      });
    });
    showPopup(f.geometry.coordinates, el);
  }
  // f null: a new note at ll.
  function openEditor(f, ll) {
    var el = document.createElement('div');
    el.innerHTML = '<textarea class="tg-notes-edit" rows="5" placeholder="What is here?"></textarea>' +
      '<div class="tg-note-acts"><button class="tg-btn tg-primary" data-a="save">Save</button> <button class="tg-btn" data-a="cancel">Cancel</button> ' +
      '<span class="tg-muted tg-small">Ctrl/Cmd-Enter saves</span></div>';
    var ta = el.querySelector('textarea'), st = el.querySelector('.tg-note-acts span');
    ta.value = f ? f.properties.text : '';
    function save() {
      if (!ta.value.trim()) { st.textContent = 'A note needs some text.'; return; }
      st.textContent = 'Saving…';
      var req = f ? api('POST', '/api/notes/' + f.properties.id, { text: ta.value })
                  : api('POST', '/api/projects/' + encodeURIComponent(proj.slug) + '/notes/new', { text: ta.value, lon: ll[0], lat: ll[1] });
      req.then(function (g) { replace(g); openNote(g); }).catch(function (err) { st.textContent = err.message; });
    }
    function cancel() { if (f) openNote(f); else closePopup(); }
    el.querySelector('[data-a=save]').addEventListener('click', save);
    el.querySelector('[data-a=cancel]').addEventListener('click', cancel);
    ta.addEventListener('keydown', function (e) {
      if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); save(); }
      else if (e.key === 'Escape') cancel();
    });
    showPopup(ll, el);
    setTimeout(function () { ta.focus(); }, 0);
  }

  return {
    init: function (a) {
      app = a; map = a.map;
      map.on('click', onClick);
      document.addEventListener('keydown', onKey);
    },
    onStyleReload: function () { addLayers(); },
    setProject: setProject,
    claims: claims,
    placing: function () { return !!placing; }
  };
})();
