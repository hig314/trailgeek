/* Copied verbatim from landslidescience inventory/static/inventory/js/ls_hash.js
 * (hig314/landslidescience @ 1c63e60, 2026-09-28). One copy of every shared
 * thing: do not edit here; change it there and run
 * `python tools/sync_shared.py sync` (tools/shared.json pins the commit). */
/* Shared URL-hash view-state codec — ONE grammar for /inventory/ and
 * /glaciers/:
 *
 *   map=<zoom>/<lat>/<lon> & base=<id> & swipe=<id> & sx=<pct>
 *   & ov=<id>[~s].l<pct>r<pct>,…   (~s = data-variant flag, e.g. smoothed
 *                                   thinning; l/r = pane visibility+opacity)
 *   & li=<id>.<p>l<pct>r<pct>,…    (lidar DEM overlays; p = shading preset
 *                                   h hillshade | k KBSP; l/r as for ov)
 *   & im=<id>.l<pct>,…             (imagery overlays: uploaded scenes and
 *                                   Sentinel-2 windows. Main pane only, which
 *                                   is why there is no r<pct> -- these have
 *                                   never been offered in the wiper's right
 *                                   pane. <id> is the TraceRaster row id.)
 *   & <extras…>                    (app-specific params pass through:
 *                                   inventory id/ids/tab/an, glaciers site/t)
 *
 * /glaciers/ consumes this module directly. map.js still carries its own
 * embedded parser with the IDENTICAL grammar (migration onto this module is
 * the planned next inventory-touching step — see CLAUDE.md); until that
 * lands, any grammar change MUST be made in both places. The server-side
 * validator charset (views.py _VIEW_STATE_RE) is the third party to keep
 * in sync.
 */
(function () {
    'use strict';

    function parse(hash) {
        var out = { extras: {} };
        if (!hash) return out;
        String(hash).replace(/^#/, '').split('&').forEach(function (kv) {
            var i = kv.indexOf('=');
            if (i < 0) return;
            var k = kv.slice(0, i), v = kv.slice(i + 1);
            if (k === 'map') {
                var m = v.split('/');
                var z = parseFloat(m[0]), lat = parseFloat(m[1]), lon = parseFloat(m[2]);
                if (isFinite(z) && isFinite(lat) && isFinite(lon) &&
                    z >= 0 && z <= 24 && lat >= -90 && lat <= 90 &&
                    lon >= -540 && lon <= 540) {
                    out.zoom = z; out.lat = lat; out.lon = lon;
                }
            } else if (k === 'base') {
                if (v) out.base = v;
            } else if (k === 'swipe') {
                if (v) out.swipe = v;
            } else if (k === 'sx') {
                var x = parseFloat(v);
                if (isFinite(x) && x >= 0 && x <= 100) out.sx = x;
            } else if (k === 'ov') {
                var ovOut = {};
                v.split(',').forEach(function (ent) {
                    var m2 = /^(.+)\.((?:[lr]\d+)+)$/.exec(ent);
                    if (!m2) return;
                    var e = {};
                    m2[2].replace(/([lr])(\d+)/g, function (_, sideCh, pct) {
                        var o = Math.min(100, Math.max(0, parseInt(pct, 10))) / 100;
                        if (sideCh === 'l') { e.left = true; e.opLeft = o; }
                        else                { e.right = true; e.opRight = o; }
                        return '';
                    });
                    if (e.left || e.right) {
                        var idBits = m2[1].split('~');
                        if (idBits.indexOf('s') > 0) e.smooth = true;
                        ovOut[idBits[0]] = e;
                    }
                });
                out.ov = ovOut;
            } else if (k === 'li') {
                var liOut = {};
                v.split(',').forEach(function (ent) {
                    // [hpk]: h hillshade, p the converged preset, k its
                    // retired KBSP spelling (read-only, see LIDAR_CODE_PRESET).
                    var m3 = /^([A-Za-z0-9_]+)\.([hpk])((?:[lr]\d+)+)$/.exec(ent);
                    if (!m3) return;
                    // 'k' is the retired KBSP code; it now resolves to the
                    // converged 'preset' so old links keep working.
                    var e3 = { preset: (m3[2] === 'p' || m3[2] === 'k') ? 'preset' : 'hillshade' };
                    m3[3].replace(/([lr])(\d+)/g, function (_, sideCh, pct) {
                        var o = Math.min(100, Math.max(0, parseInt(pct, 10))) / 100;
                        if (sideCh === 'l') { e3.left = true; e3.opLeft = o; }
                        else                { e3.right = true; e3.opRight = o; }
                        return '';
                    });
                    if (e3.left || e3.right) liOut[m3[1]] = e3;
                });
                out.li = liOut;
            } else if (k === 'im') {
                var imOut = {};
                v.split(',').forEach(function (ent) {
                    var m4 = /^(\d+)\.l(\d+)$/.exec(ent);
                    if (!m4) return;
                    imOut[m4[1]] = Math.min(100, Math.max(0, parseInt(m4[2], 10))) / 100;
                });
                out.im = imOut;
            } else {
                out.extras[k] = v;
            }
        });
        return out;
    }

    /* o: { zoom, lat, lon, base, swipe, sx,
     *      ov: {id: {left, right, opLeft, opRight, smooth}},
     *      li: {id: {preset, left, right, opLeft, opRight}}, extras: {…} }
     * Omit/null any part to leave it out of the hash. Number formats match
     * the inventory writer exactly (zoom 2dp, lat/lon 4dp). */
    function encode(o) {
        var parts = [];
        if (o.zoom != null && o.lat != null && o.lon != null) {
            parts.push('map=' + o.zoom.toFixed(2) + '/' +
                       o.lat.toFixed(4) + '/' + o.lon.toFixed(4));
        }
        if (o.base) parts.push('base=' + o.base);
        if (o.swipe) {
            parts.push('swipe=' + o.swipe);
            if (o.sx != null) parts.push('sx=' + Math.round(o.sx));
        }
        if (o.ov) {
            var ovp = [];
            Object.keys(o.ov).forEach(function (id) {
                var e = o.ov[id];
                var spec = '';
                if (e.left)  spec += 'l' + Math.round((e.opLeft != null ? e.opLeft : 1) * 100);
                if (e.right) spec += 'r' + Math.round((e.opRight != null ? e.opRight : 1) * 100);
                if (spec) ovp.push(id + (e.smooth ? '~s' : '') + '.' + spec);
            });
            if (ovp.length) parts.push('ov=' + ovp.join(','));
        }
        if (o.li) {
            var lip = [];
            Object.keys(o.li).forEach(function (id) {
                var e = o.li[id];
                var spec = '';
                if (e.left)  spec += 'l' + Math.round((e.opLeft != null ? e.opLeft : 1) * 100);
                if (e.right) spec += 'r' + Math.round((e.opRight != null ? e.opRight : 1) * 100);
                if (spec) lip.push(id + '.' + (e.preset === 'preset' ? 'p' : 'h') + spec);
            });
            if (lip.length) parts.push('li=' + lip.join(','));
        }
        if (o.im) {
            var imp = [];
            Object.keys(o.im).forEach(function (id) {
                var op = o.im[id];
                if (op != null) imp.push(id + '.l' + Math.round(op * 100));
            });
            if (imp.length) parts.push('im=' + imp.join(','));
        }
        if (o.extras) {
            Object.keys(o.extras).forEach(function (k) {
                var v = o.extras[k];
                if (v != null && v !== '') parts.push(k + '=' + v);
            });
        }
        return '#' + parts.join('&');
    }

    window.LSHash = { parse: parse, encode: encode };
})();
