/* Copied verbatim from landslidescience inventory/static/inventory/js/basemaps.js
 * (hig314/landslidescience @ 7327b63, 2026-09-26). One copy of every shared
 * thing: do not edit here; change it there and re-copy, until the modules
 * move to hig-maplibre-kit (PLAN.md §0). */
/* Shared basemap handling — the single source of truth for the built-in basemap
 * list and for turning a basemap descriptor into a MapLibre style, used by BOTH
 * the main inventory map (map.js) and the edit/review preview map
 * (_polygon_map.html) so they can't drift apart.
 *
 * A basemap *descriptor*:
 *   { id, label, category, coverage, attr,
 *     tiles | style,            // raster XYZ template, or a vector style URL
 *     labelTiles?,              // optional raster label overlay
 *     scheme?,                  // 'tms' for bottom-origin tiles
 *     tileSize?, minzoom?, maxzoom?, thumb?,
 *     reproject? }              // 'epsg3395' → reproject Yandex-style tiles on the fly
 *
 * Tile-URL transforms, all funneled through buildRasterStyle (live) and
 * thumbnailUrl (card preview), so adding one touches a single place:
 *   • {x}/{y}/{z}              — native MapLibre
 *   • scheme:'tms' / {-y}      — native (bottom origin)
 *   • {q}/{quadkey}/{switch:…}/{s}/{subdomain}
 *                              — rewritten per-tile by transformRequest()
 *   • reproject 'epsg3395'     — warped per-tile by the 'reproj' addProtocol
 *
 * Consumers:
 *   var BASEMAPS = CFG.basemaps || LSBasemaps.DEFAULTS.slice();
 *   new maplibregl.Map({ ..., transformRequest: LSBasemaps.transformRequest });
 *   LSBasemaps.registerProtocols();                 // once, before reproject tiles load
 *   map.setStyle(LSBasemaps.buildRasterStyle(bm, { globe: true }));
 */
(function () {
  // Inline SVG thumbnail for the blank basemap — a white card that labels
  // itself (no tile to sample, no static asset to ship).
  var BLANK_THUMB = 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(
    '<svg xmlns="http://www.w3.org/2000/svg" width="120" height="80">' +
    '<rect width="120" height="80" fill="#ffffff"/>' +
    '<text x="60" y="44" font-family="sans-serif" font-size="13" fill="#999" ' +
    'text-anchor="middle">Blank White</text></svg>');

  var DEFAULTS = [
    { id: 'streets', label: 'Streets', category: 'Other', coverage: 'Global',
      style: 'https://tiles.openfreemap.org/styles/liberty',
      thumb: 'inventory/img/basemap-thumbs/streets.png',
      attr: '© OpenFreeMap & OpenStreetMap contributors' },
    { id: 'esri-img', label: 'ESRI Imagery', category: 'Imagery', coverage: 'Global',
      tiles: 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
      labelTiles: 'https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}',
      thumb: 'inventory/img/basemap-thumbs/esri-img.png',
      attr: '© Esri, Maxar, Earthstar Geographics' },
    { id: 's2-cloudless', label: 'Sentinel-2 cloudless', category: 'Imagery', coverage: 'Global, 10 m',
      tiles: 'https://tiles.maps.eox.at/wmts/1.0.0/s2cloudless-2024_3857/default/g/{z}/{y}/{x}.jpg',
      thumb: 'inventory/img/basemap-thumbs/s2-cloudless.jpg',
      attr: 'Sentinel-2 cloudless 2024 by EOX (modified Copernicus Sentinel data 2024)' },
    { id: 'esri-topo', label: 'ESRI Topo', category: 'Topo', coverage: 'Global',
      tiles: 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Topo_Map/MapServer/tile/{z}/{y}/{x}',
      thumb: 'inventory/img/basemap-thumbs/esri-topo.png',
      attr: '© Esri, USGS, NOAA' },
    { id: 'usgs-topo', label: 'USGS Topo', category: 'Topo', coverage: 'Global (detailed in US only)',
      tiles: 'https://basemap.nationalmap.gov/arcgis/rest/services/USGSTopo/MapServer/tile/{z}/{y}/{x}',
      thumb: 'inventory/img/basemap-thumbs/usgs-topo.png',
      attr: 'USGS National Map' },
    { id: 'usgs-img', label: 'USGS Imagery', category: 'Imagery', coverage: 'Global (high-res in US only)',
      tiles: 'https://basemap.nationalmap.gov/arcgis/rest/services/USGSImageryTopo/MapServer/tile/{z}/{y}/{x}',
      thumb: 'inventory/img/basemap-thumbs/usgs-img.png',
      attr: 'USGS National Map' },
    { id: 'nrcs-ahap', label: 'AHAP 1978-1986', category: 'Historical', coverage: 'Alaska (partial)',
      tiles: 'https://apps.geo.fpac.usda.gov/nrcs-imagery/rest/services/ortho_imagery/ahap_1978_to_1986_150cm_colorbalance/ImageServer/exportImage?bbox={bbox-epsg-3857}&bboxSR=3857&imageSR=3857&size=256,256&format=jpgpng&f=image',
      thumb: 'inventory/img/basemap-thumbs/nrcs-ahap.png',
      attr: 'Alaska High-Altitude Photography (1978-1986), USDA NRCS / FPAC Geospatial Business Branch' },
    { id: 'usgs-hist', label: 'USGS Hist. Topo', category: 'Historical', coverage: 'United States',
      tiles: 'https://server.arcgisonline.com/ArcGIS/rest/services/USA_Topo_Maps/MapServer/tile/{z}/{y}/{x}',
      thumb: 'inventory/img/basemap-thumbs/usgs-hist.png',
      attr: '© Esri, USGS — USA historical topographic maps' },
    // Blank white canvas — no tiles at all. Lets the inventory data (or a
    // semi-transparent overlay like OPERA velocity) read without any imagery
    // behind it, on either side of the compare wiper. Inline style object
    // (no network); glyphs still needed for symbol layers (pin labels).
    { id: 'blank', label: 'Blank White', category: 'Other', coverage: 'Global',
      style: { version: 8,
               glyphs: 'https://demotiles.maplibre.org/font/{fontstack}/{range}.pbf',
               sources: {},
               layers: [{ id: 'background', type: 'background',
                          paint: { 'background-color': '#ffffff' } }] },
      thumb: BLANK_THUMB,
      attr: '' },
  ];

  // -- quadkey / subdomain placeholders (resolved client-side) ---------------
  var QK_RE = /\{q\}|\{quadkey\}|\{switch:[^}]+\}|\{s\}|\{subdomain\}/;
  function tileToQuadkey(x, y, z) {
    var qk = '';
    for (var i = z; i > 0; i--) {
      var d = 0, m = 1 << (i - 1);
      if (x & m) d += 1;
      if (y & m) d += 2;
      qk += d;
    }
    return qk;
  }
  function resolveTile(real, x, y, z) {
    var qk = tileToQuadkey(x, y, z);
    return real.replace(/\{q\}/g, qk).replace(/\{quadkey\}/g, qk)
               .replace(/\{switch:([^}]+)\}/g, function (_m, list) {
                 var a = list.split(','); return a[(x + y) % a.length];
               })
               .replace(/\{s\}/g, 'a').replace(/\{subdomain\}/g, 'a');
  }
  // transformRequest (Map construction option): rewrite the quadkey sentinel
  // into the real per-tile URL. Loaded as a plain image → no CORS dependency.
  function transformRequest(url) {
    if (url.indexOf('https://qmsq.invalid/') !== 0) return;
    try {
      var u = new URL(url);
      var p = u.pathname.split('/').filter(Boolean);   // [z, x, y]
      return { url: resolveTile(u.searchParams.get('u') || '', +p[1], +p[2], +p[0]) };
    } catch (e) { return; }
  }

  // -- EPSG:3395 → 3857 reprojection (1-D vertical warp) ----------------------
  // 3857 (spherical) and 3395 (ellipsoidal) Mercator share X and the x-tile
  // index; only latitude→Y differs, so the warp is a vertical resample. Cheap.
  var _ECC = 0.0818191908426215;        // WGS84 first eccentricity
  function _yn3857ToLat(yn) {           // normalized Y [0,1] → latitude (rad), spherical inverse
    return Math.atan(Math.sinh(Math.PI * (1 - 2 * yn)));
  }
  function _latToYn3395(lat) {          // latitude (rad) → normalized Y [0,1], ellipsoidal forward
    var s = Math.sin(lat);
    var m = Math.log(Math.tan(Math.PI / 4 + lat / 2) *
                     Math.pow((1 - _ECC * s) / (1 + _ECC * s), _ECC / 2));
    return 0.5 - m / (2 * Math.PI);
  }
  function _loadBitmap(url, ac) {
    return fetch(url, ac ? { signal: ac.signal } : undefined)
      .then(function (r) { return r.ok ? r.blob() : null; })
      .then(function (b) { return b ? createImageBitmap(b) : null; })
      .catch(function () { return null; });
  }
  function _reprojLoader(params, abortController) {
    var u = new URL(params.url.replace('reproj://', 'https://reproj.invalid/'));
    var p = u.pathname.split('/').filter(Boolean);     // [z, x, y]
    var z = +p[0], x = +p[1], y = +p[2];
    var tmpl = u.searchParams.get('u') || '';
    var n = Math.pow(2, z), TS = 256;
    // Source pixel-Y for each output row (3857 row → lat → 3395 Y).
    var srcY = new Float64Array(TS), minSY = Infinity, maxSY = -Infinity;
    for (var row = 0; row < TS; row++) {
      var yn = (y + (row + 0.5) / TS) / n;
      var sy = _latToYn3395(_yn3857ToLat(yn)) * n * TS;
      srcY[row] = sy;
      if (sy < minSY) minSY = sy;
      if (sy > maxSY) maxSY = sy;
    }
    var tyTop = Math.max(0, Math.min(n - 1, Math.floor(minSY / TS)));
    var tyBot = Math.max(0, Math.min(n - 1, Math.floor(maxSY / TS)));
    var reqs = [];
    for (var ty = tyTop; ty <= tyBot; ty++) {
      var surl = tmpl.replace(/\{z\}/g, z).replace(/\{x\}/g, x).replace(/\{y\}/g, ty);
      reqs.push(_loadBitmap(resolveTile(surl, x, ty, z), abortController));
    }
    return Promise.all(reqs).then(function (bmps) {
      var stripTop = tyTop * TS;
      var strip = document.createElement('canvas');
      strip.width = TS; strip.height = (tyBot - tyTop + 1) * TS;
      var sctx = strip.getContext('2d');
      bmps.forEach(function (b, i) { if (b) sctx.drawImage(b, 0, i * TS); });
      var out = document.createElement('canvas');
      out.width = TS; out.height = TS;
      var octx = out.getContext('2d');
      for (var row = 0; row < TS; row++) {
        var sy = Math.round(srcY[row] - stripTop);
        sy = Math.max(0, Math.min(strip.height - 1, sy));
        octx.drawImage(strip, 0, sy, TS, 1, 0, row, TS, 1);
      }
      return new Promise(function (resolve, reject) {
        out.toBlob(function (blob) {            // JPEG: satellite imagery, smaller/faster than PNG
          if (!blob) { reject(new Error('reproj toBlob failed')); return; }
          blob.arrayBuffer().then(function (buf) { resolve({ data: buf }); }, reject);
        }, 'image/jpeg', 0.85);
      });
    });
  }
  var _protocolsRegistered = false;
  function registerProtocols() {
    if (_protocolsRegistered || typeof maplibregl === 'undefined' || !maplibregl.addProtocol) return;
    maplibregl.addProtocol('reproj', _reprojLoader);
    _protocolsRegistered = true;
  }

  // -- style + thumbnail (the two tile-URL chokepoints) ----------------------
  function _liveTilesUrl(bm) {
    if (bm.reproject === 'epsg3395') {
      return 'reproj://{z}/{x}/{y}?u=' + encodeURIComponent(bm.tiles);
    }
    return QK_RE.test(bm.tiles)
      ? 'https://qmsq.invalid/{z}/{x}/{y}?u=' + encodeURIComponent(bm.tiles)
      : bm.tiles;
  }
  function buildRasterStyle(bm, opts) {
    opts = opts || {};
    var base = { type: 'raster', tiles: [_liveTilesUrl(bm)], tileSize: bm.tileSize || 256, attribution: bm.attr || '' };
    if (bm.scheme) base.scheme = bm.scheme;
    if (bm.minzoom != null) base.minzoom = bm.minzoom;
    if (bm.maxzoom != null) base.maxzoom = bm.maxzoom;
    var sources = { basemap: base };
    var layers = [{ id: 'basemap', type: 'raster', source: 'basemap' }];
    if (bm.labelTiles) {
      sources.labels = { type: 'raster', tiles: [bm.labelTiles], tileSize: 256 };
      layers.push({ id: 'labels', type: 'raster', source: 'labels' });
    }
    var style = { version: 8, sources: sources, layers: layers };
    if (opts.globe) style.projection = { type: 'globe' };
    if (opts.glyphs) style.glyphs = opts.glyphs;
    return style;
  }
  // Card thumbnail URL. cfg: { basemapThumbs, staticBase }. Reproject layers use
  // the un-warped z4 source tile (a thumbnail; misalignment is invisible there).
  function thumbnailUrl(bm, cfg) {
    cfg = cfg || {};
    if (cfg.basemapThumbs && cfg.basemapThumbs[bm.id]) return cfg.basemapThumbs[bm.id];
    if (bm.thumb) return /^(https?|data):/.test(bm.thumb) ? bm.thumb : ((cfg.staticBase || '') + bm.thumb);
    if (bm.style) return null;
    var z = 4, x = 2, y = 4;
    var t = bm.tiles
      .replace('{z}', z).replace('{y}', y).replace('{x}', x)
      .replace('{bbox-epsg-3857}', '-15028131,7514064,-12523443,10018752');
    if (QK_RE.test(t)) t = resolveTile(t, x, y, z);
    return t;
  }

  // The one way to turn a basemap descriptor into a map style: some
  // basemaps (e.g. Blank White) carry a prebuilt `style` object instead of
  // raster-tile params — callers that forget the distinction break on them.
  function styleFor(bm, opts) {
    return bm.style ? bm.style : buildRasterStyle(bm, opts);
  }

  window.LSBasemaps = {
    DEFAULTS: DEFAULTS,
    QK_RE: QK_RE,
    resolveTile: resolveTile,
    transformRequest: transformRequest,
    registerProtocols: registerProtocols,
    buildRasterStyle: buildRasterStyle,
    styleFor: styleFor,
    thumbnailUrl: thumbnailUrl,
  };
})();
