/* tg_twi.js — the wetness overlay: a topographic wetness index (TWI) for
 * the current view, computed by the worker (core/water.py) and drawn as an
 * image over the shading, under the trails. Pale to deep blue is wetter;
 * dry ground is clear. Light: no culverts, closed basins filled, so lakes,
 * the sea and other flats read as solid blue.
 * Global `window.TgTwi`; map.js calls TgTwi.init({map}) and, after a
 * basemap change, TgTwi.onStyleReload().
 */
window.TgTwi = (function () {
  'use strict';

  var map = null, img = null, timer = null, startedAt = 0;
  function $(id) { return document.getElementById(id); }
  function csrf() { var m = document.querySelector('meta[name=csrf-token]'); return m ? m.content : ''; }
  function status(t, err) { var s = $('twi-status'); if (s) { s.textContent = t; s.classList.toggle('tg-error', !!err); } }
  function opacity() { var o = $('twi-op'); return o ? +o.value / 100 : 0.8; }

  function draw() {
    if (!map || !img) return;
    if (map.getLayer('twi')) map.removeLayer('twi');
    if (map.getSource('twi')) map.removeSource('twi');
    if (!$('twi-show').checked) return;
    map.addSource('twi', { type: 'image', url: img.png, coordinates: img.corners });
    // Over the shading, under every trail layer.
    var before = ['legs-casing', 'tracks', 'trails-casing'].filter(function (id) { return map.getLayer(id); })[0];
    map.addLayer({ id: 'twi', type: 'raster', source: 'twi', paint: { 'raster-opacity': opacity(), 'raster-fade-duration': 0 } }, before);
  }

  function compute() {
    var b = map.getBounds();
    var bbox = [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()];
    $('twi-go').disabled = true;
    status('Starting…');
    fetch('/api/twi/', { method: 'POST', credentials: 'same-origin',
                        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf() },
                        body: JSON.stringify({ bbox: bbox }) })
      .then(function (r) {
        if (r.redirected && /\/accounts\/login\//.test(r.url)) throw new Error('You have been signed out. Sign in again and retry.');
        return r.json().then(function (j) { if (!r.ok) throw new Error(j.error || 'Error ' + r.status); return j; });
      })
      .then(function (j) { startedAt = Date.now(); poll(j.job); })
      .catch(function (e) { $('twi-go').disabled = false; status(e.message, true); });
  }
  function poll(job) {
    clearTimeout(timer);
    fetch('/api/twi/jobs/' + job + '/', { credentials: 'same-origin' })
      .then(function (r) {
        if (r.redirected && /\/accounts\/login\//.test(r.url)) return { status: 'failed', error: 'You have been signed out. Sign in again and retry.' };
        return r.json();
      })
      .then(function (j) {
        if (j.status === 'done') {
          img = j.result; $('twi-go').disabled = false; $('twi-show').disabled = false; $('twi-show').checked = true;
          draw();
          var s = img.stats || {};
          status('Median ' + s.p50 + ', wettest 10 % above ' + s.p90 + ' (' + Math.round(j.elapsed_s) + ' s; ' +
                 (img.sources || []).map(function (x) { return x.title + ' ' + x.pct + ' %'; }).join(', ') + ')');
          return;
        }
        if (j.status === 'failed' || j.error) { $('twi-go').disabled = false; status(j.error || 'Failed', true); return; }
        status((j.stage || 'Working') + '… ' + Math.round((Date.now() - startedAt) / 1000) + ' s');
        timer = setTimeout(function () { poll(job); }, 1500);
      })
      .catch(function () { timer = setTimeout(function () { poll(job); }, 3000); });
  }

  return {
    init: function (a) {
      map = a.map;
      if (!$('twi-go')) return;                  // signed out: no overlay controls
      $('twi-go').addEventListener('click', compute);
      $('twi-show').addEventListener('change', draw);
      $('twi-op').addEventListener('input', function () {
        $('twi-op-val').textContent = this.value + ' %';
        if (map.getLayer('twi')) map.setPaintProperty('twi', 'raster-opacity', opacity());
      });
    },
    onStyleReload: draw
  };
})();
