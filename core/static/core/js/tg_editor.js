/* tg_editor.js — edit an alignment as one continuous line made of legs.
 * Global `window.TgEditor`.
 *
 * Internally the alignment is ONE vertex list plus the indices where legs
 * meet, so a joint is a single shared vertex: dragging it moves the end of
 * one leg and the start of the next together, and the line can never come
 * apart. Legs go to and from the server as separate coordinate lists.
 *
 * Modes (one at a time, like the tool group in landslidescience's ls_tools):
 *   edit    drag a vertex (plain vertices show from z15.5, add-point handles
 *           from z16.5; ends and leg joints always); drag a midpoint handle to add one; right-click
 *           (or Alt-click) a vertex to delete it; click a leg to pick it
 *   draw    click to extend the end of the line with new construction;
 *           double-click, Enter or Esc to finish
 *   follow  click a point on an existing trail: the shortest path along the
 *           trail network from the end of the line is added as an
 *           existing-trail leg (with a short new connector if the end is
 *           off the network)
 *   split   click a vertex to split its leg in two there
 * Dragged and drawn points snap to existing trails within SNAP_PX (hold
 * Shift to place freely). Ctrl/Cmd-Z undoes, Ctrl-Shift-Z / Ctrl-Y redoes.
 *
 *   var ed = TgEditor.create(map, { onChange, onMode, onSelectLeg, onMessage, beforeLayer });
 *   ed.start(legs)        // [{coords, kind, name, trail_id, effort_factor, notes}]
 *   ed.legs()             // same shape, current state
 *   ed.setMode('draw'); ed.undo(); ed.redo(); ed.reverse();
 *   ed.setLeg(i, {kind: 'reroute'}); ed.mergeWithNext(i); ed.removeEndLeg(i);
 *   ed.stop();
 */
window.TgEditor = (function () {
  'use strict';

  var SNAP_PX = 12;
  // Imported lines can carry a vertex every metre or two, so ordinary
  // vertices and the add-a-point handles only appear when zoomed in far
  // enough to grab them; ends and joints between legs always show.
  var PLAIN_MINZOOM = 15.5, MID_MINZOOM = 16.5;
  var LEG_COLORS = (window.TgProfile && TgProfile.LEG_COLORS) || { existing: '#4d7c2a', 'new': '#7b3fa0', reroute: '#d9601a', restore: '#0f7fbf' };
  var SRC = ['ed-legs', 'ed-verts', 'ed-mids', 'ed-rubber', 'ed-snap'];

  function create(map, opts) {
    opts = opts || {};
    var V = [];          // [[lon, lat], ...]
    var B = [];          // leg joints: B[0] = 0 ... B[n] = V.length - 1  (n legs)
    var M = [];          // per-leg metadata
    var mode = 'edit', active = false, selLeg = null;
    var undoStack = [], redoStack = [];
    var drag = null, hoverPt = null, busy = false;

    // ---- state helpers ------------------------------------------------------
    function snapshot() { return JSON.stringify({ V: V, B: B, M: M }); }
    function restore(s) { var o = JSON.parse(s); V = o.V; B = o.B; M = o.M; }
    function pushUndo() { undoStack.push(snapshot()); if (undoStack.length > 200) undoStack.shift(); redoStack = []; }
    function nLegs() { return Math.max(0, B.length - 1); }
    function legOf(vi) {             // the leg a vertex belongs to (a joint belongs to the one it starts)
      for (var j = 0; j < nLegs(); j++) if (vi >= B[j] && vi < B[j + 1]) return j;
      return nLegs() - 1;
    }
    function isJoint(vi) { return B.indexOf(vi) > 0 && B.indexOf(vi) < B.length - 1; }
    function isEnd(vi) { return vi === 0 || vi === V.length - 1; }
    function blankMeta(kind) { return { kind: kind || 'new', name: '', trail_id: null, effort_factor: 1.0, notes: '' }; }

    function legsOut() {
      var out = [];
      for (var j = 0; j < nLegs(); j++) {
        var m = M[j];
        out.push({ coords: V.slice(B[j], B[j + 1] + 1).map(function (c) { return [c[0], c[1]]; }),
                   kind: m.kind, name: m.name, trail_id: m.trail_id, effort_factor: m.effort_factor,
                   notes: m.notes, trail_name: m.trail_name || '' });
      }
      return out;
    }

    function load(legs) {
      V = []; B = []; M = [];
      (legs || []).forEach(function (L) {
        var cs = L.coords || [];
        if (cs.length < 2) return;
        if (!V.length) { V.push(cs[0].slice(0, 2)); B.push(0); }
        for (var i = 1; i < cs.length; i++) V.push(cs[i].slice(0, 2));
        B.push(V.length - 1);
        M.push({ kind: L.kind || 'new', name: L.name || '', trail_id: L.trail_id || (L.trail ? L.trail.id : null),
                 trail_name: L.trail ? L.trail.name : (L.trail_name || ''),
                 effort_factor: L.effort_factor != null ? L.effort_factor : 1.0, notes: L.notes || '' });
      });
    }

    // ---- geometry helpers ---------------------------------------------------
    function metres(a, b) {
      var R = 6371008.8, k = Math.PI / 180;
      var dx = (b[0] - a[0]) * k * Math.cos((a[1] + b[1]) / 2 * k), dy = (b[1] - a[1]) * k;
      return R * Math.sqrt(dx * dx + dy * dy);
    }
    function nearestOnSegments(pt, lines) {   // screen-space nearest point on a set of lon/lat lines
      var P = map.project(pt), best = null;
      lines.forEach(function (cs) {
        for (var i = 0; i < cs.length - 1; i++) {
          var A = map.project(cs[i]), Bp = map.project(cs[i + 1]);
          var dx = Bp.x - A.x, dy = Bp.y - A.y, L2 = dx * dx + dy * dy;
          var t = L2 ? Math.max(0, Math.min(1, ((P.x - A.x) * dx + (P.y - A.y) * dy) / L2)) : 0;
          var qx = A.x + t * dx, qy = A.y + t * dy, d = Math.hypot(P.x - qx, P.y - qy);
          if (!best || d < best.d) best = { d: d, lngLat: map.unproject([qx, qy]) };
        }
      });
      return best;
    }
    function trailLinesNear(point) {
      var bb = [[point.x - SNAP_PX, point.y - SNAP_PX], [point.x + SNAP_PX, point.y + SNAP_PX]];
      var fs = map.getLayer('trails-line') ? map.queryRenderedFeatures(bb, { layers: ['trails-line'] }) : [];
      var lines = [];
      fs.forEach(function (f) {
        var g = f.geometry;
        if (g.type === 'LineString') lines.push(g.coordinates);
        else if (g.type === 'MultiLineString') g.coordinates.forEach(function (c) { lines.push(c); });
      });
      return { lines: lines, features: fs };
    }
    function snapped(e) {
      var ll = [e.lngLat.lng, e.lngLat.lat];
      if (e.originalEvent && e.originalEvent.shiftKey) return { pt: ll, snapped: false };
      var near = trailLinesNear(e.point);
      if (!near.lines.length) return { pt: ll, snapped: false };
      var n = nearestOnSegments(ll, near.lines);
      if (n && n.d <= SNAP_PX) return { pt: [n.lngLat.lng, n.lngLat.lat], snapped: true };
      return { pt: ll, snapped: false };
    }

    // ---- rendering ----------------------------------------------------------
    function fc(features) { return { type: 'FeatureCollection', features: features }; }
    function render() {
      if (!active || !map.getSource('ed-legs')) return;
      var legsF = [], verts = [], mids = [];
      for (var j = 0; j < nLegs(); j++) {
        legsF.push({ type: 'Feature', properties: { i: j, kind: M[j].kind, sel: j === selLeg ? 1 : 0 },
                     geometry: { type: 'LineString', coordinates: V.slice(B[j], B[j + 1] + 1) } });
      }
      V.forEach(function (c, i) {
        verts.push({ type: 'Feature', properties: { i: i, joint: isJoint(i) ? 1 : 0, end: isEnd(i) ? 1 : 0 },
                     geometry: { type: 'Point', coordinates: c } });
        if (i < V.length - 1) {
          mids.push({ type: 'Feature', properties: { after: i },
                      geometry: { type: 'Point', coordinates: [(c[0] + V[i + 1][0]) / 2, (c[1] + V[i + 1][1]) / 2] } });
        }
      });
      map.getSource('ed-legs').setData(fc(legsF));
      map.getSource('ed-verts').setData(fc(verts));
      map.getSource('ed-mids').setData(fc(mode === 'edit' ? mids : []));
      var rubber = [];
      if (mode === 'draw' && hoverPt && V.length) {
        rubber.push({ type: 'Feature', properties: {}, geometry: { type: 'LineString', coordinates: [V[V.length - 1], hoverPt] } });
      }
      map.getSource('ed-rubber').setData(fc(rubber));
    }
    var raf = null;
    function renderSoon() { if (!raf) raf = requestAnimationFrame(function () { raf = null; render(); }); }

    function addLayers() {
      SRC.forEach(function (id) { if (!map.getSource(id)) map.addSource(id, { type: 'geojson', data: fc([]) }); });
      var before = opts.beforeLayer && map.getLayer(opts.beforeLayer) ? opts.beforeLayer : undefined;
      var kindColor = ['match', ['get', 'kind'], 'existing', LEG_COLORS.existing, 'reroute', LEG_COLORS.reroute,
                       'restore', LEG_COLORS.restore, LEG_COLORS['new']];
      var layers = [
        { id: 'ed-legs-casing', type: 'line', source: 'ed-legs', layout: { 'line-cap': 'round', 'line-join': 'round' },
          paint: { 'line-color': '#ffffff', 'line-width': ['case', ['==', ['get', 'sel'], 1], 11, 8], 'line-opacity': 0.9 } },
        { id: 'ed-legs-line', type: 'line', source: 'ed-legs', layout: { 'line-cap': 'round', 'line-join': 'round' },
          paint: { 'line-color': kindColor, 'line-width': ['case', ['==', ['get', 'sel'], 1], 6, 4],
                   'line-dasharray': ['match', ['get', 'kind'], 'reroute', ['literal', [2, 1]], 'restore', ['literal', [3, 1, 0.5, 1]], ['literal', [1, 0]]] } },
        { id: 'ed-rubber', type: 'line', source: 'ed-rubber', paint: { 'line-color': LEG_COLORS['new'], 'line-width': 2, 'line-dasharray': [2, 2] } },
        { id: 'ed-mids', type: 'circle', source: 'ed-mids', minzoom: MID_MINZOOM,
          paint: { 'circle-radius': 3, 'circle-color': '#ffffff', 'circle-opacity': 0.85, 'circle-stroke-color': '#555', 'circle-stroke-width': 1 } },
        { id: 'ed-verts-plain', type: 'circle', source: 'ed-verts', minzoom: PLAIN_MINZOOM,
          filter: ['all', ['==', ['get', 'joint'], 0], ['==', ['get', 'end'], 0]],
          paint: { 'circle-radius': 4, 'circle-color': '#ffffff', 'circle-stroke-color': '#1f2a1f', 'circle-stroke-width': 1.3 } },
        { id: 'ed-verts', type: 'circle', source: 'ed-verts',
          filter: ['any', ['==', ['get', 'joint'], 1], ['==', ['get', 'end'], 1]],
          paint: { 'circle-radius': 7, 'circle-color': ['case', ['==', ['get', 'joint'], 1], '#ffd400', '#1f2a1f'],
                   'circle-stroke-color': ['case', ['==', ['get', 'joint'], 1], '#1f2a1f', '#ffffff'], 'circle-stroke-width': 2 } },
        { id: 'ed-snap', type: 'circle', source: 'ed-snap',
          paint: { 'circle-radius': 9, 'circle-color': 'rgba(0,0,0,0)', 'circle-stroke-color': '#2a7f2a', 'circle-stroke-width': 2.5 } }
      ];
      layers.forEach(function (L) { if (!map.getLayer(L.id)) map.addLayer(L, before); });
    }
    function removeLayers() {
      ['ed-snap', 'ed-verts', 'ed-verts-plain', 'ed-mids', 'ed-rubber', 'ed-legs-line', 'ed-legs-casing'].forEach(function (id) { if (map.getLayer(id)) map.removeLayer(id); });
      SRC.forEach(function (id) { if (map.getSource(id)) map.removeSource(id); });
    }
    function showSnap(pt) {
      if (map.getSource('ed-snap')) map.getSource('ed-snap').setData(fc(pt ? [{ type: 'Feature', properties: {}, geometry: { type: 'Point', coordinates: pt } }] : []));
    }

    // ---- editing operations -----------------------------------------------
    function changed() { render(); if (opts.onChange) opts.onChange(legsOut()); }
    function msg(t, isError) { if (opts.onMessage) opts.onMessage(t, isError); }

    function insertAfter(i, pt) {
      V.splice(i + 1, 0, pt);
      for (var k = 0; k < B.length; k++) if (B[k] > i) B[k]++;
      return i + 1;
    }
    function deleteVertex(i) {
      if (isJoint(i)) { msg('That vertex joins two legs. Merge the legs first to remove it.', true); return; }
      var j = legOf(i);
      if (i === V.length - 1) j = nLegs() - 1;
      if (B[j + 1] - B[j] < 2) { msg('A leg needs at least two points.', true); return; }
      pushUndo();
      V.splice(i, 1);
      for (var k = 0; k < B.length; k++) if (B[k] > i || (B[k] === i && k === B.length - 1)) B[k]--;
      changed();
    }
    function appendPoint(pt, kind) {
      kind = kind || 'new';
      if (!V.length) { V.push(pt); B = [0]; M = []; return; }
      if (metres(V[V.length - 1], pt) < 0.3) return;
      if (!nLegs()) { V.push(pt); B = [0, 1]; M = [blankMeta(kind)]; return; }
      var last = M[nLegs() - 1];
      if (last.kind !== kind) {            // start a new leg at the current end
        V.push(pt); B.push(V.length - 1); M.push(blankMeta(kind));
      } else {
        V.push(pt); B[B.length - 1] = V.length - 1;
      }
    }
    function appendLeg(coords, meta) {
      if (!coords || coords.length < 2) return;
      if (!V.length) { V.push(coords[0].slice(0, 2)); B = [0]; M = []; }
      else if (metres(V[V.length - 1], coords[0]) > 0.5) {
        // Off the network: a short new-construction connector keeps the line continuous.
        V.push(coords[0].slice(0, 2)); B.push(V.length - 1); M.push(blankMeta('new'));
        M[M.length - 1].name = 'Connector';
      }
      for (var i = 1; i < coords.length; i++) V.push(coords[i].slice(0, 2));
      B.push(V.length - 1);
      var m = blankMeta(meta.kind); Object.keys(meta).forEach(function (k) { m[k] = meta[k]; });
      M.push(m);
    }

    // ---- events ------------------------------------------------------------
    function vertexAt(point, layer) {
      var layers = (layer === 'ed-verts' ? ['ed-verts', 'ed-verts-plain'] : [layer]).filter(function (l) { return map.getLayer(l); });
      var fs = layers.length ? map.queryRenderedFeatures([[point.x - 6, point.y - 6], [point.x + 6, point.y + 6]], { layers: layers }) : [];
      return fs.length ? fs[0].properties : null;
    }

    function onMouseDown(e) {
      if (!active || busy || e.originalEvent.button !== 0) return;
      if (mode === 'edit' || mode === 'split') {
        var v = vertexAt(e.point, 'ed-verts');
        if (mode === 'split') {
          if (!v) return;
          e.preventDefault();
          if (isEnd(v.i) || isJoint(v.i)) { msg('Pick a point inside a leg (not an end or a joint).', true); return; }
          pushUndo();
          var j = legOf(v.i);
          B.splice(j + 1, 0, v.i);
          var m = JSON.parse(JSON.stringify(M[j])); M.splice(j + 1, 0, m);
          selLeg = j + 1;
          setMode('edit');
          changed();
          if (opts.onSelectLeg) opts.onSelectLeg(selLeg);
          return;
        }
        if (e.originalEvent.altKey && v) { e.preventDefault(); deleteVertex(v.i); return; }
        var mid = !v && vertexAt(e.point, 'ed-mids');
        if (!v && !mid) return;
        e.preventDefault();
        var before = snapshot();
        var idx = v ? v.i : insertAfter(mid.after, [e.lngLat.lng, e.lngLat.lat]);
        drag = { i: idx, before: before, moved: !v };
        map.dragPan.disable();
        map.getCanvas().style.cursor = 'grabbing';
      }
    }
    function onMouseMove(e) {
      if (!active) return;
      if (drag) {
        var s = snapped(e);
        V[drag.i] = s.pt; drag.moved = true;
        showSnap(s.snapped ? s.pt : null);
        renderSoon();
        return;
      }
      if (mode === 'draw') {
        var d = snapped(e); hoverPt = d.pt; showSnap(d.snapped ? d.pt : null); renderSoon();
      } else if (mode === 'follow') {
        var n = trailLinesNear(e.point);
        map.getCanvas().style.cursor = n.lines.length ? 'copy' : 'not-allowed';
      } else if (mode === 'edit' || mode === 'split') {
        var over = vertexAt(e.point, 'ed-verts') || (mode === 'edit' && vertexAt(e.point, 'ed-mids'));
        map.getCanvas().style.cursor = over ? (mode === 'split' ? 'crosshair' : 'grab') : '';
      }
    }
    function onMouseUp() {
      if (!drag) return;
      map.dragPan.enable();
      map.getCanvas().style.cursor = '';
      showSnap(null);
      if (drag.moved) { undoStack.push(drag.before); redoStack = []; changed(); }
      drag = null;
    }
    function onContextMenu(e) {
      if (!active || mode !== 'edit') return;
      var v = vertexAt(e.point, 'ed-verts');
      if (v) { e.preventDefault(); deleteVertex(v.i); }
    }
    function onClick(e) {
      if (!active || busy) return;
      if (mode === 'draw') {
        var s = snapped(e);
        pushUndo(); appendPoint(s.pt, 'new'); changed();
      } else if (mode === 'follow') {
        var near = trailLinesNear(e.point);
        if (!near.lines.length) { msg('Click on an existing trail (green line).', true); return; }
        var n = nearestOnSegments([e.lngLat.lng, e.lngLat.lat], near.lines);
        var to = [n.lngLat.lng, n.lngLat.lat];
        if (!V.length) { pushUndo(); V = [to]; B = [0]; M = []; changed(); msg('Start set on the trail. Now click further along the trails.'); return; }
        var from = V[V.length - 1];
        busy = true; msg('Finding the path along the trails…');
        fetch('/api/route/trails?from=' + from[0] + ',' + from[1] + '&to=' + to[0] + ',' + to[1], { credentials: 'same-origin' })
          .then(function (r) { return r.json().then(function (j) { if (!r.ok) throw new Error(j.error || ('HTTP ' + r.status)); return j; }); })
          .then(function (r) {
            pushUndo();
            var names = r.trails.map(function (t) { return t.name; }).filter(function (x, i, a) { return x && a.indexOf(x) === i; });
            appendLeg(r.coords, { kind: 'existing', trail_id: r.trails.length ? r.trails[0].id : null,
                                  trail_name: names.join(', '), name: names.slice(0, 3).join(' → ') });
            changed();
            msg('Added ' + Math.round(r.length_m) + ' m along ' + (names.join(', ') || 'existing trail') +
                (r.start_offset_m > 0.5 ? ' (with a ' + Math.round(r.start_offset_m) + ' m connector)' : '') + '.');
          })
          .catch(function (err) { msg('No trail path: ' + err.message + '. Draw the gap as new construction instead.', true); })
          .then(function () { busy = false; });
      } else if (mode === 'edit') {
        var L = map.getLayer('ed-legs-line') ? map.queryRenderedFeatures([[e.point.x - 4, e.point.y - 4], [e.point.x + 4, e.point.y + 4]], { layers: ['ed-legs-line'] }) : [];
        if (L.length) { selLeg = L[0].properties.i; render(); if (opts.onSelectLeg) opts.onSelectLeg(selLeg); }
      }
    }
    function onDblClick(e) {
      if (active && mode === 'draw') { e.preventDefault(); setMode('edit'); }
    }
    function onKey(e) {
      if (!active) return;
      var tag = (e.target && e.target.tagName) || '';
      if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return;
      var mod = e.ctrlKey || e.metaKey;
      if (mod && (e.key === 'z' || e.key === 'Z')) { e.preventDefault(); if (e.shiftKey) redo(); else undo(); }
      else if (mod && (e.key === 'y' || e.key === 'Y')) { e.preventDefault(); redo(); }
      else if (e.key === 'Escape' || e.key === 'Enter') { if (mode !== 'edit') setMode('edit'); }
    }

    function setMode(m) {
      mode = m; hoverPt = null; showSnap(null);
      if (map.doubleClickZoom) { if (m === 'draw') map.doubleClickZoom.disable(); else map.doubleClickZoom.enable(); }
      map.getCanvas().style.cursor = m === 'draw' ? 'crosshair' : '';
      render();
      if (opts.onMode) opts.onMode(m);
    }
    function undo() { if (!undoStack.length) return; redoStack.push(snapshot()); restore(undoStack.pop()); changed(); }
    function redo() { if (!redoStack.length) return; undoStack.push(snapshot()); restore(redoStack.pop()); changed(); }

    var handlers = { mousedown: onMouseDown, mousemove: onMouseMove, mouseup: onMouseUp, click: onClick,
                     dblclick: onDblClick, contextmenu: onContextMenu };
    function bind(on) {
      Object.keys(handlers).forEach(function (k) { (on ? map.on : map.off).call(map, k, handlers[k]); });
      (on ? document.addEventListener : document.removeEventListener).call(document, 'keydown', onKey);
    }

    return {
      start: function (legs) {
        load(legs); undoStack = []; redoStack = []; selLeg = null; active = true;
        addLayers(); bind(true); setMode(V.length ? 'edit' : 'draw'); changed();
      },
      stop: function () {
        active = false; bind(false); removeLayers();
        if (map.doubleClickZoom) map.doubleClickZoom.enable();
        map.dragPan.enable(); map.getCanvas().style.cursor = '';
      },
      reattach: function () { if (active) { addLayers(); render(); } },   // after a basemap/style change
      active: function () { return active; },
      legs: legsOut,
      mode: function () { return mode; },
      setMode: setMode, undo: undo, redo: redo,
      canUndo: function () { return undoStack.length > 0; }, canRedo: function () { return redoStack.length > 0; },
      selectLeg: function (j) { selLeg = j; render(); },
      setLeg: function (j, props) {
        pushUndo();
        Object.keys(props).forEach(function (k) { M[j][k] = props[k]; });
        changed();
      },
      mergeWithNext: function (j) {
        if (j >= nLegs() - 1) return;
        pushUndo(); B.splice(j + 1, 1); M.splice(j + 1, 1); selLeg = j; changed();
      },
      removeEndLeg: function (j) {
        var n = nLegs();
        if (n < 2 || (j !== 0 && j !== n - 1)) { msg('Only the first or last leg can be removed; merge a middle leg instead.', true); return; }
        pushUndo();
        if (j === 0) { var cut = B[1]; V = V.slice(cut); B = B.slice(1).map(function (b) { return b - cut; }); M = M.slice(1); }
        else { V = V.slice(0, B[n - 1] + 1); B = B.slice(0, n); M = M.slice(0, n - 1); }
        selLeg = null; changed();
      },
      reverse: function () {
        if (V.length < 2) return;
        pushUndo();
        var last = V.length - 1;
        V = V.slice().reverse(); B = B.map(function (b) { return last - b; }).reverse(); M = M.slice().reverse();
        changed();
      },
      fitBounds: function () {
        if (V.length < 2) return;
        var b = V.reduce(function (a, c) { return [Math.min(a[0], c[0]), Math.min(a[1], c[1]), Math.max(a[2], c[0]), Math.max(a[3], c[1])]; }, [180, 90, -180, -90]);
        map.fitBounds(b, { padding: 60, maxZoom: 16 });
      }
    };
  }

  return { create: create };
})();
