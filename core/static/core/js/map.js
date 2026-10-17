/* map.js — the trailgeek home map (Phase 1).
 *
 * Base layers lean on landslidescience: the basemap list (basemaps.js), the
 * URL-hash grammar (ls_hash.js), and its lidar collection through the
 * demshade bridge (dem_shade_bridge.js) and the DEM stack shared with
 * /lidar/ (dem_stack.js), read from this site's DemSource table at
 * /api/dems.geojson. Trail-specific parts are this file's own:
 * trails / tracks / alignment legs as live vector tiles, the detail panel,
 * the D3 profile (tg_profile.js) from GPS elevations or terrain tiles
 * (tg_sample.js), and the trail design panels and editor (tg_design.js,
 * tg_editor.js), which this file hands a small `app` object.
 *
 * URL hash (LSHash grammar, so a view can be pasted between the two sites):
 *   map=z/lat/lon[/bearing/pitch]  base=<id>  li=<dem id>.<h|p>l<opacity%>
 *   and trailgeek's own keys, through the grammar's extras pass-through:
 *   t3d=1  ve=<exaggeration>  sh=<h|s|p>  op=<opacity%>
 *   trail=<slug> | track=<id> | align=<id> | project=<slug>
 * One table, VIEW_PARAMS (near the end of this file), writes and applies it all.
 */
(function () {
  'use strict';

  var CFG = window.TG_CONFIG || {};
  var HOME = { lon: -151.19, lat: 59.62, zoom: 11 };   // Grewingk / Kachemak Bay
  var DEFAULT_BASE = 'usgs-topo';
  var SHADE = { az: 315, alt: 45, hs: 0.8, bl: 3, sl: 0, ve: 1 };   // bl 3 = multiply
  // The view the lidar viewers call "Preset" (landslidescience map.js
  // LIDAR_PRESETS, converged 2026-09-20): mod-5 m elevation bands at full
  // strength, the slope ramp at 0.6, hillshade over the top in hard light
  // (bl 2). Same numbers here so the word means the same thing on both sites.
  var PRESET = { az: 315, alt: 45, hs: 1, bl: 2, sl: 0.6, md: 1, as: 0, ve: 1 };
  var SHADING_CODE = { hillshade: 'h', slope: 's', preset: 'p' };   // the `sh=` key (VIEW_PARAMS)
  var SHADING_MODE = { h: 'hillshade', s: 'slope', p: 'preset' };
  function pctOf(v, dflt) {   // '0'..'100' -> 0..1, else dflt
    var n = parseInt(v, 10);
    return v != null && isFinite(n) ? Math.min(100, Math.max(0, n)) / 100 : dflt;
  }
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
    bearing: hadView ? state.bearing || 0 : 0,   // the camera angle travels with the view (ls_hash.js, 2026-10-17)
    pitch: hadView ? state.pitch || 0 : 0,
    maxPitch: 90,            // 90 = horizontal: a side-on view of a climb (see the view buttons)
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
  // What the link says about the shade, read here because the catalogue
  // (and so the pickers) arrive later: the survey from `li=`, the shading
  // and opacity from trailgeek's `sh=` / `op=`, falling back to the li
  // entry's own code and opacity, which is all a landslidescience link has.
  var liAt = state.li && Object.keys(state.li).length ? state.li[Object.keys(state.li)[0]] : null;
  var explicitDem = liAt ? Object.keys(state.li)[0] : null;
  var shadeOpacity = pctOf(state.extras.op, liAt && liAt.opLeft != null ? liAt.opLeft : 0.6);
  var shadingAtLoad = SHADING_MODE[state.extras.sh] || (liAt && liAt.preset === 'preset' ? 'preset' : 'hillshade');
  // The context under the lidar, as on /lidar/: the baked USGS 3DEP 1/3"
  // context (the catalogue's `ctx_3dep`, z5-z13, over-zoomed above) where
  // its archive is readable from here, and AWS Terrarium beyond it (in place
  // of /lidar/'s live 3DEP service: global, CORS open, no 1-2 s exports).
  // CTX is also the picker's "regional DEM only" value; ctxFill is what the
  // surveys actually composite over.
  var CTX = 'terrarium';
  var ctxFill = CTX, ctxBaked = null;
  var footprints = {};      // id -> outer rings, the survey boundary for fillMode 'outside'

  function registerDems(fc) {
    var ctxReady = Promise.resolve();
    fc.features.forEach(function (f) {
      var p = f.properties;
      if (p.kind !== 'context' || p.encoding !== 'terrarium') return;
      dems[p.id] = p;
      DemShade.instance.addSource(p.id, { tiles: p.tiles_url, encoding: 'terrarium', maxzoom: p.max_zoom, alphaNoData: false })
        .catch(function (e) { console.warn('demshade context', e); });
    });
    if (!dems[CTX]) {
      // Catalogue not imported yet: still give the map some terrain.
      DemShade.instance.addSource(CTX, {
        tiles: 'https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png',
        encoding: 'terrarium', maxzoom: 15, alphaNoData: false
      }).catch(function (e) { console.warn('demshade context', e); });
    }
    fc.features.forEach(function (f) {
      var p = f.properties;
      if (p.kind !== 'context' || p.encoding === 'terrarium') return;
      dems[p.id] = p;
      if (p.encoding === 'imageserver') {
        DemShade.addImageServer(p.id, p.tiles_url, { maxzoom: p.max_zoom });
      } else if (p.pmtiles_url || p.tiles_url) {
        // The baked context. The surveys wait for it, so they are
        // registered over whichever context this origin can actually read.
        ctxReady = probe(p).then(function () {
          DemShade.addDataset(p.id, p.pmtiles_url, LSDemStack.contextOpts(p, CTX));
          p.ready = true;
          ctxFill = ctxBaked = p.id;
          if (!activeDem) syncDem(true, true);
        }, function () { p.unavailable = true; });
      }
    });
    fc.features.forEach(function (f) {
      var p = f.properties;
      if (p.kind === 'context') return;
      dems[p.id] = p;
      lidarIds.push(p.id);
      footprints[p.id] = LSDemStack.outerRings(f.geometry);
      Promise.all([probe(p), ctxReady]).then(function () {
        // One registration per survey, composited over the context so both
        // shading and 3D terrain run to the horizon without a seam at the
        // survey's edge (dem_stack.js, surveyOpts).
        DemShade.addDataset(p.id, p.pmtiles_url, LSDemStack.surveyOpts(p, ctxFill, footprints[p.id]));
        p.ready = true;
      }, function (e) {
        p.unavailable = true;
        if (explicitDem === p.id) explicitDem = null;
      }).then(function () { fillDemPicker(); syncDem(false); });
    });
  }

  // The archives live on landslidescience's R2 bucket, whose CORS allowlist
  // must name this origin (docs/OPERATIONS.md). Each survey is probed with
  // one small request before it is registered with demshade at all: a
  // survey that cannot be read from here is left out of the picker and the
  // auto choice, and never asked for tiles, so the map falls back to the
  // regional DEM with a single warning per survey instead of an error per
  // tile. Resolves when readable, rejects otherwise.
  var corsWarned = false;
  // With both a tile-Worker URL and an archive, a Worker this origin cannot
  // read falls back to the archive (the Worker's CORS list is set separately
  // from the bucket's).
  function probe(p) {
    if (p.tiles_url && p.pmtiles_url) {
      return probeOne(p, true).catch(function () {
        p.tiles_url = null;
        return probeOne(p, false);
      });
    }
    return probeOne(p, !!p.tiles_url);
  }
  function probeOne(p, tiles) {
    var url, init = { mode: 'cors', credentials: 'omit' };
    if (tiles) {
      // A tile at the centre of the survey's bounds. The baked context has
      // no bounds: a z9 tile over Kachemak Bay, which it covers.
      var b = p.bounds || [-151.5, 59.5, -151.0, 59.8];
      var z = p.bounds ? p.min_zoom : Math.max(p.min_zoom, 9), n = Math.pow(2, z), c = [(b[0] + b[2]) / 2, (b[1] + b[3]) / 2];
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
  function shadeSourceId() { return activeDem || ctxFill; }
  function shadeUrl() {
    var mode = $('shading').value, o;
    if (mode === 'preset') o = Object.assign({}, PRESET);
    else {
      o = { az: SHADE.az, alt: SHADE.alt, hs: SHADE.hs, bl: SHADE.bl, ve: SHADE.ve };
      if (mode === 'slope') o.sl = 0.6;
    }
    return DemShade.url(shadeSourceId(), o);
  }
  // Zoom range of what feeds shade and terrain. The context is asked for up
  // to z15 whichever it is (the bake is over-zoomed past its z13).
  function activeZooms() {
    var p = activeDem ? dems[activeDem] : null;
    return p ? { min_zoom: p.min_zoom || 0, max_zoom: p.max_zoom || 15 } : { min_zoom: 0, max_zoom: 15 };
  }
  function activeMaxZoom() { return activeZooms().max_zoom; }
  function contextLabel() {
    return ctxBaked ? 'Regional DEM: USGS 3DEP 1/3″ (baked), AWS Terrain Tiles beyond'
                    : 'Regional DEM (AWS Terrain Tiles)';
  }
  // ctxChanged: the context under everything changed (the baked one became
  // readable), so shade and terrain are rebuilt even if the survey did not.
  function syncDem(force, ctxChanged) {
    var want = explicitDem === CTX ? null : (explicitDem || autoDem());
    if (want && !usable(want)) want = null;
    var changed = want !== activeDem || !!ctxChanged;
    activeDem = want;
    var p = activeDem ? dems[activeDem] : null;
    $('demstatus').textContent = p
      ? p.title + (p.year ? ' (' + p.year + ')' : '') + ' · ' + (p.native_res_m || '?') + ' m' + (p.source ? ' · ' + p.source : '')
      : contextLabel();
    if (!map.getLayer('legs-line')) return;   // overlays not added yet (style still loading)
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
                             attribution: 'Terrain: USGS 3DEP, AWS Terrain Tiles; lidar via landslidescience.org' });
    map.addLayer({ id: 'shade', type: 'raster', source: 'shade', paint: {
      'raster-opacity': shadeOpacity, 'raster-fade-duration': 0, 'raster-opacity-transition': { duration: 0, delay: 0 }
    } }, shadeBelow());
  }
  // The shade sits directly on the basemap imagery: under the basemap's own
  // label tiles (ESRI imagery has them), under every trail and track
  // layer, and under any symbol layer a vector style may have. Anchoring it
  // to the first of those that exists keeps the order the same whether the
  // shade is built before the trails (first load) or after them (a basemap
  // change rebuilds the style, adds the trails, then the shade). Inserting
  // it above the trails, as before, dimmed them by the shade's opacity
  // and hid them entirely at 100 %.
  var SHADE_ABOVE = { labels: 1, 'legs-casing': 1, 'legs-line': 1, tracks: 1, 'trails-casing': 1, 'trails-line': 1, 'selected-halo': 1 };
  function shadeBelow() {
    // The lowest of those layers in the style's current order (the legs
    // layers get moved to the top, so a fixed list would not do).
    var layers = map.getStyle().layers;
    for (var i = 0; i < layers.length; i++) if (SHADE_ABOVE[layers[i].id] || layers[i].type === 'symbol') return layers[i].id;
    return undefined;
  }
  function firstSymbolLayer() {
    var layers = map.getStyle().layers;
    for (var i = 0; i < layers.length; i++) if (layers[i].type === 'symbol') return layers[i].id;
    return undefined;
  }

  // ---- 3D ---------------------------------------------------------------------
  // Terrain, sky, the centre tracker and the white-washout fix come from
  // dem_stack.js, shared with /lidar/.
  var untrack = null;
  var FOG_MIN_PITCH = 20;   // looking straight down there is no far field for fog to soften
  var setSky = LSDemStack.skySwitch(map, {
    'sky-color': '#cfe0ee', 'horizon-color': '#e9eef3', 'fog-color': '#e9eef3',
    'fog-ground-blend': 0.55, 'horizon-fog-blend': 0.9, 'sky-horizon-blend': 0.6, 'atmosphere-blend': 0
  });
  function applySky() { setSky(!!map.getTerrain() && map.getPitch() >= FOG_MIN_PITCH); }
  map.on('pitchend', applySky);
  LSDemStack.floorTerrainMinimum(map, function () {
    var p = activeDem ? dems[activeDem] : null;
    return p && p.z_min != null ? p.z_min : null;
  });
  function rebuildTerrain() {
    var on = $('t3d').checked;
    if (map.getTerrain()) map.setTerrain(null);
    if (map.getSource('dem')) map.removeSource('dem');
    if (!on) { setSky(false); if (untrack) { untrack(); untrack = null; } syncHash(); return; }
    // The 512 mesh (dem_stack.js). minzoom 0 even over lidar: the chain ends
    // in Terrarium, which exists at every zoom.
    map.addSource('dem', LSDemStack.terrainSource(shadeSourceId(), activeZooms(), { minzoom: 0 }));
    map.setTerrain({ source: 'dem', exaggeration: +$('exag').value / 10 });
    applySky();
    if (!untrack && !nearHorizontal) untrack = LSDemStack.trackTerrainCentre(map);
    syncHash();
  }

  // ---- near-horizontal views -------------------------------------------------
  // With terrain on, MapLibre (centerClampedToGround) and demshade's
  // trackTerrainCenter both re-solve the map centre after a move so that it
  // sits on the ground under the view ray, deriving zoom from the camera
  // height / cos(pitch). Near 90 degrees that divides by ~0: the centre is
  // flung towards the horizon, zoom collapses (14 -> ~11.9), the camera can
  // drop to sea level, and every draped line is sized for that far-off
  // centre (the fat-trails bug). So above 80 degrees both are switched off
  // and the camera simply orbits the point it was looking at; below 75 they
  // come back. MapLibre's own guard against a camera inside terrain stays on.
  var nearHorizontal = false;
  function setNearHorizontal(on) {
    if (on === nearHorizontal) return;
    nearHorizontal = on;
    // Order matters: the centre tracker turns clamping off when it attaches
    // and back ON when it detaches, so detach first, then set clamping.
    if (on && untrack) { untrack(); untrack = null; }
    map.setCenterClampedToGround(!on);
    if (!on && map.getTerrain() && !untrack) untrack = LSDemStack.trackTerrainCentre(map);
  }
  map.on('pitch', function () {
    var p = map.getPitch();
    if (p > 80) setNearHorizontal(true);
    else if (p < 75) setNearHorizontal(false);
  });
  if (map.getPitch() > 80) setNearHorizontal(true);   // a pasted side view starts near-horizontal

  // ---- trails, tracks, alignment legs: live MVT ------------------------------
  // Trail classes follow the Kachemak trails map's symbols; width and dash
  // carry the class as well as colour.
  var CLASS_COLOR = ['match', ['get', 'trail_class'], 'major', '#1e5c1e', 'route', '#7a5a2b', 'ski', '#2b6cb0',
                     'sidewalk', '#7d7d7d', 'abandoned', '#8a8a8a', '#2a7f2a'];
  var CLASS_DASH = ['match', ['get', 'trail_class'], 'route', ['literal', [2, 1.5]], 'ski', ['literal', [1, 1.5]],
                    'abandoned', ['literal', [0.5, 1.5]], ['literal', [1, 0]]];
  var CLASS_W = ['match', ['get', 'trail_class'], 'major', 1.6, 'sidewalk', 0.7, 1.0];
  var LEG_COLOR = ['match', ['get', 'kind'], 'existing', TgProfile.LEG_COLORS.existing, 'reroute', TgProfile.LEG_COLORS.reroute,
                   'restore', TgProfile.LEG_COLORS.restore, TgProfile.LEG_COLORS['new']];
  var tileVersion = Date.now();
  function tileUrl() { return location.origin + '/tiles/trails/{z}/{x}/{y}.mvt?v=' + tileVersion; }
  function addTrailLayers() {
    map.addSource('trails', { type: 'vector', tiles: [tileUrl()], minzoom: 5, maxzoom: 18 });
    map.addLayer({ id: 'legs-casing', type: 'line', source: 'trails', 'source-layer': 'legs',
      layout: { 'line-cap': 'round', 'line-join': 'round' },
      paint: { 'line-color': '#ffffff', 'line-width': ['interpolate', ['linear'], ['zoom'], 10, 2.5, 15, 6], 'line-opacity': 0.7 } });
    map.addLayer({ id: 'legs-line', type: 'line', source: 'trails', 'source-layer': 'legs',
      layout: { 'line-cap': 'round', 'line-join': 'round' },
      paint: { 'line-color': LEG_COLOR, 'line-width': ['interpolate', ['linear'], ['zoom'], 10, 1.5, 15, 3.5],
               'line-opacity': ['case', ['==', ['get', 'priority'], 1], 0.95, 0.65],
               'line-dasharray': ['match', ['get', 'kind'], 'reroute', ['literal', [2, 1]], 'restore', ['literal', [3, 1, 0.5, 1]], ['literal', [1, 0]]] } });
    map.addLayer({ id: 'tracks', type: 'line', source: 'trails', 'source-layer': 'tracks',
      layout: { 'line-cap': 'round', 'line-join': 'round' },
      paint: { 'line-color': '#b5367d', 'line-width': ['interpolate', ['linear'], ['zoom'], 10, 1.2, 15, 2.5], 'line-opacity': 0.85 } }, 'legs-casing');
    map.addLayer({ id: 'trails-casing', type: 'line', source: 'trails', 'source-layer': 'trails',
      layout: { 'line-cap': 'round', 'line-join': 'round' },
      paint: { 'line-color': '#ffffff', 'line-width': ['interpolate', ['linear'], ['zoom'], 10, ['*', 3, CLASS_W], 15, ['*', 6, CLASS_W]], 'line-opacity': 0.6 } }, 'tracks');
    map.addLayer({ id: 'trails-line', type: 'line', source: 'trails', 'source-layer': 'trails',
      layout: { 'line-cap': 'round', 'line-join': 'round' },
      paint: { 'line-color': CLASS_COLOR,
               'line-width': ['interpolate', ['linear'], ['zoom'], 10, ['*', 1.5, CLASS_W], 15, ['*', 3.5, CLASS_W]],
               'line-dasharray': CLASS_DASH } }, 'tracks');
    map.addSource('selected', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
    map.addLayer({ id: 'selected-halo', type: 'line', source: 'selected',
      layout: { 'line-cap': 'round', 'line-join': 'round' },
      paint: { 'line-color': '#ffd400', 'line-width': 9, 'line-opacity': 0.55, 'line-blur': 2 } }, 'trails-casing');
    map.addSource('hl', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
    map.addLayer({ id: 'hl', type: 'line', source: 'hl', layout: { 'line-cap': 'round', 'line-join': 'round' },
      paint: { 'line-color': '#00e5ff', 'line-width': 7, 'line-opacity': 0.8 } });
    map.addSource('cursor', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
    map.addLayer({ id: 'cursor', type: 'circle', source: 'cursor',
      paint: { 'circle-radius': 6, 'circle-color': '#ffd400', 'circle-stroke-color': '#1f2a1f', 'circle-stroke-width': 2,
               'circle-pitch-scale': 'viewport' } });
  }
  function refreshTiles() {
    tileVersion = Date.now();
    if (map.getSource('trails')) map.getSource('trails').setTiles([tileUrl()]);
  }

  var CLICKABLE = ['trails-line', 'tracks', 'legs-line'];
  map.on('click', function (e) {
    if (window.TgDesign && TgDesign.editing()) return;      // the editor owns clicks
    var fs = map.queryRenderedFeatures(e.point, { layers: CLICKABLE });
    if (!fs.length) return;
    // Prefer an alignment leg over the trail under it: it is on top.
    fs.sort(function (a, b) { return CLICKABLE.indexOf(b.layer.id) - CLICKABLE.indexOf(a.layer.id); });
    var f = fs[0];
    if (f.layer.id === 'trails-line') select('trail', f.properties.slug, false);
    else if (f.layer.id === 'tracks') select('track', f.properties.id, false);
    else select('align', f.properties.id, false);
  });
  map.on('mousemove', function (e) {
    if (window.TgDesign && TgDesign.editing()) return;
    var hit = map.queryRenderedFeatures(e.point, { layers: CLICKABLE }).length > 0;
    map.getCanvas().style.cursor = hit ? 'pointer' : '';
  });

  // ---- selection + detail panel --------------------------------------------
  var selected = null;      // { kind, key }
  var profile = TgProfile.create('#profile', { onHover: function (s) {
    setSourceData('cursor', { type: 'FeatureCollection', features: s ? [{ type: 'Feature', geometry: { type: 'Point', coordinates: [s.lon, s.lat] }, properties: {} }] : [] });
  } });
  var lastFeature = null, lastSamples = null;

  // Selection can arrive (from the URL hash) before the overlay sources
  // exist; addOverlays() re-applies lastFeature once they do.
  function setSourceData(id, data) { var src = map.getSource(id); if (src) src.setData(data); }
  function setSelectedData(data) { setSourceData('selected', data); }

  function apiUrl(kind, key) {
    return kind === 'trail' ? '/api/trails/' + encodeURIComponent(key) + '.geojson'
         : kind === 'track' ? '/api/tracks/' + key + '.geojson'
         : kind === 'project' ? '/api/projects/' + encodeURIComponent(key) + '.json'
         : '/api/alignments/' + key + '.geojson';
  }
  function select(kind, key, fit) {
    if (window.TgDesign && !TgDesign.leave()) return;
    selected = { kind: kind, key: String(key) };
    syncHash();
    $('panel').classList.add('open');
    $('panel').classList.toggle('tg-wide', kind === 'project');
    $('detail').innerHTML = '<p class="tg-muted">Loading…</p>';
    $('profstats').innerHTML = '';
    profile.clear();
    fetch(apiUrl(kind, key), { credentials: 'same-origin' }).then(function (r) {
      if (!r.ok) throw new Error(r.status === 404 ? 'Not found, or not visible to you.' : 'Error ' + r.status);
      return r.json();
    }).then(function (f) {
      if (kind === 'project') {
        lastFeature = null;
        setSelectedData({ type: 'FeatureCollection', features: [] });
        TgDesign.showProject(f, fit);
        syncProjectPicker();
        return;
      }
      lastFeature = f;
      syncProjectPicker();
      setSelectedData(f.geometry ? f : { type: 'FeatureCollection', features: [] });
      if (fit && f.geometry) map.fitBounds(bboxOf(f.geometry), { padding: 60, maxZoom: 15, duration: 0 });
      if (kind === 'align') { TgDesign.showAlignment(f); return; }
      renderDetail(f);
      loadProfile(f, f.properties.has_elevation ? 'gps' : 'terrain');
    }).catch(function (e) {
      $('detail').innerHTML = '<p class="tg-error">' + esc(e.message) + '</p>';
    });
  }
  function clearSelection() {
    if (window.TgDesign && !TgDesign.leave()) return;
    selected = null; lastFeature = null;
    syncProjectPicker();
    setSelectedData({ type: 'FeatureCollection', features: [] });
    setSourceData('cursor', { type: 'FeatureCollection', features: [] });
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
    if (p.project) h += '<div class="tg-muted">Project: <a href="#project=' + esc(p.project.slug) + '">' + esc(p.project.name) + '</a>' + (p.trailhead ? ' · from ' + esc(p.trailhead) : '') + '</div>';
    if (p.trail) h += '<div class="tg-muted">On trail: <a href="#trail=' + esc(p.trail.slug) + '">' + esc(p.trail.name) + '</a></div>';
    if (p.taken_at) h += '<div class="tg-muted">Recorded ' + esc(p.taken_at.slice(0, 10)) + (p.device ? ' · ' + esc(p.device) : '') + '</div>';
    if (p.trail_class_label) h += '<div class="tg-muted">' + esc(p.trail_class_label) + (p.builder ? ' · built by ' + esc(p.builder) : '') + '</div>';
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
    links.push('<a href="#" id="sideview" title="Look at it (nearly) horizontally, from the side, to see how steady the climb is">Side view</a>');
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
    $('sideview').addEventListener('click', function (ev) { ev.preventDefault(); sideViewOf(f.geometry); });
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
  // ---- project picker ---------------------------------------------------------
  // Lists every project the visitor may see, so a public project is findable
  // without its link. Follows the selection: the project of a selected
  // alignment is shown too.
  function fillProjects() {
    var sel = $('projects');
    fetch('/api/projects/', { credentials: 'same-origin' }).then(function (r) { return r.json(); }).then(function (r) {
      var h = '<option value="">Open a project…</option>';
      r.projects.forEach(function (p) {
        h += '<option value="' + esc(p.slug) + '">' + esc(p.name) + (p.visibility !== 'public' ? ' (' + esc(p.visibility) + ')' : '') +
             (p.alignments ? ' · ' + p.alignments : '') + '</option>';
      });
      sel.innerHTML = h;
      syncProjectPicker();
    }).catch(function () {});
    sel.addEventListener('change', function () { if (sel.value) select('project', sel.value, true); });
  }
  function syncProjectPicker() {
    var sel = $('projects'), slug = '';
    if (selected && selected.kind === 'project') slug = selected.key;
    else if (lastFeature && lastFeature.properties && lastFeature.properties.project) slug = lastFeature.properties.project.slug || '';
    if (sel.querySelector('option[value="' + slug.replace(/"/g, '') + '"]')) sel.value = slug; else sel.value = '';
  }
  function fillBasemaps() {
    var sel = $('base');
    sel.innerHTML = basemaps.map(function (b) { return '<option value="' + b.id + '">' + esc(b.label) + '</option>'; }).join('');
    sel.value = baseId;
    sel.addEventListener('change', function () { setBasemap(sel.value); });
  }
  function setBasemap(id) {
    baseId = id;
    var terrainOn = $('t3d').checked;
    // Listen before setStyle: the basemap styles are inline objects, and
    // MapLibre fires style.load for those synchronously inside setStyle. A
    // listener registered after the call missed it, so the trails, shade
    // and selection only came back one basemap change late (when the stale
    // listener caught the next style load).
    map.once('style.load', function () {
      baseWidth = {};                         // the layers come back unscaled
      setSky(false);                          // a new style has no sky, whatever the switch last set
      addOverlays(); if (terrainOn) rebuildTerrain(); if (window.TgDesign) TgDesign.onStyleReload();
      rescaleLines(true);
    });
    map.setStyle(LSBasemaps.styleFor(baseOf(id)));
    syncHash();
  }
  function addOverlays() {
    addTrailLayers();
    if (lastFeature) setSelectedData(lastFeature);
    syncDem(true);
  }

  // ---- line widths under pitch ------------------------------------------------
  // MapLibre drapes a line on the ground as a stripe whose width in metres is
  // fixed per frame from the scale at the map CENTRE (width px x metres per
  // px there). Looking down that is what you want. Tilted towards the
  // horizon, the centre slides far away, the map zoom drops (14 -> ~12 at
  // 85 degrees) and a 2-3 px trail becomes a ~40 m stripe, which the nearer
  // ground in view then magnifies: the "trails go fat when nearly
  // horizontal" bug. So when pitched, widths are scaled down by how much
  // nearer the ground in view (sampled below the centre of the screen) is
  // than the far-off centre, square-rooted as a compromise between near and
  // far. Recomputed on every moveend. `window.tgLineScale = false` in the
  // console turns it off for comparison.
  var SCALED_LINES = ['legs-casing', 'legs-line', 'tracks', 'trails-casing', 'trails-line', 'selected-halo', 'hl',
                      'ed-legs-casing', 'ed-legs-line', 'ed-rubber'];
  var baseWidth = {};         // layer id -> its own line-width, captured before scaling
  var lineScale = 1;
  function groundMetresPerPx(x, y) {
    var a = map.unproject([x - 8, y]), b = map.unproject([x + 8, y]);
    if (!a || !b) return null;
    var m = TgSample.metres([a.lng, a.lat], [b.lng, b.lat]) / 16;
    return isFinite(m) && m > 0 ? m : null;
  }
  function computeLineScale() {
    if (window.tgLineScale === false) return 1;     // console switch, for comparing
    if (map.getPitch() < 40) return 1;
    var c = map.getCanvas(), w = c.clientWidth, h = c.clientHeight;
    var atCentre = groundMetresPerPx(w / 2, h / 2);
    if (!atCentre) return 1;
    // The ground people look at when tilted: the lower middle of the view.
    var ratios = [0.6, 0.7, 0.8].map(function (f) {
      var m = groundMetresPerPx(w / 2, h * f);
      return m ? m / atCentre : null;
    }).filter(function (r) { return r !== null && r < 1; }).sort();
    if (!ratios.length) return 1;
    var r = ratios[Math.floor(ratios.length / 2)];
    // Full correction (r) makes the near ground right but thins trails at
    // mid distance to nothing; the square root splits the difference (checked
    // side-on at 80-90 degrees over the Grewingk trails).
    return Math.max(0.25, Math.min(1, Math.sqrt(r)));
  }
  // A zoom expression must stay at the top level of a style value, so the
  // factor goes inside interpolate / step outputs rather than around them.
  function scaleWidth(expr, f) {
    if (typeof expr === 'number') return expr * f;
    if (Array.isArray(expr) && (expr[0] === 'interpolate' || expr[0] === 'step')) {
      // interpolate: [op, type, input, stop, out, stop, out...] (outputs from 4)
      // step:        [op, input, out, stop, out...]            (outputs 2, 4, ...)
      var out = expr.slice();
      if (expr[0] === 'step') out[2] = ['*', f, expr[2]];
      for (var i = 4; i < out.length; i += 2) out[i] = ['*', f, expr[i]];
      return out;
    }
    return ['*', f, expr];
  }
  function rescaleLines(force) {
    var f = computeLineScale();
    if (!force && Math.abs(f - lineScale) < 0.02) return;
    lineScale = f;
    SCALED_LINES.forEach(function (id) {
      if (!map.getLayer(id)) return;
      if (!(id in baseWidth)) baseWidth[id] = map.getPaintProperty(id, 'line-width');
      var base = baseWidth[id];
      if (base === undefined) return;
      map.setPaintProperty(id, 'line-width', f >= 0.99 ? base : scaleWidth(base, f));
    });
  }
  map.on('moveend', function () { rescaleLines(false); });

  // ---- views: top, oblique, and side-on ------------------------------------
  // Side-on means looking (nearly) horizontally at a slope to judge how
  // steady a climb is. Just tilting to 90 degrees puts the camera at the
  // height of the point it looks at, so any hill in between swallows it.
  // Instead the camera is placed explicitly (MapLibre's
  // calculateCameraOptionsFromTo): off to one side of the target, and lifted
  // just enough that the line of sight clears the ground, sampled from the
  // terrain tiles along the way (tg_sample.js, so it does not depend on which
  // tiles happen to be loaded). The result is as close to horizontal as the
  // terrain allows; the user can still tilt all the way to 90.
  var SIDE_CLEARANCE_M = 25;
  function ensureTerrain() {
    if ($('t3d').checked) return;
    $('t3d').checked = true; $('exag').disabled = false; rebuildTerrain();
  }
  function offset(ll, bearingDeg, dist) {      // point `dist` metres from ll along a bearing
    var R = 6371008.8, b = bearingDeg * Math.PI / 180, lat = ll[1] * Math.PI / 180;
    return [ll[0] + dist * Math.sin(b) / (R * Math.cos(lat)) * 180 / Math.PI,
            ll[1] + dist * Math.cos(b) / R * 180 / Math.PI];
  }
  function bearingOf(a, b) {
    var lat = (a[1] + b[1]) / 2 * Math.PI / 180;
    return Math.atan2((b[0] - a[0]) * Math.cos(lat), b[1] - a[1]) * 180 / Math.PI;
  }
  // Camera for looking at `target` from bearing `from` (degrees, the
  // direction the camera sits in, seen from the target) at `dist` metres.
  // Resolves to {options, altitude} or null.
  function sideCamera(target, from, dist) {
    var cam = offset(target, from, dist);
    return TgSample.profile([cam, target], { spacing: Math.max(10, dist / 60) }).then(function (s) {
      var ex = map.getTerrain() ? map.getTerrain().exaggeration || 1 : +$('exag').value / 10;
      var zT = s[s.length - 1].z * ex, need = s[0].z * ex + SIDE_CLEARANCE_M;
      s.forEach(function (p) {
        var t = p.d / dist;
        if (t <= 0 || t >= 0.97) return;
        // The sight line from camera (t=0) to target (t=1) must pass above
        // this ground point: alt + (zT - alt) t >= ground + clearance.
        need = Math.max(need, (p.z * ex + SIDE_CLEARANCE_M - zT * t) / (1 - t));
      });
      var alt = Math.max(need, zT + 1);
      return { options: map.calculateCameraOptionsFromTo(cam, alt, target, zT), altitude: alt - zT,
               cam: cam, camAlt: alt, groundAtCam: s[0].z * ex };
    });
  }
  function sideView(target, lineBearing, dist) {
    ensureTerrain();
    // A line is seen from across its direction (either side); the map centre
    // from behind the current view, or a little either side of it. Each at
    // three distances: nearer can look over less terrain.
    var bearings = lineBearing == null ? [0, 25, -25].map(function (d) { return map.getBearing() + 180 + d; })
                                       : [lineBearing + 90, lineBearing - 90];
    var tries = [];
    bearings.forEach(function (b) { [0.6, 1, 1.5].forEach(function (k) { tries.push([b, dist * k]); }); });
    Promise.all(tries.map(function (t) {
      return sideCamera(target, t[0], t[1]).then(function (c) { c.dist = t[1]; return c; }, function () { return null; });
    })).then(function (cams) {
      // Closest to horizontal wins (angle of the camera above the target);
      // ties go to the more distant, wider view.
      cams = cams.filter(Boolean).sort(function (a, b) {
        var da = Math.atan2(a.altitude, a.dist), db = Math.atan2(b.altitude, b.dist);
        return Math.abs(da - db) > 0.01 ? da - db : b.dist - a.dist;
      });
      if (!cams.length) { map.easeTo({ pitch: 85, duration: 800 }); return; }
      setNearHorizontal(true);                 // keep the placement: no re-solving of the centre
      // jumpTo, not easeTo: only jumpTo honours `elevation` (the height of
      // the point looked at); easeTo re-derives it from the terrain tiles.
      map.jumpTo(cams[0].options);
      window.tgLastSideView = cams[0];         // for console debugging
    });
  }
  // Side-on view of a line: from across its overall direction, far enough to
  // take in all of it.
  function sideViewOf(geom) {
    var cs = flatCoords(geom).filter(function (c) { return c && isFinite(c[0]); });
    if (cs.length < 2) return;
    var b = bboxOf(geom), mid = [(b[0] + b[2]) / 2, (b[1] + b[3]) / 2];
    var span = TgSample.metres([b[0], b[1]], [b[2], b[3]]);
    sideView(mid, bearingOf(cs[0], cs[cs.length - 1]), Math.max(700, span * 1.1));
  }
  function setView(which) {
    if (which === 'top') { map.easeTo({ pitch: 0, duration: 800 }); return; }
    if (which === 'oblique') { ensureTerrain(); map.easeTo({ pitch: 60, duration: 800 }); return; }
    // Side-on: the selected line if there is one, else what is at the centre.
    if (lastFeature && lastFeature.geometry) { sideViewOf(lastFeature.geometry); return; }
    var c = map.getCenter(), mpp = 40075016.686 * Math.cos(c.lat * Math.PI / 180) / (512 * Math.pow(2, map.getZoom()));
    sideView([c.lng, c.lat], null, Math.min(2500, Math.max(700, mpp * map.getCanvas().clientWidth * 0.5)));
  }
  Array.prototype.forEach.call(document.querySelectorAll('[data-view]'), function (b) {
    b.addEventListener('click', function () { setView(b.dataset.view); });
  });

  // ---- view state in the URL: one table -------------------------------------
  // Every piece of state that travels in the hash is one row here, and the
  // writer (syncHash) and the applier (applyState: a hashchange, or the cold
  // load once the catalogue is in) are loops over it. So a pasted link, a
  // hand-edited URL and back/forward all do the same thing, and new state is
  // a new row, never a line in a writer that the reader then lacks (which is
  // how the opacity came to be stranded inside `li=`, lost in Auto mode). The
  // shape is landslidescience map.js's _VIEW_PARAMS (2026-10-06), kept the
  // same so a lesson learned on one site ports to the other.
  //
  //   collect(o)      put the current value on the codec object (LSHash.encode's
  //                   shape; trailgeek-only keys go in o.extras as strings)
  //   apply(s, how)   put the parsed state on the map. how is 'hash' or 'cold'.
  //                   A key absent from s is left alone, except where the key
  //                   describes the whole thing: `map=` is the whole camera
  //                   (MapLibre's rule: no bearing/pitch means 0) and `t3d`
  //                   absent means 3D off, or a link could never turn it off.
  //   cold            true = applied by applyState(state, 'cold') in map.on('load');
  //                   a string names the earlier reader, for state needed while
  //                   the map is built or before the catalogue arrives.
  //
  // trailgeek's own keys, through the shared grammar's extras pass-through
  // (CLAUDE.md "URL hash" lists them beside landslidescience's so the two
  // sites never claim one key twice):
  //   t3d=1      3D terrain on               ve=<n>    vertical exaggeration (1.0 = true), with t3d
  //   sh=h|s|p   hillshade | + slope ramp | preset      op=<pct>  hillshade opacity
  //   trail=<slug> | track=<id> | align=<id> | project=<slug>   the selection
  var SELECTION_KINDS = ['trail', 'track', 'align', 'project'];
  function cameraNow() {
    var c = map.getCenter();
    return { zoom: map.getZoom(), lat: c.lat, lon: c.lng, bearing: map.getBearing(), pitch: map.getPitch() };
  }
  function cameraString(o) {   // the camera as the codec writes it: equal strings = same view
    return LSHash.encode({ zoom: o.zoom, lat: o.lat, lon: o.lon, bearing: o.bearing, pitch: o.pitch });
  }
  function liEntry(s) { return s.li && Object.keys(s.li).length ? s.li[Object.keys(s.li)[0]] : null; }
  var VIEW_PARAMS = [
    { key: 'map', cold: 'the map constructor; setNearHorizontal for a pitch above 80',
      collect: function (o) { Object.assign(o, cameraNow()); },
      apply: function (s) {
        if (s.zoom == null) return;
        if (cameraString(s) === cameraString(cameraNow())) return;
        map.jumpTo({ center: [s.lon, s.lat], zoom: s.zoom, bearing: s.bearing || 0, pitch: s.pitch || 0 });
      } },
    { key: 'base', cold: 'the map constructor (baseId)',
      // Always written, default included: if the default basemap ever
      // changes, old links keep the one they were taken with.
      collect: function (o) { o.base = baseId; },
      apply: function (s) {
        if (!s.base || s.base === baseId) return;
        if (!basemaps.some(function (b) { return b.id === s.base; })) return;
        $('base').value = s.base;
        setBasemap(s.base);
      } },
    { key: 'li', cold: 'explicitDem, read before the catalogue arrives',
      // The survey chosen by hand, with the shading code and opacity the
      // shared grammar gives it, so a landslidescience page reads the same
      // survey the same way. Auto mode (finest survey under the centre)
      // writes nothing: it follows the view, which the link already carries.
      collect: function (o) {
        if (!explicitDem) return;
        o.li = {};
        o.li[explicitDem] = { preset: $('shading').value === 'preset' ? 'preset' : 'hillshade', left: true, opLeft: shadeOpacity };
      },
      apply: function (s) {
        if (!s.li) return;                       // absent: leave the survey alone
        var id = Object.keys(s.li)[0] || null;   // present but empty: back to Auto
        if (id === explicitDem) return;
        explicitDem = id;
        $('dem').value = id || '';
        syncDem(true);
      } },
    { key: 'sh', cold: 'shadingAtLoad',
      collect: function (o) { o.extras.sh = SHADING_CODE[$('shading').value] || 'h'; },
      apply: function (s) {
        var e = liEntry(s);
        var mode = SHADING_MODE[s.extras.sh] || (e && s.extras.sh == null ? (e.preset === 'preset' ? 'preset' : 'hillshade') : null);
        if (!mode || mode === $('shading').value) return;
        $('shading').value = mode;
        if (map.getLayer('shade')) rebuildShade();
      } },
    { key: 'op', cold: 'shadeOpacity',
      collect: function (o) { o.extras.op = String(Math.round(shadeOpacity * 100)); },
      apply: function (s) {
        var e = liEntry(s);
        var v = pctOf(s.extras.op, e && e.opLeft != null ? e.opLeft : null);
        if (v == null || v === shadeOpacity) return;
        shadeOpacity = v;
        $('shade-op').value = Math.round(v * 100); showShadeOp();
        if (map.getLayer('shade')) map.setPaintProperty('shade', 'raster-opacity', v);
      } },
    { key: 't3d', cold: true,
      collect: function (o) {
        if (!$('t3d').checked) return;
        o.extras.t3d = '1';
        o.extras.ve = (+$('exag').value / 10).toFixed(1);
      },
      apply: function (s) {
        var on = s.extras.t3d === '1', ve = parseFloat(s.extras.ve), ex = $('exag');
        var slider = isFinite(ve) ? Math.round(Math.min(+ex.max, Math.max(+ex.min, ve * 10))) : +ex.value;
        var change = on !== $('t3d').checked || slider !== +ex.value;
        ex.value = slider; showExag();
        $('t3d').checked = on; ex.disabled = !on;
        if (change || (on && !map.getTerrain())) rebuildTerrain();
      } },
    { key: 'selection', cold: true,
      collect: function (o) { if (selected) o.extras[selected.kind] = selected.key; },
      apply: function (s, how) {
        var kind = SELECTION_KINDS.filter(function (k) { return s.extras[k]; })[0];
        if (kind) {
          if (!selected || selected.kind !== kind || selected.key !== String(s.extras[kind])) {
            select(kind, s.extras[kind], s.zoom == null);   // fit to it only when the link has no view of its own
          }
        } else if (selected && how === 'hash') {
          clearSelection();
        }
      } }
  ];
  var hashLock = false;
  function syncHash() {
    var o = { extras: {} };
    VIEW_PARAMS.forEach(function (r) { r.collect(o); });
    var h = LSHash.encode(o);
    if (location.hash === h) return;
    hashLock = true;
    history.replaceState(null, '', h);
    hashLock = false;
  }
  // Row order is apply order. The parsed state is applied as a snapshot: a
  // row's side effects (a moveend, a style load) call syncHash and rewrite
  // the address bar, but cannot change what the later rows see. At the end
  // the address bar is normalised to what this page writes (a 4 dp link, a
  // missing base=); an untouched page is left without a hash until it moves.
  function applyState(s, how) {
    VIEW_PARAMS.forEach(function (r) { if (how !== 'cold' || r.cold === true) r.apply(s, how); });
    if (how !== 'cold' || location.hash) syncHash();
  }
  window.addEventListener('hashchange', function () {
    if (hashLock) return;
    applyState(LSHash.parse(location.hash), 'hash');
  });
  map.on('moveend', function () { fillDemPicker(); syncDem(false); syncHash(); });

  // ---- controls --------------------------------------------------------------
  $('dem').addEventListener('change', function () { explicitDem = $('dem').value || null; syncDem(true); syncHash(); });
  $('shade-op').value = Math.round(shadeOpacity * 100);
  $('shade-op').addEventListener('input', function () {
    shadeOpacity = +$('shade-op').value / 100;
    showShadeOp();
    if (map.getLayer('shade')) map.setPaintProperty('shade', 'raster-opacity', shadeOpacity);
    syncHash();
  });
  $('shading').value = shadingAtLoad;
  $('shading').addEventListener('change', function () { if (map.getLayer('shade')) rebuildShade(); syncHash(); });
  $('t3d').addEventListener('change', function () { $('exag').disabled = !$('t3d').checked; rebuildTerrain(); });
  $('exag').disabled = !$('t3d').checked;
  // The sliders show their value, so "is it stretched?" has an answer:
  // 1.0× is true vertical scale.
  function showExag() { $('exag-val').textContent = (+$('exag').value / 10).toFixed(1) + '×'; }
  function showShadeOp() { $('shade-op-val').textContent = $('shade-op').value + ' %'; }
  showExag(); showShadeOp();
  $('exag').addEventListener('input', function () {
    showExag();
    if (map.getTerrain()) map.setTerrain({ source: 'dem', exaggeration: +$('exag').value / 10 });
  });
  $('layers-toggle').addEventListener('click', function () { $('layers').classList.toggle('open'); });
  var na = $('new-alignment');
  if (na) na.addEventListener('click', function (e) { e.preventDefault(); if (TgDesign.leave()) TgDesign.newAlignment(null); });

  function esc(t) {
    return String(t == null ? '' : t).replace(/[&<>"]/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]; });
  }

  // ---- the hooks tg_design.js uses ------------------------------------------
  function setGeo(src, geom) {
    if (map.getSource(src)) map.getSource(src).setData(geom ? { type: 'Feature', properties: {}, geometry: geom.geometry || geom } : { type: 'FeatureCollection', features: [] });
  }
  TgDesign.init({
    map: map, profile: profile, esc: esc,
    select: select, clearSelection: clearSelection, refreshTiles: refreshTiles,
    setSelected: function (kind, key) { selected = { kind: kind, key: String(key) }; syncHash(); },
    setSelectedGeometry: function (geom) { setGeo('selected', geom); },
    highlight: function (f) { setGeo('hl', f); },
    fitTo: function (geom, bbox) { map.fitBounds(bbox || bboxOf(geom), { padding: 60, maxZoom: 16 }); },
    quickProfile: function (f) { if (f.geometry) loadProfile(f, 'terrain'); },
    rescaleLines: function () { rescaleLines(true); },
    sideView: sideViewOf
  });

  // ---- boot -------------------------------------------------------------------
  fillBasemaps();
  fillProjects();
  map.on('load', function () {
    fetch('/api/dems.geojson', { credentials: 'same-origin' })
      .then(function (r) { return r.json(); })
      .catch(function () { return { type: 'FeatureCollection', features: [] }; })
      .then(function (fc) {
        registerDems(fc);
        addOverlays();
        fillDemPicker();
        applyState(state, 'cold');   // 3D terrain and the selection; the rest was read at the top
      });
  });
})();
