"""Topographic wetness index, deliberately light (owner, 2026-10-17): the
foundational water work belongs in topobathy, the shared base map; this is
a quick TWI to weight the refine tool by wet ground meanwhile.

    TWI = ln(a / tan b)

a is the specific catchment area (upslope area per unit contour width, m)
from multiple-flow-direction routing (Freeman 1991, exponent 1.1) over the
DEM with every pit filled (priority-flood + epsilon, Barnes et al. 2014),
and tan b the local slope, floored. Light means: no culverts, no breaching,
closed basins filled (so they read as wet flats, which they often are),
and catchments cut off at the window's edge (the caller's buffer sets how
far upslope is counted; cells near the edge read drier than they are).
"""
import heapq
import math

import numpy as np

FREEMAN_P = 1.1
TAN_MIN = 0.01        # a flat cell's slope, so a filled basin is wet, not infinite
EPS = 1e-4            # m: the fill's gradient across a flat, so water still drains

# Neighbour offsets (row, col) and their distances in cells.
_NB = [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]
_DIST = np.array([math.hypot(dr, dc) for dr, dc in _NB])


def fill_pits(z):
    """Priority-flood with epsilon: every cell drains to the edge of the
    data (the grid's border, or a nan). Returns the filled surface."""
    h, w = z.shape
    valid = np.isfinite(z)
    out = np.where(valid, z, np.nan)
    pad = np.pad(valid, 1, constant_values=False)
    inner = valid.copy()
    for dr, dc in _NB:
        inner &= pad[1 + dr:1 + dr + h, 1 + dc:1 + dc + w]
    done = ~valid | ~inner
    heap = [(float(out[r, c]), int(r), int(c)) for r, c in np.argwhere(valid & ~inner)]
    heapq.heapify(heap)
    while heap:
        e, r, c = heapq.heappop(heap)
        for dr, dc in _NB:
            rr, cc = r + dr, c + dc
            if 0 <= rr < h and 0 <= cc < w and not done[rr, cc]:
                done[rr, cc] = True
                if out[rr, cc] <= e:
                    out[rr, cc] = e + EPS
                heapq.heappush(heap, (float(out[rr, cc]), rr, cc))
    return out


def twi(z, res):
    """TWI on grid z (nan = no data) with cell size res metres. Returns
    (twi, specific catchment area a, tan b); nan where z is nan."""
    z = np.asarray(z, float)
    h, w = z.shape
    f = fill_pits(z)
    valid = np.isfinite(f)
    pad = np.pad(f, 1, constant_values=np.nan)
    # Downhill drop to each neighbour, per unit distance; 0 if not downhill.
    S = np.zeros((h, w, 8))
    for k, (dr, dc) in enumerate(_NB):
        nb = pad[1 + dr:1 + dr + h, 1 + dc:1 + dc + w]
        S[..., k] = np.where(np.isfinite(nb), np.maximum(f - nb, 0.0), 0.0) / (_DIST[k] * res)
    W = S ** FREEMAN_P
    tot = W.sum(axis=2)
    W = np.where(tot[..., None] > 0, W / np.where(tot > 0, tot, 1)[..., None], 0.0)
    # Local slope: steepest descent on the unfilled ground where it exists.
    zp = np.pad(np.where(np.isfinite(z), z, f), 1, constant_values=np.nan)
    tanb = np.zeros((h, w))
    for k, (dr, dc) in enumerate(_NB):
        nb = zp[1 + dr:1 + dr + h, 1 + dc:1 + dc + w]
        tanb = np.fmax(tanb, np.where(np.isfinite(nb), (zp[1:-1, 1:-1] - nb) / (_DIST[k] * res), 0.0))
    tanb = np.maximum(tanb, TAN_MIN)

    # Accumulate cells (each starts with its own area) from the top down.
    acc = np.where(valid, 1.0, 0.0).ravel()
    order = np.argsort(np.where(valid, f, -np.inf).ravel(), kind="stable")[::-1]
    order = order[valid.ravel()[order]]
    Wf = W.reshape(-1, 8)
    offs = [dr * w + dc for dr, dc in _NB]
    rows_of = order // w
    cols_of = order % w
    for i, r, c in zip(order.tolist(), rows_of.tolist(), cols_of.tolist()):
        a = acc[i]
        wi = Wf[i]
        for k in range(8):
            if wi[k] > 0:
                dr, dc = _NB[k]
                if 0 <= r + dr < h and 0 <= c + dc < w:
                    acc[i + offs[k]] += a * wi[k]
    area = acc.reshape(h, w) * res * res
    a = area / res                       # per unit contour width (the cell's width)
    with np.errstate(divide="ignore"):
        out = np.where(valid, np.log(np.where(valid, a, 1.0) / tanb), np.nan)
    return out, np.where(valid, a, np.nan), np.where(valid, tanb, np.nan)
