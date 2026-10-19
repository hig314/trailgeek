"""Glue for the refine tool (docs/REFINE_TOOL.md): read the corridor's
terrain once into a Grid, run trailgeek_analysis.refine in the Huey worker,
and report progress and the result through Huey's key store, where the
browser polls it (core.refine_views).

Terrain: the same DEM stack the evaluator uses (evaluation.choose_sources:
lidar first, the baked 3DEP context, Terrarium), sampled at GRID_RES_M on
blocks of the grid that lie within the corridor plus a margin, so a long
diagonal stretch does not read its whole bounding box. Each grid sample
takes the first source with data there. No smoothing: elevation noise is
the cut/fill tube's job (owner, 2026-10-17).
"""
import hashlib
import json
import logging
import math
import time

import numpy as np
from django.contrib.gis.gdal import SpatialReference
from django.contrib.gis.geos import LineString, MultiLineString

from trailgeek_analysis.refine import Grid
from trailgeek_analysis.refine import refine as run_refine
from trailgeek_analysis.refine.score import resample_legs
from trailgeek_analysis.refine.twi import twi

from . import geo
from .dem import transform_xy

log = logging.getLogger(__name__)

GRID_RES_M = 1.0
BLOCK = 32                 # grid samples per block side
MARGIN_M = 8.0             # beyond the corridor: bench cross-sections and slide probes
MAX_GRID_SAMPLES = 4_000_000
TWI_RES_M = 5.0            # TWI on raw 1 m lidar maps ruts and logs, not wetness
TWI_SUB = 3                # each TWI cell averages 3 x 3 samples
TWI_BUFFER_M = 150.0       # upslope area is counted this far past the corridor
TWI_CACHE = 8
SIMPLIFY_M = 0.1           # output lines lose vertices that move them less than this
JOB_KEY = "refine:{}"


def job_key(job):
    return JOB_KEY.format(job)


def put_state(job, state):
    from huey.contrib.djhuey import HUEY
    HUEY.put(job_key(job), state)


def get_state(job):
    from huey.contrib.djhuey import HUEY
    return HUEY.get(job_key(job), peek=True)


def legs_signature(alignment):
    """Changes whenever any leg of the alignment changes, so a result is not
    applied to an alignment that was edited after the run."""
    coords = [[leg.kind, [[round(x, 7), round(y, 7)] for x, y in leg.geom.coords]]
              for leg in alignment.legs.order_by("order")]
    return hashlib.sha1(json.dumps(coords).encode()).hexdigest()


def sample_first(x, y, srid, sources):
    """Elevation from the first source with data at each point, and which
    source (index, -1 = none). A source that cannot be read is skipped."""
    z = np.full(len(x), np.nan)
    src = np.full(len(x), -1)
    srs = SpatialReference(srid)
    warnings = []
    for k, s in enumerate(sources):
        todo = np.flatnonzero(src < 0)
        if not len(todo):
            break
        try:
            v = s.sample(x[todo], y[todo], srs)
        except Exception:                         # noqa: BLE001 - a read failure means "next DEM"
            log.warning("DEM %s could not be read", s.key, exc_info=True)
            warnings.append(f"{s.title} could not be read just now, so the next DEM was used there.")
            continue
        ok = np.isfinite(v)
        z[todo[ok]] = v[ok]
        src[todo[ok]] = k
    return z, src, warnings


def build_grid(legs_xy, srid, sources, corridor_m, res=GRID_RES_M, margin_m=MARGIN_M, sub=1):
    """The terrain within corridor_m + margin_m of the legs at `res`; with
    sub > 1 each cell is the mean of sub x sub samples across it (a coarse
    grid that averages the lidar rather than picking points off it)."""
    reach = corridor_m + margin_m
    pts, _ = resample_legs(legs_xy, 5.0)
    x0 = math.floor((pts[:, 0].min() - reach) / res) * res
    y0 = math.floor((pts[:, 1].min() - reach) / res) * res
    W = int(math.ceil((pts[:, 0].max() + reach - x0) / res)) + 1
    H = int(math.ceil((pts[:, 1].max() + reach - y0) / res)) + 1
    nbx, nby = -(-W // BLOCK), -(-H // BLOCK)
    need = np.zeros((nby, nbx), bool)
    bx = ((pts[:, 0] - x0) / res // BLOCK).astype(int)
    by = ((pts[:, 1] - y0) / res // BLOCK).astype(int)
    r = int(math.ceil(reach / (BLOCK * res)))
    for dy in range(-r, r + 1):
        for dx in range(-r, r + 1):
            need[np.clip(by + dy, 0, nby - 1), np.clip(bx + dx, 0, nbx - 1)] = True
    blocks = np.argwhere(need)
    if len(blocks) * BLOCK * BLOCK > MAX_GRID_SAMPLES:
        raise ValueError("this stretch is too long to refine in one go; select fewer legs")
    rows, cols = [], []
    jj, ii = np.meshgrid(np.arange(BLOCK), np.arange(BLOCK), indexing="ij")
    for j, i in blocks:
        rr, cc = j * BLOCK + jj.ravel(), i * BLOCK + ii.ravel()
        ok = (rr < H) & (cc < W)
        rows.append(rr[ok])
        cols.append(cc[ok])
    rows, cols = np.concatenate(rows), np.concatenate(cols)
    if sub > 1:
        o = (np.arange(sub) + 0.5) / sub - 0.5
        ox, oy = [a.ravel() * res for a in np.meshgrid(o, o)]
        xs = (x0 + cols * res)[:, None] + ox[None, :]
        ys = (y0 + rows * res)[:, None] + oy[None, :]
        zz, ss, warnings = sample_first(xs.ravel(), ys.ravel(), srid, sources)
        zz = zz.reshape(len(cols), -1)
        with np.errstate(all="ignore"):
            z = np.where(np.isfinite(zz).any(1), np.nanmean(np.where(np.isfinite(zz), zz, np.nan), 1), np.nan)
        src = ss.reshape(len(cols), -1)[:, 0]
    else:
        z, src, warnings = sample_first(x0 + cols * res, y0 + rows * res, srid, sources)
    grid = np.full((H, W), np.nan)
    grid[rows, cols] = z
    used = {}
    for k in np.unique(src[src >= 0]).tolist():
        s = sources[k]
        used[s.key] = {"title": s.title, "kind": s.kind, "res_m": round(s.res, 2),
                       "pct": round(100 * float((src == k).mean()), 1)}
    info = {"res_m": res, "samples": int(len(z)), "sources": list(used.values()),
            "lidar_pct": round(sum(u["pct"] for u in used.values() if u["kind"] == "lidar"), 1),
            "warnings": warnings}
    return Grid(grid, x0, y0, res), info


_twi_cache = {}             # signature -> Grid; the worker is long-lived, so the panel's
                           # baseline and the run that follows read the window once


def build_twi(legs_xy, srid, sources, corridor_m):
    """The wet-ground layer for the refine tool: TWI on a TWI_RES_M grid
    reaching TWI_BUFFER_M past the corridor, so upslope area that close is
    counted (trailgeek_analysis.refine.twi; deliberately light)."""
    h = hashlib.sha1(json.dumps([srid, round(corridor_m, 2), [s.key for s in sources],
                                 [np.round(L, 2).tolist() for L in legs_xy]]).encode()).hexdigest()
    if h in _twi_cache:
        return _twi_cache[h]
    g, _ = build_grid(legs_xy, srid, sources, corridor_m, res=TWI_RES_M, margin_m=TWI_BUFFER_M, sub=TWI_SUB)
    t, _, _ = twi(g.z, g.res)
    out = Grid(t, g.x0, g.y0, g.res)
    while len(_twi_cache) >= TWI_CACHE:
        _twi_cache.pop(next(iter(_twi_cache)))
    _twi_cache[h] = out
    return out


def simplify(xy, tol=SIMPLIFY_M):
    """Douglas-Peucker, keeping both ends."""
    xy = np.asarray(xy, float)
    n = len(xy)
    if n < 3:
        return xy
    keep = np.zeros(n, bool)
    keep[0] = keep[-1] = True
    stack = [(0, n - 1)]
    while stack:
        a, b = stack.pop()
        if b <= a + 1:
            continue
        p, q = xy[a], xy[b]
        seg = q - p
        L = math.hypot(*seg)
        pts = xy[a + 1:b]
        if L < 1e-12:
            dist = np.hypot(*(pts - p).T)
        else:
            dist = np.abs(seg[0] * (pts[:, 1] - p[1]) - seg[1] * (pts[:, 0] - p[0])) / L
        i = int(np.argmax(dist))
        if dist[i] > tol:
            m = a + 1 + i
            keep[m] = True
            stack += [(a, m), (m, b)]
    return xy[keep]


def _to_lonlat(legs_xy, srid):
    out = []
    for L in legs_xy:
        xy = simplify(L)
        lon, lat = transform_xy(xy[:, 0], xy[:, 1], srid, 4326)
        out.append([[round(float(a), 7), round(float(b), 7)] for a, b in zip(lon, lat)])
    return out


def run_job(job, payload):
    """payload: alignment_id, legs [first, last] (indices, inclusive),
    settings (full, cleaned), mode "run" | "baseline", user_id. Or, to score
    any one line (Factors along the line on a trail or a track): `line`
    (lon/lat) and `existing` (is it existing tread?) in place of the
    alignment, with mode "baseline"."""
    from .evaluation import choose_sources
    from .models import Alignment

    base = {"user": payload["user_id"], "alignment": payload.get("alignment_id"), "mode": payload["mode"],
            "legs": payload.get("legs")}
    state = dict(base, status="running", stage="Reading terrain for the corridor", progress=0.0)
    put_state(job, state)
    t0 = time.time()
    try:
        if payload.get("line"):
            a, project = None, None
            lines = [LineString(payload["line"], srid=4326)]
            kinds = ["existing" if payload.get("existing") else "new"]
        else:
            a = Alignment.objects.select_related("project").get(pk=payload["alignment_id"])
            project = a.project
            legs = list(a.legs.order_by("order"))
            i0, i1 = payload["legs"]
            sel = legs[i0:i1 + 1]
            lines = [LineString(list(l.geom.coords), srid=4326) for l in sel]
            kinds = [l.kind for l in sel]
        whole = MultiLineString(*lines, srid=4326)
        c = whole.centroid
        srid = geo.utm_srid(c.x, c.y)
        legs_xy, existing_xy = [], []
        for kind, line in zip(kinds, lines):
            cs = np.asarray(line.coords, float)
            x, y = transform_xy(cs[:, 0], cs[:, 1], 4326, srid)
            legs_xy.append(np.column_stack([x, y]))
            if kind == "existing":
                existing_xy.append(legs_xy[-1])         # the tread "Off existing tread" prefers
        settings = payload["settings"]
        grid, terrain = build_grid(legs_xy, srid, choose_sources(whole, project),
                                   float(settings["params"]["corridor_m"]))
        if payload["mode"] == "baseline":
            settings = dict(settings, params=dict(settings["params"], chains=0))
        last = [0.0]

        def progress(frac, best):
            if frac < 1 and frac - last[0] < 0.02:
                return
            last[0] = frac
            put_state(job, dict(base, status="running", stage="Searching", progress=round(frac, 3),
                                best=round(float(best), 4), elapsed_s=round(time.time() - t0, 1)))

        put_state(job, dict(base, status="running", stage="Searching", progress=0.0))
        put_state(job, dict(base, status="running", stage="Wetness index (TWI)", progress=0.0))
        twi_grid = build_twi(legs_xy, srid, choose_sources(whole, project), float(settings["params"]["corridor_m"]))
        res = run_refine(legs_xy, grid, settings, progress=progress, twi_grid=twi_grid, existing_xy=existing_xy)
        for d in [res["input"]] + res["candidates"]:
            d["legs"] = _to_lonlat(d.pop("legs_xy"), srid)
            xy = np.asarray(d["tracks"].pop("xy"), float)
            lon, lat = transform_xy(xy[:, 0], xy[:, 1], srid, 4326)
            d["tracks"]["lonlat"] = [[round(float(a), 7), round(float(b), 7)] for a, b in zip(lon, lat)]
        res.update({"srid": srid, "terrain": terrain, "signature": legs_signature(a) if a else None,
                    "settings": payload["settings"], "elapsed_s": round(time.time() - t0, 1),
                    "leg_ids": [l.pk for l in sel] if a else []})
        put_state(job, dict(base, status="done", progress=1.0, result=res))
    except ValueError as e:
        put_state(job, dict(base, status="failed", error=str(e)))
    except Exception as e:                         # noqa: BLE001 - reported to the panel, logged in full
        log.exception("refine job %s failed", job)
        put_state(job, dict(base, status="failed", error=f"{type(e).__name__}: {e}"))
