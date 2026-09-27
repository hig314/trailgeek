/* Copied verbatim from landslidescience inventory/static/inventory/js/dem_shade_bridge.js
 * (hig314/landslidescience @ 7327b63, 2026-09-26). One copy of every shared
 * thing: do not edit here; change it there and re-copy, until the modules
 * move to hig-maplibre-kit (PLAN.md §0). */
/* dem_shade_bridge.js — the old `window.DemShade` API on top of the
 * maplibre-gl-demshade package (vendor/maplibre-gl-demshade.iife.js).
 *
 * The package is the worker-based successor to dem_shade.js: same
 * `demshade://` protocol and byte-identical tile URLs, but the fetch, decode,
 * halo, shading and caches run off the main thread and tiles reach MapLibre
 * as transferred ImageBitmaps. This shim keeps map.js and the lidar preview
 * calling the API they always did:
 *
 *   DemShade.register(maplibregl);            // no-op: the instance registers itself
 *   DemShade.addDataset(id, pmtilesUrl, opts);// sync; tiles wait for the header read
 *   DemShade.catalogOpts(props, extra);       // addDataset opts from a catalog feature
 *   DemShade.addImageServer(id, url, opts);   // e.g. USGS 3DEP, live float32 exports
 *   DemShade.url(id, { az, alt, hs, bl, sl, md, as, ve });
 *   DemShade.demUrl(id);                      // raster-dem passthrough (terrain)
 *   DemShade.diffUrl(newerId, olderId, dmax); // PRGn difference, newer - older
 *
 * `opts.fill` names another registered source to read where this one has no
 * data (a survey over 3DEP). `bl` codes: 0 overlay, 1 plain transparency,
 * 2 hard light, 3 multiply. Set window.DEMSHADE_OPTIONS before this script
 * to pass constructor options (cache sizes, ramps).
 */
window.DemShade = (function () {
  'use strict';
  var P = window.MapLibreGlDemShade;
  if (!P) { console.error('dem_shade_bridge: maplibre-gl-demshade not loaded'); return null; }
  var inst = new P.DemShade(window.DEMSHADE_OPTIONS || {});
  var known = {};
  var last = { elevHit: 0, elevMiss: 0, tileHit: 0, tileMiss: 0, elevCached: 0, tilesCached: 0 };

  return {
    register: function () { /* registered on construction */ },
    addDataset: function (id, pmtilesUrl, opts) {
      if (known[id]) return;
      known[id] = true;
      opts = opts || {};
      var spec;
      if (opts.tiles) {
        // Per-tile URLs through the tile Worker (edge-cached). No archive
        // header to read, so zooms and footprint come from the catalog.
        spec = { tiles: new URL(opts.tiles, location.href).href.replace(/%7B/g, '{').replace(/%7D/g, '}'),
                 encoding: 'mapbox' };
        if (opts.minzoom !== undefined) spec.minzoom = opts.minzoom;
        if (opts.maxzoom !== undefined) spec.maxzoom = opts.maxzoom;
        if (opts.bounds) spec.bounds = opts.bounds;
      } else {
        spec = { pmtiles: new URL(pmtilesUrl, location.href).href, encoding: 'mapbox' };
      }
      if (opts.fill) spec.fill = opts.fill;
      // 'missing' keeps the fill outside the survey only, so water holes
      // inside it stay holes instead of showing the regional DEM's idea
      // of the sea surface.
      if (opts.fillMode) spec.fillMode = opts.fillMode;
      if (opts.overzoom) spec.overzoom = true;
      // Pre-baked slope pyramid (build_lidar.py --stage slope): the slope ramp
      // reads true slope from the float32 archive instead of a gradient of
      // the 0.1 m-quantised tiles, which staircases on gentle ground.
      if (opts.slopeTiles) {
        spec.slope = { tiles: new URL(opts.slopeTiles, location.href).href.replace(/%7B/g, '{').replace(/%7D/g, '}'),
                       step: opts.slopeStep || 0.5 };
      } else if (opts.slope) {
        spec.slope = { pmtiles: new URL(opts.slope, location.href).href, step: opts.slopeStep || 0.5 };
      }
      inst.addSource(id, spec)
          .catch(function (e) { delete known[id]; console.warn('demshade: source ' + id + ' failed', e); });
    },
    // addDataset options from a catalog feature's properties (or the catalog's
    // "context" member): tile-Worker URLs when the catalog has them, else the
    // archives directly; slope pyramid when built. `extra` overrides, e.g.
    // { fill: 'usgs3dep' } or { slope: null, slopeTiles: null } for a twin
    // that must use the in-browser gradient.
    catalogOpts: function (p, extra) {
      var o = { tiles: p.tiles_url || null, slopeTiles: p.slope_tiles_url || null,
                slope: p.slope_url || null, slopeStep: p.slope_step || 0.5,
                minzoom: p.min_zoom, maxzoom: p.max_zoom, bounds: p.bounds || null };
      if (extra) for (var k in extra) if (Object.prototype.hasOwnProperty.call(extra, k)) o[k] = extra[k];
      return o;
    },
    addImageServer: function (id, url, opts) {
      if (known[id]) return;
      known[id] = true;
      var spec = { imageServer: url };
      if (opts && opts.maxzoom !== undefined) spec.maxzoom = opts.maxzoom;
      inst.addSource(id, spec)
          .catch(function (e) { delete known[id]; console.warn('demshade: source ' + id + ' failed', e); });
    },
    url: function (id, o) { return inst.tileUrl(id, P.toOptions(o || {})); },
    demUrl: function (id) { return inst.demTileUrl(id); },
    // Difference layer: newer minus older, ColorBrewer PRGn. Pass the pair
    // in TIME order -- green means the ground rose, which is only true when
    // the first id is the later survey.
    diffUrl: function (newerId, olderId, dmax) { return inst.diffTileUrl(newerId, olderId, dmax); },
    defaults: P.DEFAULT_PARAMS,
    loadTile: function (url, signal) { return inst.loadTile(url, signal); },
    // Old API was synchronous; return the last snapshot and refresh it.
    stats: function () {
      inst.stats().then(function (s) { last = s; });
      return last;
    },
    resetStats: function () { /* counters live in the worker; not resettable */ },
    clearCache: function () { inst.clearCache(); },
    // Keep the map centre on the terrain surface without MapLibre's per-frame
    // re-solve (see terrain-center.ts in the package). Returns a detach fn.
    trackTerrainCenter: function (map, opts) { return P.trackTerrainCenter(map, opts); },
    instance: inst
  };
})();
