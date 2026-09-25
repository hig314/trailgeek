/* trailgeek home map (Phase 0): USGS Topo basemap, demshade hillshade and
 * slope from AWS Terrarium tiles, and demshade's 3D terrain toggle.
 * Basemap descriptors move to the shared maplibre kit in a later phase;
 * until then this is the only copy in this repo. */
(function () {
  'use strict';

  var map = new maplibregl.Map({
    container: 'map',
    hash: true,
    center: [-151.19, 59.62],   // Grewingk / Kachemak Bay: the first test lidar
    zoom: 11,
    maxPitch: 85,
    style: {
      version: 8,
      sources: {
        usgstopo: {
          type: 'raster',
          tiles: ['https://basemap.nationalmap.gov/arcgis/rest/services/USGSTopo/MapServer/tile/{z}/{y}/{x}'],
          tileSize: 256,
          maxzoom: 16,
          attribution: 'USGS The National Map'
        }
      },
      layers: [{ id: 'usgstopo', type: 'raster', source: 'usgstopo' }]
    }
  });
  window.tgMap = map;   // console handle for debugging
  map.addControl(new maplibregl.NavigationControl({ visualizePitch: true }), 'top-left');
  map.addControl(new maplibregl.ScaleControl({ unit: 'imperial' }), 'bottom-left');

  var lib = window.MapLibreGlDemShade;
  var demshade = new lib.DemShade();

  map.on('load', function () {
    demshade.addSource('terrarium', {
      tiles: 'https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png',
      encoding: 'terrarium',
      maxzoom: 15,
      alphaNoData: false
    }).then(function () {
      map.addControl(new lib.DemShadeControl({
        demshade: demshade,
        source: 'terrarium',
        shade: { hillshade: 0.8, slope: 0.35, blend: 'multiply' },
        opacity: 0.6,
        collapsed: true
      }), 'top-right');
    }).catch(function (e) { console.error('demshade', e); });
  });
})();
