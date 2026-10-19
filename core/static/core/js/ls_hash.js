/* Copied verbatim from landslidescience inventory/static/inventory/js/ls_hash.js
 * (hig314/landslidescience @ e152641, 2026-10-18). One copy of every shared
 * thing: do not edit here; change it there and run
 * `python tools/sync_shared.py sync` (tools/shared.json pins the commit). */
/* Shared URL-hash view-state codec — THE grammar for /inventory/ and
 * /glaciers/. Both pages parse and write through this module; nothing else
 * may read or build a hash by hand. (Until 2026-10-06 map.js carried its own
 * copy of the parser and the two drifted — this one had lost the ortho lidar
 * code and never learned `ext`.) The server-side validator charset
 * (views.py _VIEW_STATE_RE) is the one other party to keep in step.
 *
 *   map=<zoom>/<lat>/<lon>[/<bearing>/<pitch>] & base=<id> & swipe=<id> & sx=<pct>
 *         the camera, in MapLibre's own hash order (its `hash: true` option
 *         writes `#zoom/lat/lon/bearing/pitch`). Bearing and pitch are
 *         written only when one is non-zero and read as 0 when absent, so a
 *         `map=` always describes the whole camera and every link written
 *         before 2026-10-17 reads as it did. A page that locks north-up and
 *         nadir (/inventory/, /glaciers/) ignores them; trailgeek's 3D views
 *         ride on them.
 *   & ov=<id>[~s][~<date>].l<pct>r<pct>,…
 *         raster overlays. l/r = which pane it is visible in, with opacity
 *         0–100. `~s` = data-variant flag (smoothed thinning). `~YYYY-MM-DD`
 *         or `~all` pins the date of an overlay that has a date stepper
 *         (surface disturbance); no date = leave the date alone.
 *   & li=<id>.<p>l<pct>[<q>]r<pct>,…
 *         lidar overlays. <p> = what to draw: h hillshade | p preset |
 *         o orthomosaic (k = retired read-only alias for p). An optional
 *         second code <q> before the r pane when the wiper's right pane
 *         draws the survey differently.
 *   & ext=<source>/<layer>,…   mirrored third-party inventory layers
 *   & im=<id>.l<pct>,…         imagery overlays (uploaded scenes, Sentinel-2
 *                              windows; <id> = TraceRaster row id). Main pane
 *                              only, hence no r<pct>.
 *   & ref=<name>,…             reference vector layers (faults | circles)
 *   & <extras…>                app-specific params, passed through untouched
 *                              as strings: inventory sc / filters / tab / an /
 *                              id / ids / pp (permafrost patch) / pr,pw,pve (elevation profile:
 *                              line, window, aspect lock), glaciers site / t.
 *
 * LIST PARAMS (ov, li, ext, im, ref): PRESENT fully describes the set —
 * anything unlisted is off, and present-but-empty means "none". ABSENT means
 * "leave alone". parse() therefore returns the key whenever the param exists,
 * even empty, and encode() writes it whenever the caller passes it, even
 * empty; pass null/undefined to leave it out.
 *
 * Additive only. Stored default views, snapshots and shared links hold these
 * strings for good, so an existing form must keep its meaning: new state gets
 * a new key or a new optional flag, never a new reading of an old token.
 */
(function () {
    'use strict';

    var LI_CODE = { h: 'hillshade', p: 'preset', k: 'preset', o: 'ortho' };
    var LI_PRESET = { hillshade: 'h', preset: 'p', ortho: 'o' };
    var DATE_RE = /^(\d{4}-\d\d-\d\d|all)$/;

    function panes(specStr, e) {      // 'l75r40' -> e.left/opLeft/right/opRight
        specStr.replace(/([lr])(\d+)/g, function (_, sideCh, pct) {
            var o = Math.min(100, Math.max(0, parseInt(pct, 10))) / 100;
            if (sideCh === 'l') { e.left = true; e.opLeft = o; }
            else                { e.right = true; e.opRight = o; }
            return '';
        });
    }

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
                    var b = m.length > 3 ? parseFloat(m[3]) : 0, p = m.length > 4 ? parseFloat(m[4]) : 0;
                    out.bearing = isFinite(b) && Math.abs(b) <= 360 ? b : 0;
                    out.pitch = isFinite(p) && p >= 0 && p <= 180 ? p : 0;
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
                    panes(m2[2], e);
                    if (e.left || e.right) {
                        // `~` flags on the id: s = smoothed variant, a date
                        // pins a stepper. Unknown flags are ignored, so a
                        // reader older than a flag still finds the overlay.
                        var idBits = m2[1].split('~');
                        idBits.slice(1).forEach(function (bit) {
                            if (bit === 's') e.smooth = true;
                            else if (DATE_RE.test(bit)) e.date = bit;
                        });
                        ovOut[idBits[0]] = e;
                    }
                });
                out.ov = ovOut;
            } else if (k === 'li') {
                var liOut = {};
                v.split(',').forEach(function (ent) {
                    // A code may also precede a later pane (`hl100pr80`) and
                    // applies from there on, so the one-code form reads as it
                    // always did.
                    var m3 = /^([A-Za-z0-9_]+)\.([hkpo])((?:[hkpo]?[lr]\d+)+)$/.exec(ent);
                    if (!m3) return;
                    var e3 = { preset: LI_CODE[m3[2]] }, code3 = m3[2];
                    m3[3].replace(/([hkpo]?)([lr])(\d+)/g, function (_, code, sideCh, pct) {
                        if (code) code3 = code;
                        var o = Math.min(100, Math.max(0, parseInt(pct, 10))) / 100;
                        if (sideCh === 'l') { e3.left = true; e3.opLeft = o; e3.preset = LI_CODE[code3]; }
                        else                { e3.right = true; e3.opRight = o; e3.presetR = LI_CODE[code3]; }
                        return '';
                    });
                    if (e3.left || e3.right) liOut[m3[1]] = e3;
                });
                out.li = liOut;
            } else if (k === 'ext') {
                var extOut = {};
                v.split(',').forEach(function (ent) {
                    var em = /^([a-z0-9_]+)\/([a-z0-9_]+)$/.exec(ent);
                    if (em) extOut[em[1] + '/' + em[2]] = true;
                });
                out.ext = extOut;
            } else if (k === 'im') {
                var imOut = {};
                v.split(',').forEach(function (ent) {
                    var m4 = /^(\d+)\.l(\d+)$/.exec(ent);
                    if (!m4) return;
                    imOut[m4[1]] = Math.min(100, Math.max(0, parseInt(m4[2], 10))) / 100;
                });
                out.im = imOut;
            } else if (k === 'ref') {
                var refOut = {};
                v.split(',').forEach(function (name) {
                    if (/^[a-z]+$/.test(name)) refOut[name] = true;
                });
                out.ref = refOut;
            } else {
                out.extras[k] = v;
            }
        });
        return out;
    }

    function pct(op) { return Math.round((op != null ? op : 1) * 100); }

    /* o: { zoom, lat, lon, bearing, pitch, base, swipe, sx,
     *      ov:  {id: {left, right, opLeft, opRight, smooth, date}},
     *      li:  {id: {preset, presetR, left, right, opLeft, opRight}},
     *      ext: {'source/layer': true}, im: {id: opacity}, ref: {name: true},
     *      extras: {key: string} }
     * Omit/null any part to leave it out of the hash; a list part that is
     * passed is written even when empty (see LIST PARAMS above). Entries are
     * written in the object's own key order, so the caller decides it.
     * Number formats: zoom 2 dp; lat/lon by zoom (MapLibre's half-pixel rule,
     * never fewer than 4 dp: 4 up to z11, 5 at z12-15, 6 at z16-18); bearing
     * 1 dp and pitch whole degrees, as MapLibre writes them. */
    function encode(o) {
        var parts = [];
        if (o.zoom != null && o.lat != null && o.lon != null) {
            // lat/lon to MapLibre's rule for its own hash (512 px tiles:
            // 2^z * 512 / 360 / 10^d < 0.5 px), and never fewer than the 4 dp
            // every link so far was written with. 4 dp is 11 m: a link
            // reopened at a lidar zoom used to land several metres off.
            var d = Math.max(4, Math.ceil((o.zoom * Math.LN2 + Math.log(512 / 360 / 0.5)) / Math.LN10));
            var cam = o.zoom.toFixed(2) + '/' + o.lat.toFixed(d) + '/' + o.lon.toFixed(d);
            var b = +o.bearing || 0, pt = +o.pitch || 0;
            if (b || pt) cam += '/' + (Math.round(b * 10) / 10);
            if (pt) cam += '/' + Math.round(pt);
            parts.push('map=' + cam);
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
                if (e.left)  spec += 'l' + pct(e.opLeft);
                if (e.right) spec += 'r' + pct(e.opRight);
                if (spec) ovp.push(id + (e.smooth ? '~s' : '') +
                                   (e.date && DATE_RE.test(e.date) ? '~' + e.date : '') + '.' + spec);
            });
            parts.push('ov=' + ovp.join(','));
        }
        if (o.li) {
            var lip = [];
            Object.keys(o.li).forEach(function (id) {
                var e = o.li[id];
                var spec = '';
                var lead = LI_PRESET[e.preset] || 'h';
                var rc = LI_PRESET[e.presetR || e.preset] || 'h';
                if (e.left)  spec += 'l' + pct(e.opLeft);
                // Second code only when the right pane draws it differently.
                if (e.right) spec += (e.left && rc !== lead ? rc : '') + 'r' + pct(e.opRight);
                if (spec) lip.push(id + '.' + (e.left ? lead : rc) + spec);
            });
            parts.push('li=' + lip.join(','));
        }
        if (o.ext) {
            parts.push('ext=' + Object.keys(o.ext).filter(function (k) { return o.ext[k]; }).join(','));
        }
        if (o.im) {
            var imp = [];
            Object.keys(o.im).forEach(function (id) {
                var op = o.im[id];
                if (op != null) imp.push(id + '.l' + Math.round(op * 100));
            });
            parts.push('im=' + imp.join(','));
        }
        if (o.ref) {
            parts.push('ref=' + Object.keys(o.ref).filter(function (k) { return o.ref[k]; }).join(','));
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
