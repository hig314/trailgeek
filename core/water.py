"""The wetness overlay: a topographic wetness index (TWI) for the map view,
computed in the Huey worker and shown as an image under the trails. The
same light TWI as the refine tool's wet-ground factor
(trailgeek_analysis.refine.twi; docs/REFINE_TOOL.md §11): 5 m cells, each
the mean of 2 x 2 samples of the DEM stack, upslope area counted up to
BUFFER_M beyond the view. No culverts, closed basins filled.

    POST /api/twi/              {"bbox": [west, south, east, north]} -> {"job"}
    GET  /api/twi/jobs/<job>/   progress, then {"png": data URL, "corners", "stats", "sources"}

Signed-in users only: each view costs the worker tens of seconds.
"""
import base64
import hashlib
import json
import logging
import re
import struct
import time
import uuid
import zlib

import numpy as np
from django.contrib.auth.decorators import login_required
from django.contrib.gis.geos import Polygon
from django.http import JsonResponse
from django.views.decorators.http import require_GET, require_POST

from trailgeek_analysis.refine.twi import twi

from . import geo
from .dem import transform_xy

log = logging.getLogger(__name__)

RES_M = 5.0
SUB = 2
BUFFER_M = 150.0
MAX_KM2 = 4.0
JOB_KEY = "twi:{}"
_JOB = re.compile(r"^[0-9a-f]{32}$")
_cache = {}               # bbox signature -> result; the worker is long-lived
CACHE = 6

# TWI -> colour: transparent where dry, pale to deep blue where wet.
RAMP = [(6.0, (198, 233, 245, 0)), (7.0, (160, 215, 240, 90)), (9.0, (79, 163, 217, 150)),
        (12.0, (31, 95, 191, 205)), (14.0, (11, 42, 128, 230))]


def put_state(job, state):
    from huey.contrib.djhuey import HUEY
    HUEY.put(JOB_KEY.format(job), state)


def get_state(job):
    from huey.contrib.djhuey import HUEY
    return HUEY.get(JOB_KEY.format(job), peek=True)


def colourise(t):
    """TWI array -> RGBA uint8 (nan transparent)."""
    xs = [r[0] for r in RAMP]
    out = np.zeros(t.shape + (4,), np.uint8)
    v = np.where(np.isfinite(t), t, -np.inf)
    for ch in range(4):
        out[..., ch] = np.interp(v, xs, [r[1][ch] for r in RAMP]).round().astype(np.uint8)
    out[~np.isfinite(t)] = 0
    return out


def png_bytes(rgba):
    """A minimal RGBA PNG writer (no imaging library in the image)."""
    h, w = rgba.shape[:2]
    raw = b"".join(b"\x00" + rgba[r].tobytes() for r in range(h))

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


def compute(bbox, progress=None):
    """TWI for bbox (lon/lat), as the result the browser draws."""
    from .evaluation import choose_sources
    from .refine import sample_first

    w_, s_, e_, n_ = bbox
    srid = geo.utm_srid((w_ + e_) / 2, (s_ + n_) / 2)
    cx, cy = transform_xy([w_, e_, e_, w_], [s_, s_, n_, n_], 4326, srid)
    vx0, vx1, vy0, vy1 = cx.min(), cx.max(), cy.min(), cy.max()
    x0 = np.floor((vx0 - BUFFER_M) / RES_M) * RES_M
    y0 = np.floor((vy0 - BUFFER_M) / RES_M) * RES_M
    W = int(np.ceil((vx1 + BUFFER_M - x0) / RES_M)) + 1
    H = int(np.ceil((vy1 + BUFFER_M - y0) / RES_M)) + 1
    poly = Polygon.from_bbox((w_, s_, e_, n_))
    poly.srid = 4326
    sources = choose_sources(poly)
    cols, rows = np.meshgrid(np.arange(W), np.arange(H))
    o = ((np.arange(SUB) + 0.5) / SUB - 0.5) * RES_M
    ox, oy = [a.ravel() for a in np.meshgrid(o, o)]
    xs = (x0 + cols.ravel() * RES_M)[:, None] + ox[None, :]
    ys = (y0 + rows.ravel() * RES_M)[:, None] + oy[None, :]
    if progress:
        progress("Reading the DEM")
    zz, src, warnings = sample_first(xs.ravel(), ys.ravel(), srid, sources)
    zz = zz.reshape(-1, SUB * SUB)
    with np.errstate(all="ignore"):
        z = np.nanmean(zz, axis=1).reshape(H, W)
    if progress:
        progress("Routing water")
    t, _, _ = twi(z, RES_M)
    # Crop to the view (the buffer only feeds upslope area).
    c0 = max(0, int((vx0 - x0) // RES_M)); c1 = min(W - 1, int(np.ceil((vx1 - x0) / RES_M)))
    r0 = max(0, int((vy0 - y0) // RES_M)); r1 = min(H - 1, int(np.ceil((vy1 - y0) / RES_M)))
    tv = t[r0:r1 + 1, c0:c1 + 1]
    img = colourise(tv[::-1])                       # PNG rows run north to south
    ex0, ex1 = x0 + c0 * RES_M - RES_M / 2, x0 + c1 * RES_M + RES_M / 2
    ey0, ey1 = y0 + r0 * RES_M - RES_M / 2, y0 + r1 * RES_M + RES_M / 2
    lon, lat = transform_xy([ex0, ex1, ex1, ex0], [ey1, ey1, ey0, ey0], srid, 4326)
    fin = tv[np.isfinite(tv)]
    src = src.reshape(-1, SUB * SUB)[:, 0]
    used = []
    for k in np.unique(src[src >= 0]).tolist():
        s = sources[k]
        used.append({"title": s.title, "kind": s.kind, "pct": round(100 * float((src == k).mean()), 1)})
    return {
        "png": "data:image/png;base64," + base64.b64encode(png_bytes(img)).decode(),
        "corners": [[round(float(a), 7), round(float(b), 7)] for a, b in zip(lon, lat)],
        "res_m": RES_M, "size": [int(tv.shape[1]), int(tv.shape[0])],
        "stats": {k: round(float(np.percentile(fin, q)), 1) for k, q in (("p50", 50), ("p90", 90), ("p99", 99))} if len(fin) else {},
        "sources": used, "warnings": warnings,
    }


def run_job(job, payload):
    base = {"user": payload["user_id"]}
    t0 = time.time()
    bbox = payload["bbox"]
    key = hashlib.sha1(json.dumps([round(v, 5) for v in bbox]).encode()).hexdigest()
    try:
        if key in _cache:
            res = _cache[key]
        else:
            res = compute(bbox, lambda stage: put_state(job, dict(base, status="running", stage=stage,
                                                                  elapsed_s=round(time.time() - t0, 1))))
            while len(_cache) >= CACHE:
                _cache.pop(next(iter(_cache)))
            _cache[key] = res
        put_state(job, dict(base, status="done", result=res, elapsed_s=round(time.time() - t0, 1)))
    except Exception as e:                         # noqa: BLE001 - reported to the panel, logged in full
        log.exception("twi job %s failed", job)
        put_state(job, dict(base, status="failed", error=f"{type(e).__name__}: {e}"))


def area_km2(bbox):
    w_, s_, e_, n_ = bbox
    srid = geo.utm_srid((w_ + e_) / 2, (s_ + n_) / 2)
    x, y = transform_xy([w_, e_, e_, w_], [s_, s_, n_, n_], 4326, srid)
    return (x.max() - x.min()) * (y.max() - y.min()) / 1e6


@login_required
@require_POST
def api_twi(request):
    from . import tasks
    from .design_views import _bad, _body
    body = _body(request) or {}
    bbox = body.get("bbox")
    try:
        bbox = [float(v) for v in bbox]
        ok = len(bbox) == 4 and -180 <= bbox[0] < bbox[2] <= 180 and -85 <= bbox[1] < bbox[3] <= 85
    except (TypeError, ValueError):
        ok = False
    if not ok:
        return _bad("expected bbox [west, south, east, north]")
    a = area_km2(bbox)
    if a > MAX_KM2:
        return _bad(f"this view is {a:.1f} km²; zoom in to {MAX_KM2:g} km² or less")
    job = uuid.uuid4().hex
    put_state(job, {"user": request.user.pk, "status": "queued", "stage": "Waiting for the worker"})
    tasks.twi_job(job, {"bbox": bbox, "user_id": request.user.pk})
    return JsonResponse({"job": job, "area_km2": round(a, 2)}, status=202)


@login_required
@require_GET
def api_twi_job(request, job):
    from .design_views import _bad
    st = get_state(job) if _JOB.match(job or "") else None
    if st is None or st.get("user") != request.user.pk:
        return _bad("no such job (they expire when the worker restarts)", 404)
    out = {k: v for k, v in st.items() if k != "user"}
    return JsonResponse(out, status=200 if st["status"] in ("done", "failed") else 202)
