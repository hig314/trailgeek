/* map.js — the trailgeek home map (Phase 1).
 *
 * Base layers lean on landslidescience: the basemap list (basemaps.js), the
 * URL-hash grammar (ls_hash.js), and its lidar collection through the
 * demshade bridge (dem_shade_bridge.js), read from this site's DemSource
 * table at /api/dems.geojson. Trail-specific parts are this file's own:
 * trails / tracks / alignments as live vector tiles, the detail panel, and
 * the D3 profile (tg_profile.js) sampled from terrain tiles (tg_sample.js)
 * or from a track's own GPS elevations.
 *
 * URL hash (LSHash grammar, so a view can be pasted between the two sites):
 *   map=z/lat/lon  base=<id>  li=<dem id>.h l<opacity%>  t3d=1
 *   trail=<slug> | track=<id> | align=<id>
 */
(function () {
  'use strict';

  var CFG = window.TG_CONFIG || {};
  var HOME = { lon: -151.19, lat: 59.62, zoom: 11 };   // Grewingk / Kachemak Bay
  var DEFAULT_BASE = 'usgs-topo';
  var SHADE = { az: 315, alt: 45, hs: 0.8, bl: 3, sl: 0, ve: 1 };   // bl 3 = multiply
  var $ = function (id) { return document.getElementById(id); };

  // ---- state from the URL --------------------------------------------------
  var state = LSHash.parse(location.hash);
  var hadView = state.zoom != null;
  var baseId = state.base || DEFAULT_BASE;
  var basemaps = LSBasemaps.DEFAULTS.filter(function (b) { return b.id !== 'blank'; });
  var baseOf = function (id) {
    return basemaps.filter(function (b) { return b.id === id; })[0] || basemaps.filter(function (b) { return b.id === DEFAULT_BASE; })[0];
  };

  LSBasemaps.registerProtocols();
  var map = new maplibregl.Map({
    container: 'map',
    center: hadView ? [state.lon, state.lat] : [HOME.lon, HOME.lat],
    zoom: hadView ? state.zoom : HOME.zoom,
    maxPitch: 85,
    hash: false,
    transformRequest: LSBasemaps.transformRequest,
    style: LSBasemaps.styleFor(baseOf(baseId))
  });
  window.tgMap = map;   // console handle for debugging
  map.addControl(new maplibregl.NavigationControl({ visualizePitch: true }), 'top-left');
  map.addControl(new maplibregl.ScaleControl({ unit: 'imperial' }), 'bottom-left');
  map.addControl(new maplibregl.ScaleControl({ unit: 'metric' }), 'bottom-left');

  // ---- DEMs: the terrain context plus every lidar survey in the catalogue --
  var dems = {};            // id -> catalogue properties
  var lidarIds = [];
  var activeDem = null;     // id of the survey feeding shade + terrain, or null for context only
  var explicitDem = state.li ? Object.keys(state.li)[0] : null;
  var shadeOpacity = state.li && explicitDem ? (state.li[explicitDem].opLeft != null ? state.li[explicitDem].opLeft : 0.6) : 0.6;
  var CTX = 'terrarium';

  function registerDems(fc) {
    fc.features.forEach(function (f) {
      var p = f.properties;
      dems[p.id] = p;
      if (p.kind === 'context') {
        if (p.encoding === 'terrarium') {
          DemShade.instance.addSource(p.id, { tiles: p.tiles_url, encoding: 'terrarium', maxzoom: p.max_zoom, alphaNoData: false })
            .catch(function (e) { console.warn('demshade context', e); });
        } else if (p.encoding === 'imageserver') {
          DemShade.addImageServer(p.id, p.tiles_url, { maxzoom: p.max_zoom });
        }
      } else {
        lidarIds.push(p.id);
        probe(p).then(function () {
          // One registration per survey, composited over the regional DEM
          // so both shading and 3D terrain run to the horizon (dem_fill.js's
          // idea, done inside the demshade worker). 'missing' keeps holes
          // inside the survey honest instead of patching sea surface in.
          DemShade.addDataset(p.id, p.pmtiles_url, DemShade.catalogOpts(p, {
            fill: CTX, fillMode: p.fill_mode || 'missing'
          }));
          p.ready = true;
        }, function (e) {
          p.unavailable = true;
          if (explicitDem === p.id) explicitDem = null;
        }).then(function () { fillDemPicker(); syncDem(false); });
      }
    });
    if (!dems[CTX]) {
      // Catalogue not imported yet: still give the map some terrain.
      DemShade.instance.addSource(CTX, {
        tiles: 'https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png',
        encoding: 'terrarium', maxzoom: 15, alphaNoData: false
      }).catch(function (e) { console.warn('demshade context', e); });
    }
  }

  // The archives live on landslidescience's R2 bucket, whose CORS allowlist
  // must name this origin (docs/OPERATIONS.md). Each survey is probed with
  // one small request before it is registered with demshade at all: a
  // survey that cannot be read from here is left out of the picker and the
  // auto choice, and never asked for tiles, so the map falls back to the
  // regional DEM with a single warning per survey instead of an error per
  // tile. Resolves when readable, rejects otherwise.
  var corsWarned = false;
  function probe(p) {
    var url, init = { mode: 'cors', credentials: 'omit' };
    if (p.tiles_url) {
      var z = p.min_zoom, n = Math.pow(2, z), c = p.bounds ? [(p.bounds[0] + p.bounds[2]) / 2, (p.bounds[1] + p.bounds[3]) / 2] : [0, 0];
      var lr = c[1] * Math.PI / 180;
      url = p.tiles_url.replace('{z}', z).replace('{x}', Math.floor((c[0] + 180) / 360 * n))
                       .replace('{y}', Math.floor((1 - Math.log(Math.tan(lr) + 1 / Math.cos(lr)) / Math.PI) / 2 * n));
    } else if (p.pmtiles_url) {
      url = p.pmtiles_url; init.headers = { Range: 'bytes=0-15' };
    } else { return Promise.reject(new Error('no archive URL')); }
    return fetch(url, init).then(function (r) {
      if (!r.ok && r.status !== 204) throw new Error('HTTP ' + r.status);
      if (!init.headers) return;
      // Read only the first chunk (a server ignoring Range would send the
      // whole multi-GB archive) and check the PMTiles magic.
      var reader = r.body.getReader();
      return reader.read().then(function (c) {
        reader.cancel();
        var head = String.fromCharCode.apply(null, (c.value || new Uint8Array(0)).slice(0, 7));
        if (head !== 'PMTiles') throw new Error('not a PMTiles archive');
      });
    }).catch(function (e) {
      console.warn('lidar ' + p.id + ' is not readable from ' + location.origin + ': ' + e.message);
      if (!corsWarned && e instanceof TypeError) {
        corsWarned = true;
        console.warn('A "Failed to fetch" here usually means this origin is missing from the ' +
                     'landslidescience-lidar R2 bucket CORS allowlist and the lidar-tiles Worker ' +
                     '(docs/OPERATIONS.md, open items). Shading falls back to the regional DEM.');
      }
      throw e;
    });
  }
  function usable(id) { return !!(dems[id] && dems[id].ready); }

  function overlaps(p, b) {
    var q = p.bounds;
    return q && !(q[2] < b.getWest() || q[0] > b.getEast() || q[3] < b.getSouth() || q[1] > b.getNorth());
  }
  function contains(p, c) {
    var q = p.bounds;
    return q && c.lng >= q[0] && c.lng <= q[2] && c.lat >= q[1] && c.lat <= q[3];
  }
  // Auto choice: the finest survey under the map centre.
  function autoDem() {
    var c = map.getCenter(), best = null;
    lidarIds.forEach(function (id) {
      var p = dems[id];
      if (!usable(id) || !contains(p, c)) return;
      if (!best || (p.native_res_m || 99) < (dems[best].native_res_m || 99)) best = id;
    });
    return best;
  }
  function fillDemPicker() {
    var sel = $('dem'), b = map.getBounds();
    var opts = '<option value="">Auto (finest lidar under the centre)</option>';
    lidarIds.filter(function (id) { return usable(id) && overlaps(dems[id], b); })
      .sort(function (a, c) { return (dems[a].title > dems[c].title) ? 1 : -1; })
      .forEach(function (id) {
        var p = dems[id];
        opts += '<option value="' + id + '">' + esc(p.title) + (p.native_res_m ? ' (' + p.native_res_m + ' m)' : '') + '</option>';
      });
    opts += '<option value="' + CTX + '">Regional DEM only</option>';
    sel.innerHTML = opts;
    sel.value = explicitDem || '';
  }
  function shadeSourceId() { return activeDem || CTX; }
  function shadeUrl() {
    var o = { az: SHADE.az, alt: SHADE.alt, hs: SHADE.hs, bl: SHADE.bl, ve: SHADE.ve };
    if ($('slope').checked) o.sl = 0.6;
    return DemShade.url(shadeSourceId(), o);
  }
  function activeMaxZoom() { var p = dems[shadeSourceId()]; return p && p.max_zoom ? p.max_zoom : 15; }
  function syncDem(force) {
    var want = explicitDem === CTX ? null : (explicitDem || autoDem());
    if (want && !usable(want)) want = null;
    var changed = want !== activeDem;
    activeDem = want;
    var p = activeDem ? dems[activeDem] : null;
    $('demstatus').textContent = p
      ? p.title + (p.year ? ' (' + p.year + ')' : '') + ' · ' + (p.native_res_m || '?') + ' m' + (p.source ? ' · ' + p.source : '')
      : 'Regional DEM (AWS Terrain Tiles)';
    if (!map.getLayer('alignments')) return;   // overlays not added yet (style still loading)
    if (changed || force) {
      rebuildShade();
      if (changed) rebuildTerrain();
    }
  }

  // The shade layer is rebuilt rather than re-pointed when the survey
  // changes: a raster source's maxzoom is fixed at creation, and it must
  // follow the survey (z15 for the regional DEM, up to z17 for 0.5 m lidar)
  // or the map either stops short or asks for tiles that do not exist.
  function rebuildShade() {
    if (map.getLayer('shade')) map.removeLayer('shade');
    if (map.getSource('shade')) map.removeSource('shade');
    map.addSource('shade', { type: 'raster', tiles: [shadeUrl()], tileSize: 256, maxzoom: activeMaxZoom(),
                             attribution: 'Terrain: AWS Terrain Tiles; lidar via landslidescience.org' });
    map.addLayer({ id: 'shade', type: 'raster', source: 'shade', paint: {
      'raster-opacity': shadeOpacity, 'raster-fade-duration': 0, 'raster-opacity-transition': { duration: 0, delay: 0 }
    } }, map.getLayer('selected-halo') ? 'selected-halo' : firstSymbolLayer());
  }
  function firstSymbolLayer() {
    var layers = map.getStyle().layers;
    for (var i = 0; i < layers.length; i++) if (layers[i].type === 'symbol') return layers[i].id;
    return undefined;
  }

  // ---- 3D ---------------------------------------------------------------------
  var untrack = null;
  function rebuildTerrain() {
    var on = $('t3d').checked;
    if (map.getTerrain()) map.setTerrain(null);
    if (map.getSource('dem')) map.removeSource('dem');
    if (!on) { map.setSky(null); if (untrack) { untrack(); untrack = null; } syncHash(); return; }
    map.addSource('dem', { type: 'raster-dem', tiles: [DemShade.demUrl(shadeSourceId())], encoding: 'mapbox', tileSize: 256, maxzoom: activeMaxZoom() });
    map.setTerrain({ source: 'dem', exaggeration: +$('exag').value / 10 });
    map.setSky({ 'sky-color': '#cfe0ee', 'horizon-color': '#e9eef3', 'fog-color': '#e9eef3',
                 'fog-ground-blend': 0.55, 'horizon-fog-blend': 0.9, 'sky-horizon-blend': 0.6, 'atmosphere-blend': 0 });
    if (!untrack && DemShade.trackTerrainCenter) untrack = DemShade.trackTerrainCenter(map);
    syncHash();
  }

  // ---- trails, tracks, alignments: live MVT --------------------------------
  function addTrailLayers() {
    map.addSource('trails', { type: 'vector', tiles: [location.origin + '/tiles/trails/{z}/{x}/{y}.mvt'], minzoom: 5, maxzoom: 18 });
    map.addLayer({ id: 'alignments', type: 'line', source: 'trails', 'source-layer': 'alignments',
      layout: { 'line-cap': 'round', 'line-join': 'round' },
      paint: { 'line-color': '#7b3fa0', 'line-width': ['interpolate', ['linear'], ['zoom'], 10, 1.5, 15, 3],
               'line-opacity': ['case', ['==', ['get', 'priority'], 1], 1, 0.6] } });
    map.addLayer({ id: 'tracks', type: 'line', source: 'trails', 'source-layer': 'tracks',
      layout: { 'line-cap': 'round', 'line-join': 'round' },
      paint: { 'line-color': '#d9772b', 'line-width': ['interpolate', ['linear'], ['zoom'], 10, 1.2, 15, 2.5], 'line-opacity': 0.85 } });
    map.addLayer({ id: 'trails-casing', type: 'line', source: 'trails', 'source-layer': 'trails',
      layout: { 'line-cap': 'round', 'line-join': 'round' },
      paint: { 'line-color': '#ffffff', 'line-width': ['interpolate', ['linear'], ['zoom'], 10, 3, 15, 6], 'line-opacity': 0.6 } });
    map.addLayer({ id: 'trails-line', type: 'line', source: 'trails', 'source-layer': 'trails',
      layout: { 'line-cap': 'round', 'line-join': 'round' },
      paint: { 'line-color': ['match', ['get', 'status'], 'proposed', '#1f78b4', 'historic', '#6b6b6b', '#2a7f2a'],
               'line-width': ['interpolate', ['linear'], ['zoom'], 10, 1.5, 15, 3.5],
               'line-dasharray': ['match', ['get', 'status'], 'proposed', ['literal', [2, 1.5]], 'historic', ['literal', [0.5, 1.5]], ['literal', [1, 0]]] } });
    map.addSource('selected', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
    map.addLayer({ id: 'selected-halo', type: 'line', source: 'selected',
      layout: { 'line-cap': 'round', 'line-join': 'round' },
      paint: { 'line-color': '#ffd400', 'line-width': 9, 'line-opacity': 0.55, 'line-blur': 2 } }, 'alignments');
    map.addSource('cursor', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
    map.addLayer({ id: 'cursor', type: 'circle', source: 'cursor',
      paint: { 'circle-radius': 6, 'circle-color': '#ffd400', 'circle-stroke-color': '#1f2a1f', 'circle-stroke-width': 2 } });
  }

  var CLICKABLE = ['trails-line', 'tracks', 'alignments'];
  map.on('click', function (e) {
    var fs = map.queryRenderedFeatures(e.point, { layers: CLICKABLE });
    if (!fs.length) return;
    var f = fs[0];
    if (f.layer.id === 'trails-line') select('trail', f.properties.slug, false);
    else if (f.layer.id === 'tracks') select('track', f.properties.id, false);
    else select('align', f.properties.id, false);
  });
  map.on('mousemove', function (e) {
    var hit = map.queryRenderedFeatures(e.point, { layers: CLICKABLE }).length > 0;
    map.getCanvas().style.cursor = hit ? 'pointer' : '';
  });

  // ---- selection + detail panel --------------------------------------------
  var selected = null;      // { kind, key }
  var profile = TgProfile.create('#profile', { onHover: function (s) {
    map.getSource('cursor').setData({ type: 'FeatureCollection', features: s ? [{ type: 'Feature', geometry: { type: 'Point', coordinates: [s.lon, s.lat] }, properties: {} }] : [] });
  } });
  var lastFeature = null, lastSamples = null;

  function apiUrl(kind, key) {
    return kind === 'trail' ? '/api/trails/' + encodeURIComponent(key) + '.geojson'
         : kind === 'track' ? '/api/tracks/' + key + '.geojson'
         : '/api/alignments/' + key + '.geojson';
  }
  function select(kind, key, fit) {
    selected = { kind: kind, key: String(key) };
    syncHash();
    $('panel').classList.add('open');
    $('detail').innerHTML = '<p class="tg-muted">Loading…</p>';
    profile.clear();
    fetch(apiUrl(kind, key), { credentials: 'same-origin' }).then(function (r) {
      if (!r.ok) throw new Error(r.status === 404 ? 'Not found, or not visible to you.' : 'Error ' + r.status);
      return r.json();
    }).then(function (f) {
      lastFeature = f;
      map.getSource('selected').setData(f);
      if (fit) map.fitBounds(bboxOf(f.geometry), { padding: 60, maxZoom: 15, duration: 0 });
      renderDetail(f);
      loadProfile(f, f.properties.has_elevation ? 'gps' : 'terrain');
    }).catch(function (e) {
      $('detail').innerHTML = '<p class="tg-error">' + esc(e.message) + '</p>';
    });
  }
  function clearSelection() {
    selected = null; lastFeature = null;
    map.getSource('selected').setData({ type: 'FeatureCollection', features: [] });
    map.getSource('cursor').setData({ type: 'FeatureCollection', features: [] });
    $('panel').classList.remove('open');
    profile.clear();
    syncHash();
  }
  $('panel-close').addEventListener('click', clearSelection);

  function flatCoords(geom) {
    if (geom.type === 'LineString') return geom.coordinates;
    var out = [];
    geom.coordinates.forEach(function (part) { out = out.concat(part); });
    return out;
  }
  function bboxOf(geom) {
    var b = [Infinity, Infinity, -Infinity, -Infinity];
    flatCoords(geom).forEach(function (c) {
      if (c[0] < b[0]) b[0] = c[0]; if (c[1] < b[1]) b[1] = c[1];
      if (c[0] > b[2]) b[2] = c[0]; if (c[1] > b[3]) b[3] = c[1];
    });
    return b;
  }

  function fmtKm(m) { return (m / 1000).toFixed(2) + ' km / ' + (m / 1609.344).toFixed(2) + ' mi'; }
  function fmtM(m) { return Math.round(m) + ' m / ' + Math.round(m * 3.28084) + ' ft'; }

  function renderDetail(f) {
    var p = f.properties, h = '';
    var kindLabel = { trail: 'Trail', track: 'GPS track', alignment: 'Alignment' }[p.kind];
    h += '<div class="tg-kind">' + kindLabel + (p.status ? ' · ' + esc(p.status) : '') + (p.priority ? ' · priority ' + p.priority : '') + '</div>';
    h += '<h2>' + esc(p.name) + '</h2>';
    if (p.project) h += '<div class="tg-muted">Project: ' + esc(p.project.name) + (p.trailhead ? ' · from ' + esc(p.trailhead) : '') + '</div>';
    if (p.trail) h += '<div class="tg-muted">On trail: <a href="#trail=' + esc(p.trail.slug) + '">' + esc(p.trail.name) + '</a></div>';
    if (p.taken_at) h += '<div class="tg-muted">Recorded ' + esc(p.taken_at.slice(0, 10)) + (p.device ? ' · ' + esc(p.device) : '') + '</div>';
    if (p.region) h += '<div class="tg-muted">' + esc(p.region) + '</div>';
    h += '<dl class="tg-stats"><dt>Length</dt><dd>' + fmtKm(p.length_m) + '</dd>';
    if (p.kind === 'track' && p.has_elevation) {
      h += '<dt>GPS climb</dt><dd>' + fmtM(p.climb_m) + '</dd><dt>GPS descent</dt><dd>' + fmtM(p.descent_m) + '</dd>';
    }
    h += '</dl>';
    if (p.description) h += '<p class="tg-desc">' + esc(p.description) + '</p>';
    if (p.tracks && p.tracks.length) {
      h += '<div class="tg-sub">Tracks on this trail</div><ul class="tg-list">';
      p.tracks.forEach(function (t) { h += '<li><a href="#track=' + t.id + '">' + esc(t.name) + '</a>' + (t.taken ? ' <span class="tg-muted">' + t.taken + '</span>' : '') + '</li>'; });
      h += '</ul>';
    }
    if (p.siblings && p.siblings.length) {
      h += '<div class="tg-sub">Other alignments in this project</div><ul class="tg-list">';
      p.siblings.forEach(function (s) { h += '<li><a href="#align=' + s.id + '">' + esc(s.name) + '</a> <span class="tg-muted">priority ' + s.priority + '</span></li>'; });
      h += '</ul>';
    }
    var links = [];
    if (p.kind === 'track' && p.original) links.push('<a href="/tracks/' + p.id + '/download/">Download original GPX</a>');
    if (p.can_edit || CFG.canEdit) {
      var adminUrl = p.kind === 'trail' ? '/admin/core/trail/' + p.id + '/change/' : p.kind === 'track' ? '/admin/core/track/' + p.id + '/change/' : '/admin/core/alignment/' + p.id + '/change/';
      links.push('<a href="' + adminUrl + '">Edit</a>');
    }
    links.push('<a href="#" id="zoomto">Zoom to</a>');
    h += '<div class="tg-links">' + links.join(' · ') + '</div>';
    // Profile source switch: a track with GPS elevations can be compared
    // against the terrain tiles.
    h += '<div class="tg-sub">Profile <span id="profsrc" class="tg-muted"></span></div>';
    if (p.has_elevation) {
      h += '<div class="tg-toggle"><label><input type="radio" name="psrc" value="gps" checked> GPS elevation</label> ' +
           '<label><input type="radio" name="psrc" value="terrain"> Terrain tiles</label></div>';
    }
    $('detail').innerHTML = h;
    $('zoomto').addEventListener('click', function (ev) { ev.preventDefault(); map.fitBounds(bboxOf(f.geometry), { padding: 60, maxZoom: 15 }); });
    Array.prototype.forEach.call(document.querySelectorAll('input[name=psrc]'), function (r) {
      r.addEventListener('change', function () { loadProfile(f, r.value); });
    });
  }

  function loadProfile(f, source) {
    var coords = flatCoords(f.geometry);
    $('profstats').innerHTML = '';
    if (source === 'gps') {
      var d = TgSample.distances(coords);
      var s = coords.map(function (c, i) { return { d: d[i], z: c[2] || 0, lon: c[0], lat: c[1] }; });
      showProfile(s, 'from the GPS file');
      return;
    }
    $('profsrc').textContent = '· sampling terrain tiles…';
    var lengthM = TgSample.distances(coords)[coords.length - 1];
    var spacing = Math.max(10, Math.round(lengthM / 800));   // ≤ ~800 samples
    TgSample.profile(coords, { spacing: spacing }).then(function (s) {
      if (lastFeature !== f) return;
      showProfile(s, 'from AWS Terrain Tiles (coarse; lidar profile comes with the evaluator)');
    }).catch(function (e) { $('profsrc').textContent = '· terrain sampling failed: ' + e.message; });
  }
  function showProfile(s, label) {
    lastSamples = s;
    profile.render(s);
    $('profsrc').textContent = '· ' + label;
    var st = TgProfile.stats(s);
    if (!st) return;
    $('profstats').innerHTML =
      '<dl class="tg-stats"><dt>Climb</dt><dd>' + fmtM(st.climb_m) + '</dd><dt>Descent</dt><dd>' + fmtM(st.descent_m) + '</dd>' +
      '<dt>Elevation</dt><dd>' + Math.round(st.min_z) + '–' + Math.round(st.max_z) + ' m</dd>' +
      '<dt>Steepest 30 m</dt><dd>' + (st.max_grade >= 0 ? '+' : '') + st.max_grade.toFixed(0) + ' %</dd></dl>' +
      '<div class="tg-keys">' + profile.legend() + '</div>';
  }

  // ---- basemap picker -------------------------------------------------------
  function fillBasemaps() {
    var sel = $('base');
    sel.innerHTML = basemaps.map(function (b) { return '<option value="' + b.id + '">' + esc(b.label) + '</option>'; }).join('');
    sel.value = baseId;
    sel.addEventListener('change', function () { setBasemap(sel.value); });
  }
  function setBasemap(id) {
    baseId = id;
    var terrainOn = $('t3d').checked;
    map.setStyle(LSBasemaps.styleFor(baseOf(id)));
    map.once('style.load', function () { addOverlays(); if (terrainOn) rebuildTerrain(); });
    syncHash();
  }
  function addOverlays() {
    addTrailLayers();
    if (lastFeature) map.getSource('selected').setData(lastFeature);
    syncDem(true);
  }

  // ---- hash ----------------------------------------------------------------
  var hashLock = false;
  function syncHash() {
    var c = map.getCenter();
    var o = { zoom: map.getZoom(), lat: c.lat, lon: c.lng, base: baseId !== DEFAULT_BASE ? baseId : null, extras: {} };
    if (explicitDem) { o.li = {}; o.li[explicitDem] = { preset: 'hillshade', left: true, opLeft: shadeOpacity }; }
    if ($('t3d').checked) o.extras.t3d = '1';
    if (selected) o.extras[selected.kind] = selected.key;
    hashLock = true;
    history.replaceState(null, '', LSHash.encode(o));
    hashLock = false;
  }
  window.addEventListener('hashchange', function () {
    if (hashLock) return;
    var s = LSHash.parse(location.hash);
    var want = s.extras.trail ? ['trail', s.extras.trail] : s.extras.track ? ['track', s.extras.track] : s.extras.align ? ['align', s.extras.align] : null;
    if (want && (!selected || selected.kind !== want[0] || selected.key !== String(want[1]))) select(want[0], want[1], true);
    else if (!want && selected) clearSelection();
  });
  map.on('moveend', function () { fillDemPicker(); syncDem(false); syncHash(); });

  // ---- controls --------------------------------------------------------------
  $('dem').addEventListener('change', function () { explicitDem = $('dem').value || null; syncDem(true); syncHash(); });
  $('shade-op').value = Math.round(shadeOpacity * 100);
  $('shade-op').addEventListener('input', function () {
    shadeOpacity = +$('shade-op').value / 100;
    if (map.getLayer('shade')) map.setPaintProperty('shade', 'raster-opacity', shadeOpacity);
    syncHash();
  });
  $('slope').addEventListener('change', function () { rebuildShade(); });
  $('t3d').checked = state.extras.t3d === '1';
  $('t3d').addEventListener('change', function () { $('exag').disabled = !$('t3d').checked; rebuildTerrain(); });
  $('exag').disabled = !$('t3d').checked;
  $('exag').addEventListener('input', function () {
    if (map.getTerrain()) map.setTerrain({ source: 'dem', exaggeration: +$('exag').value / 10 });
  });
  $('layers-toggle').addEventListener('click', function () { $('layers').classList.toggle('open'); });

  function esc(t) {
    return String(t == null ? '' : t).replace(/[&<>"]/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]; });
  }

  // ---- boot -------------------------------------------------------------------
  fillBasemaps();
  map.on('load', function () {
    fetch('/api/dems.geojson', { credentials: 'same-origin' })
      .then(function (r) { return r.json(); })
      .catch(function () { return { type: 'FeatureCollection', features: [] }; })
      .then(function (fc) {
        registerDems(fc);
        addOverlays();
        fillDemPicker();
        if ($('t3d').checked) rebuildTerrain();
        var s = state.extras;
        if (s.trail) select('trail', s.trail, !hadView);
        else if (s.track) select('track', s.track, !hadView);
        else if (s.align) select('align', s.align, !hadView);
      });
  });
})();
