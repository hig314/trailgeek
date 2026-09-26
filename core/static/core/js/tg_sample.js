/* tg_sample.js — instant client-side elevation profiles from AWS Terrain
 * Tiles (Mapzen "terrarium"), the same public source the map's 3D terrain
 * falls back to outside lidar. Global `window.TgSample`.
 *
 * The tile decode is the one in landslidescience's dem_fill.js
 * (inventory/static/inventory/js/dem_fill.js, contextTile()): h = R*256 + G +
 * B/256 - 32768. Sampling is bilinear between pixel centres at z14, where a
 * pixel is ~5 m at 60°N; the underlying data in Alaska is the USGS 2 arc-second
 * NED (~60 m), so this is a *quick look*. The authoritative profile on lidar
 * comes from the Phase 2 server evaluator.
 *
 *   TgSample.profile(coords, { spacing: 10 })   // coords: [[lon, lat], …]
 *     -> Promise<[{d, z, lon, lat}, …]>          // d in metres along the line
 *   TgSample.resample(coords, spacing)          // the densified line, no z
 *   TgSample.distances(coords)                  // cumulative metres per vertex
 */
window.TgSample = (function () {
  'use strict';

  var Z = 14, TILE = 256;
  var URL_T = 'https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png';
  var cache = new Map();          // 'z/x/y' -> Promise<Float32Array>
  var CACHE_MAX = 200;

  // Equirectangular metres between two lon/lat points; plenty at trail scale.
  function metres(a, b) {
    var R = 6371008.8, toR = Math.PI / 180;
    var dLat = (b[1] - a[1]) * toR;
    var dLon = (b[0] - a[0]) * toR * Math.cos((a[1] + b[1]) / 2 * toR);
    return R * Math.sqrt(dLat * dLat + dLon * dLon);
  }

  function distances(coords) {
    var d = [0];
    for (var i = 1; i < coords.length; i++) d.push(d[i - 1] + metres(coords[i - 1], coords[i]));
    return d;
  }

  // Densify: a point every `spacing` metres, keeping the original vertices'
  // spacing where it is already finer. Returns [{lon, lat, d}].
  function resample(coords, spacing) {
    var out = [], acc = 0;
    if (!coords.length) return out;
    out.push({ lon: coords[0][0], lat: coords[0][1], d: 0 });
    for (var i = 1; i < coords.length; i++) {
      var a = coords[i - 1], b = coords[i], seg = metres(a, b);
      if (seg === 0) continue;
      var n = Math.max(1, Math.round(seg / spacing));
      for (var k = 1; k <= n; k++) {
        var t = k / n;
        out.push({ lon: a[0] + (b[0] - a[0]) * t, lat: a[1] + (b[1] - a[1]) * t, d: acc + seg * t });
      }
      acc += seg;
    }
    return out;
  }

  function tilePixel(lon, lat) {
    var n = Math.pow(2, Z);
    var x = (lon + 180) / 360 * n;
    var latR = lat * Math.PI / 180;
    var y = (1 - Math.log(Math.tan(latR) + 1 / Math.cos(latR)) / Math.PI) / 2 * n;
    return { x: x * TILE, y: y * TILE };       // global pixel coords at z
  }

  function tile(tx, ty) {
    var key = Z + '/' + tx + '/' + ty;
    if (cache.has(key)) { var v = cache.get(key); cache.delete(key); cache.set(key, v); return v; }
    var url = URL_T.replace('{z}', Z).replace('{x}', tx).replace('{y}', ty);
    var job = fetch(url).then(function (r) {
      if (!r.ok) throw new Error('terrain tile ' + r.status);
      return r.blob();
    }).then(function (b) { return createImageBitmap(b); }).then(function (bmp) {
      var c = document.createElement('canvas');
      c.width = c.height = TILE;
      var ctx = c.getContext('2d', { willReadFrequently: true });
      ctx.drawImage(bmp, 0, 0);
      bmp.close && bmp.close();
      var d = ctx.getImageData(0, 0, TILE, TILE).data;
      var out = new Float32Array(TILE * TILE);
      for (var i = 0, p = 0; i < out.length; i++, p += 4) {
        out[i] = d[p] * 256 + d[p + 1] + d[p + 2] / 256 - 32768;
      }
      return out;
    });
    job.catch(function () { cache.delete(key); });
    cache.set(key, job);
    if (cache.size > CACHE_MAX) cache.delete(cache.keys().next().value);
    return job;
  }

  // Bilinear sample at global pixel (px, py), reading up to four tiles.
  function sampleAt(px, py) {
    var fx = px - 0.5, fy = py - 0.5;
    var x0 = Math.floor(fx), y0 = Math.floor(fy), tx = fx - x0, ty = fy - y0;
    function at(gx, gy) {
      var txi = Math.floor(gx / TILE), tyi = Math.floor(gy / TILE);
      return tile(txi, tyi).then(function (T) { return T[(gy - tyi * TILE) * TILE + (gx - txi * TILE)]; });
    }
    return Promise.all([at(x0, y0), at(x0 + 1, y0), at(x0, y0 + 1), at(x0 + 1, y0 + 1)]).then(function (v) {
      return (v[0] * (1 - tx) + v[1] * tx) * (1 - ty) + (v[2] * (1 - tx) + v[3] * tx) * ty;
    });
  }

  function profile(coords, opts) {
    opts = opts || {};
    var pts = resample(coords, opts.spacing || 10);
    return Promise.all(pts.map(function (p) {
      var g = tilePixel(p.lon, p.lat);
      return sampleAt(g.x, g.y).then(function (z) { return { d: p.d, z: z, lon: p.lon, lat: p.lat }; });
    }));
  }

  return { profile: profile, resample: resample, distances: distances, metres: metres, zoom: Z };
})();
