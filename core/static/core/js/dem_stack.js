/* dem_stack.js — the lidar + context DEM stack that /lidar/ (landslidescience)
 * and trailgeek's home map both build on maplibre-gl-demshade, through
 * dem_shade_bridge.js. No DOM, no page state: each page keeps its own
 * controls and passes plain values in.
 *
 * Status (2026-09-28): written in trailgeek as the candidate shared module,
 * from the logic inline in landslidescience's
 * pages/templates/pages/lidar_preview.html @ 1c63e60. Its intended home is
 * landslidescience inventory/static/inventory/js/dem_stack.js, next to the
 * bridge; once /lidar/ loads it from there, trailgeek syncs it back like the
 * other shared files (tools/shared.json) and this note goes. Until then,
 * change it here and in /lidar/ together (docs/SISTER_PROJECTS.md).
 *
 *   LSDemStack.outerRings(geometry)             // survey boundary for fillMode 'outside'
 *   LSDemStack.contextOpts(ctx, beyond)         // addDataset opts, baked context
 *   LSDemStack.surveyOpts(p, fill, footprint)   // addDataset opts, a survey over context
 *   LSDemStack.terrainSource(id, p, opts)       // raster-dem source spec (512 mesh)
 *   LSDemStack.trackTerrainCentre(map, opts)    // centre on the ground; returns detach
 *   LSDemStack.relativeThreshold(fraction, min) // threshold scaled to camera height
 *   LSDemStack.floorTerrainMinimum(map, zMin)   // the white-washout fix
 *   LSDemStack.skySwitch(map, sky)              // setSky only on a change
 */
window.LSDemStack = (function () {
  'use strict';
  var DS = window.DemShade;

  // The footprint's OUTER rings: a survey's boundary, holes and all inside it.
  function outerRings(geometry) {
    if (!geometry) return null;
    var polys = geometry.type === 'MultiPolygon' ? geometry.coordinates
              : geometry.type === 'Polygon' ? [geometry.coordinates] : [];
    var rings = polys.map(function (poly) { return poly[0]; }).filter(Boolean);
    return rings.length ? rings : null;
  }

  // The baked context (the catalog's top-level "context": USGS 3DEP
  // 1/3 arc-second, z5-z13, bake_context.py) is over-zoomed above its
  // max_zoom. `beyond` names an already-registered source for ground the
  // bake does not cover (the live 3DEP ImageServer on /lidar/, Terrarium on
  // trailgeek), or nothing.
  function contextOpts(ctx, beyond) {
    var extra = { overzoom: true };
    if (beyond) extra.fill = beyond;
    return DS.catalogOpts(ctx, extra);
  }

  // A survey composited over the context. fillMode 'outside': the context
  // supplies ground outside the survey's BOUNDARY (its footprint's outer
  // rings) and nothing inside it. The older 'missing' drew that line at tile
  // edges instead, so the last partial tile along the survey's edge fell to
  // a 0 m shelf before the context resumed. Patching a 10 m regional DEM into
  // a hole in a 1 m coastal survey invents a surface out of whatever that DEM
  // believed the water was, which is worse than an honest void (Hig,
  // 2026-09-14), so holes inside the boundary stay holes. A catalog
  // fill_mode (e.g. 'holes' for an SfM surface) still overrides.
  function surveyOpts(p, fill, footprint) {
    return DS.catalogOpts(p, { fill: fill, fillMode: p.fill_mode || 'outside', footprint: footprint || null });
  }

  // raster-dem source for 3D terrain through demshade's passthrough (always
  // Mapbox terrain-RGB). size 512 (the default): each terrain tile is the
  // four z+1 tiles assembled (posts the shading already decoded, so no extra
  // fetch), and MapLibre builds a quarter as many terrain meshes; maxzoom is
  // one less, since z+1 must exist. opts.minzoom: the context's, when the
  // survey is composited over one (below it there is nothing to ask for).
  function terrainSource(id, p, opts) {
    opts = opts || {};
    var size = opts.size === 256 ? 256 : 512;
    var minz = opts.minzoom != null ? opts.minzoom : (p.min_zoom || 0);
    var maxz = p.max_zoom != null ? p.max_zoom : 15;
    return {
      type: 'raster-dem', tiles: [DS.demUrl(id, size)], encoding: 'mapbox', tileSize: size,
      minzoom: minz, maxzoom: size === 512 ? Math.max(minz, maxz - 1) : maxz
    };
  }

  // Camera height above the map centre, metres. MapLibre 5 has no
  // getFreeCameraOptions (demshade's relativeThreshold feature-checks for it
  // and so always falls back to its minimum); the transform does the sum.
  function cameraHeight(map) {
    var tr = map.transform;
    if (!tr || typeof tr.getCameraAltitude !== 'function') return NaN;
    return tr.getCameraAltitude() - map.getCenterElevation();
  }

  // Threshold scaled to the camera's height: `fraction` of it, never under
  // `min` metres. A fixed 0.25 m re-solved after nearly every gesture over
  // rough ground, and each re-solve re-chooses every source's tiles (the
  // settle-and-reload hitch after each drag).
  function relativeThreshold(fraction, min) {
    fraction = fraction == null ? 0.02 : fraction;
    min = min == null ? 0.5 : min;
    return function (map) {
      var h = cameraHeight(map);
      return isFinite(h) ? Math.max(min, Math.abs(h) * fraction) : min;
    };
  }

  // Keep the map centre on the terrain surface after each gesture and once
  // tiles settle, rather than MapLibre's per-frame re-clamping (the view
  // lurches as sharper DEM tiles stream in) or none (the centre sits at sea
  // level: tiles chosen for the wrong distance, rotation about a point in
  // the air). demshade's trackTerrainCenter (terrain-center.ts) with three
  // fixes to take upstream (core/static/core/vendor/VENDOR.md):
  //   1. detaching also cancels an already-scheduled "idle" re-solve, which
  //      otherwise fires after detach and moves the camera;
  //   2. no re-solve above opts.maxPitch (80): it derives zoom from
  //      height / cos(pitch), which degenerates near horizontal (zoom
  //      collapses, the centre flies to the horizon);
  //   3. the default threshold is relativeThreshold(0.02, 0.5), computed in
  //      a way that works on MapLibre 5.
  function trackTerrainCentre(map, opts) {
    opts = opts || {};
    var maxPitch = opts.maxPitch != null ? opts.maxPitch : 80;
    var thr = opts.threshold != null ? opts.threshold : relativeThreshold(0.02, 0.5);
    var busy = false, waiting = false, alive = true;
    var priv = typeof map._getTransformForUpdate === 'function' && typeof map._applyUpdatedTransform === 'function';
    function threshold() {
      if (typeof thr !== 'function') return thr;
      var v = thr(map);
      return isFinite(v) && v > 0 ? v : 0.25;
    }
    function settle() {
      if (!alive || busy || !map.getTerrain() || map.getPitch() > maxPitch) return;
      var u = map.queryTerrainElevation(map.getCenter());
      if (u == null || !isFinite(u) || Math.abs(u - map.getCenterElevation()) < threshold()) return;
      busy = true;
      try {
        if (priv) {
          // The pair MapLibre's handler manager uses when a terrain drag
          // ends: the camera stays put, centre/zoom/elevation are re-solved.
          var tr = map._getTransformForUpdate();
          tr.recalculateZoomAndCenter(map.terrain);
          map._applyUpdatedTransform(tr);
          // _update() marks every source cache dirty so tiles are re-chosen
          // for the new zoom; a bare repaint kept the old tile set.
          if (typeof map._update === 'function') map._update(); else map.triggerRepaint();
        } else {
          // easeTo ignores `elevation` in MapLibre 5; jumpTo honours it.
          map.jumpTo({ elevation: u });
        }
      } finally { setTimeout(function () { busy = false; }, 0); }
    }
    function onMoveEnd() {
      if (busy) return;
      settle();
      if (!waiting) { waiting = true; map.once('idle', function () { waiting = false; settle(); }); }
    }
    if (map.setCenterClampedToGround) map.setCenterClampedToGround(false);
    map.on('moveend', onMoveEnd);
    onMoveEnd();
    return function detach() {
      alive = false;
      map.off('moveend', onMoveEnd);
      if (map.setCenterClampedToGround) map.setCenterClampedToGround(true);
    };
  }

  // THE WHITE-WASHOUT FIX (landslidescience, 2026-09-18). MapLibre sets its
  // far clip plane from the minimum terrain elevation, taken from ONE tile
  // (the one under the centre) via getMinTileElevationForLngLatZoom(), whose
  // `?? 0` makes a tile with no DEM loaded report sea level. Everything below
  // 0 m is then clipped: a white region under a smooth curved edge that
  // slides as the centre tile changes and scales with exaggeration. The fix
  // floors the reported minimum at the active survey's z_min (exaggerated);
  // MapLibre then does its own near/far arithmetic with a truthful number.
  // zMin() returns the current survey's z_min, or null for none.
  // Verified against maplibre-gl 5.24.0. The terrain object is built lazily,
  // so this keeps trying on render until it appears, and says on the console
  // whether it attached.
  function floorTerrainMinimum(map, zMin) {
    var tried = 0;
    function terrainObject() {
      return (map.style && map.style.terrain) || map.terrain || (map.painter && map.painter.terrain) || null;
    }
    function attach() {
      var t = terrainObject();
      if (!t || typeof t.getMinTileElevationForLngLatZoom !== 'function') {
        if (++tried === 60) console.warn('demshade: could not floor the terrain minimum; deep surveys may clip white at high exaggeration');
        return;
      }
      if (t._lsFloored) return;
      t._lsFloored = true;
      var inner = t.getMinTileElevationForLngLatZoom.bind(t);
      t.getMinTileElevationForLngLatZoom = function (lnglat, z) {
        var v = inner(lnglat, z), zm = zMin();
        return zm == null || !isFinite(zm) ? v : Math.min(v, zm * (t.exaggeration || 1));
      };
    }
    map.on('render', function () { if (map.getTerrain()) attach(); });
  }

  // setSky is a style change that repaints everything whether or not
  // anything differed, and callers tend to run on every moveend: only on a
  // change. set(false) after setTerrain(null) as well, so the next set(true)
  // is not skipped.
  function skySwitch(map, sky) {
    var on = null;
    return function set(want) {
      want = !!want;
      if (want === on) return;
      on = want;
      map.setSky(want ? sky : null);
    };
  }

  return {
    outerRings: outerRings, contextOpts: contextOpts, surveyOpts: surveyOpts,
    terrainSource: terrainSource, trackTerrainCentre: trackTerrainCentre,
    relativeThreshold: relativeThreshold, cameraHeight: cameraHeight,
    floorTerrainMinimum: floorTerrainMinimum, skySwitch: skySwitch
  };
})();
