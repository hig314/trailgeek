"""The corridor search (owner, 2026-10-26): the cheapest line through the
search's limit by dynamic programming over cross-sections, a seed for the
annealer beside the smooth fit and the route-finder (docs/REFINE_TOOL.md §26).

Cross-sections ("stations") are laid STATION_M apart along the smooth fit to
the person's line, each with candidate points OFFSET_M apart across it, out to
the search's limit. A link joins a point on one station to a point on the
next; the line is a chain of links, one per station, and the cheapest chain
is found exactly (for these costs), not by random moves. Each link's cost is
the scoring's own per-metre charges, read on the link: its length along the
slope times 1 + the weighted curves for grade, side slope, trail-slope
alignment, fall-line tread, wet ground and distance from existing tread; a
turn at a point costs the bend factor for the radius it implies. The state
is a point and the link that reached it, so turns can be charged, and grade
is read over the two links, 10 m, as the scoring reads it (the climb and the
descent within them over their length).

What it cannot see: anything that needs the whole profile (the design grade
line through the cut/fill tube, excess climb, drainage runs) and corners.
The steepest 10 m, charged again by the scoring, is a maximum, which a sum
of link costs cannot express, so the search is run under several grade caps
(links steeper than the cap are not allowed; the standard way to bring a
maximum into dynamic programming), and the caller keeps whichever line the
real scoring likes best (2026-10-26: uncapped, its line on the gully reached
37 % where the best known holds 15 %). Its line is a seed, scored and
polished by the real scoring like every other chain. It cannot double back past a
station, so it never makes a switchback: that is the route-finder's job.
"""
import math

import numpy as np

from .score import FALL_MIN_SLOPE_PCT, resample_legs
from .terrain import Grid

STATION_M = 5.0           # m between cross-sections
SMOOTH_M = 5.0            # m: the ground is read as its mean over a square this wide (the design grade line
                          # cuts and fills through smaller bumps; on raw 1 m lidar the links' grades were noise)
OFFSET_M = 1.0            # m between candidate points across one
MAX_SKEW_DEG = 70.0       # a link runs at most this far off the stations' own direction (50 missed a cone's spiral)
MAX_WORK = 4e7            # stations x points x moves^2 beyond this: the points are spaced wider
CAP_SHARES = (None, 1.0, 0.8, 0.65, 0.5, 0.4)   # grade caps, as shares of the uncapped line's steepest link


def _smoothed(prob):
    """The ground averaged over SMOOTH_M (nan where any of it is missing),
    made once per problem."""
    if getattr(prob, "_corridor_z", None) is None:
        g = prob.grid
        r = max(1, int(round(SMOOTH_M / g.res / 2)))
        z = np.asarray(g.z, float)
        okz = np.isfinite(z)
        c = np.pad(np.where(okz, z, 0.0), r).cumsum(0).cumsum(1)
        n = np.pad(okz.astype(float), r).cumsum(0).cumsum(1)
        box = lambda a: (a[2 * r:, 2 * r:] - a[:-2 * r, 2 * r:] - a[2 * r:, :-2 * r] + a[:-2 * r, :-2 * r])
        sm = box(c)[:z.shape[0], :z.shape[1]]
        cnt = box(n)[:z.shape[0], :z.shape[1]]
        full = (2 * r) ** 2
        prob._corridor_z = Grid(np.where(cnt >= full - 1e-9, sm / np.maximum(cnt, 1), np.nan), g.x0 - 0.5 * g.res,
                                g.y0 - 0.5 * g.res, g.res)
    return prob._corridor_z


def _frames(prob, step):
    """Stations along the smooth fit, with unit tangents and left normals
    smoothed over a few stations, so cross-sections at a bend fan out
    rather than cross at once."""
    S, _ = resample_legs([prob.curve(prob.start())], step)
    t = np.gradient(S, axis=0)
    k = np.ones(5) / 5
    t = np.column_stack([np.convolve(np.pad(t[:, i], 2, mode="edge"), k, mode="valid") for i in (0, 1)])
    t /= np.maximum(np.hypot(t[:, 0], t[:, 1]), 1e-9)[:, None]
    return S, t, np.column_stack([-t[:, 1], t[:, 0]])


def corridor_lines(prob, k, progress=None):
    """The cheapest chain of links through the limit under weights `k`, with
    no grade cap and then under each of CAP_SHARES of its own steepest link.
    Returns [(cap %, polyline, info)] for each cap under which a chain fits."""
    out = []
    steepest = None
    for n_c, share in enumerate(CAP_SHARES):
        cap = None if share is None else share * steepest
        R, info = corridor_line(prob, k, cap,
                                None if progress is None else lambda fr, n_c=n_c: progress((n_c + fr) / len(CAP_SHARES)))
        if R is None:
            if share is None:
                return out                            # nothing fits at all
            continue
        if share is None:
            steepest = info["grade_max"]
        out.append((cap, R, info))
    return out


def corridor_line(prob, k, cap=None, progress=None):
    """The cheapest chain of links through the limit, under weights `k` and no
    link steeper than `cap` % (None: no cap), as an (m, 2) polyline from the
    line's start to its end, or None if no chain fits. Also returns a small
    info dict (stations, points, estimate, steepest link, ...)."""
    S, T, N = _frames(prob, STATION_M)
    ns = len(S)
    if ns < 3:
        return None, {"reason": "too short"}
    do = OFFSET_M
    w_ = lambda do: max(1, int(math.ceil(STATION_M * math.tan(math.radians(MAX_SKEW_DEG)) / do)))
    while ns * (2 * int(prob.limit / do) + 1) * (2 * w_(do) + 1) ** 2 > MAX_WORK:
        do *= 2
    J = int(prob.limit / do)
    o = (np.arange(2 * J + 1) - J) * do
    M, w = len(o), w_(do)
    dl = np.arange(-w, w + 1)
    D = len(dl)

    X = S[:, 0, None] + o[None, :] * N[:, 0, None]
    Y = S[:, 1, None] + o[None, :] * N[:, 1, None]
    zg = _smoothed(prob)
    z = zg.sample(X, Y)
    dist = prob.dist_grid.sample(X, Y)
    ok = np.isfinite(z) & np.isfinite(dist) & (dist <= prob.limit + 1e-6)
    ok[0], ok[-1] = False, False
    ok[0, J] = ok[-1, J] = True                       # the ends are the line's own
    X[0, J], Y[0, J], X[-1, J], Y[-1, J] = *prob.P[0], *prob.P[-1]
    z[0, J], z[-1, J] = zg.sample(*prob.P[0]), zg.sample(*prob.P[-1])
    gx = np.nan_to_num(prob.side_grids[0].sample(X, Y))
    gy = np.nan_to_num(prob.side_grids[1].sample(X, Y))

    f = prob.f
    kk = lambda name: float(k.get(name, 0))
    # The per-metre charges that do not depend on the link's direction.
    static = kk("slope") * f["slope"](100 * np.hypot(gx, gy))
    if prob.twi_grid is not None and kk("wet"):
        static = static + kk("wet") * f["wet"](np.nan_to_num(prob.twi_grid.sample(X, Y)))
    if prob.existing_grid is not None and kk("existing"):
        static = static + kk("existing") * f["existing"](np.nan_to_num(prob.existing_grid.sample(X, Y)))
    cos_skew = math.cos(math.radians(MAX_SKEW_DEG))
    jj = np.arange(M)[:, None] + np.zeros(D, int)[None, :]           # (M, D): this point
    jp = jj - dl[None, :]                                            # ... and the one before it

    def links(i):
        """(M, D) cost of the link into point j of station i from j - dl."""
        a = np.clip(jp, 0, M - 1)
        valid = ok[i][jj] & (jp >= 0) & (jp < M) & ok[i - 1][a]
        dx, dy = X[i][jj] - X[i - 1][a], Y[i][jj] - Y[i - 1][a]
        dz = z[i][jj] - z[i - 1][a]
        l2 = np.hypot(dx, dy)
        tm = (T[i] + T[i - 1]) / 2
        valid &= (dx * tm[0] + dy * tm[1]) > cos_skew * l2
        l2s = np.maximum(l2, 1e-6)
        ux, uy = dx / l2s, dy / l2s
        mgx, mgy = (gx[i][jj] + gx[i - 1][a]) / 2, (gy[i][jj] + gy[i - 1][a]) / 2
        along, cross = mgx * ux + mgy * uy, -mgx * uy + mgy * ux
        tsa = np.degrees(np.arctan2(np.abs(cross), np.abs(along)))
        tsa = np.where(100 * np.hypot(mgx, mgy) < FALL_MIN_SLOPE_PCT, 90.0, tsa)
        l3 = np.hypot(l2, dz)
        c = l3 * (1 + (static[i][jj] + static[i - 1][a]) / 2 + kk("tsa") * f["tsa"](tsa))
        valid &= np.isfinite(c)
        # Off the data a point has no elevation: its links are barred, and their
        # grade must not be nan, or argmin would prefer it to every finite cost.
        return (np.where(valid, c, np.inf), ux, uy, l2, np.where(valid, np.abs(dz), 0.0), np.where(valid, l3, 0.0),
                tsa < prob.fall_tsa)

    def grade_cost(gw, l3, fall):
        """Grade and fall-line tread for a link, at the grade over 10 m."""
        c = l3 * (kk("grade") * f["grade"](gw) + kk("falltread") * f["falltread"](gw) * fall)
        return c if cap is None else np.where(gw <= cap, c, np.inf)

    kb = kk("bend")
    V, ux0, uy0, l20, adz0, l3_, fall_ = links(1)
    V = V + grade_cost(100 * adz0 / np.maximum(l20, 1e-6), l3_, fall_)
    V = np.where(jp == J, V, np.inf)                  # the first link leaves the start
    back = np.zeros((ns, M, D), np.int8)
    for i in range(2, ns):
        L, ux, uy, l2, adz, l3, fall = links(i)
        # The previous point is (i-1, jp); it was reached by its own move b.
        a = np.clip(jp, 0, M - 1)
        Vp = V[a]                                     # (M, D, D): [j, this move, previous move]
        pl2 = l20[a]
        gw = 100 * (adz0[a] + adz[:, :, None]) / np.maximum(pl2 + l2[:, :, None], 1e-6)
        Vp = Vp + grade_cost(gw, l3[:, :, None], fall[:, :, None])
        if kb:
            pux, puy = ux0[a], uy0[a]
            cosp = np.clip(pux * ux[:, :, None] + puy * uy[:, :, None], -1, 1)
            phi = np.arccos(cosp)
            r = np.minimum(pl2, l2[:, :, None]) / 2 / np.maximum(np.tan(phi / 2), 1e-9)
            Vp = Vp + kb * f["bend"](np.minimum(r, 1e4)) * phi / (math.pi / 2)
        Vp = np.where((jp >= 0)[:, :, None] & (jp < M)[:, :, None], Vp, np.inf)
        b = np.argmin(Vp, axis=2)
        back[i] = b
        V = L + np.take_along_axis(Vp, b[:, :, None], axis=2)[:, :, 0]
        ux0, uy0, l20, adz0 = ux, uy, l2, adz
        if progress and i % 50 == 0:
            progress(i / ns)
    a = int(np.argmin(V[J]))
    best = float(V[J, a])
    if not math.isfinite(best):
        return None, {"reason": "no chain fits the limit"}
    js = [J]
    j = J
    for i in range(ns - 1, 0, -1):
        jprev = j - int(dl[a])
        a = int(back[i, j, a]) if i > 1 else a
        j = jprev
        js.append(j)
    js = js[::-1]
    R = np.column_stack([X[np.arange(ns), js], Y[np.arange(ns), js]])
    adz_, l2_ = np.abs(np.diff(z[np.arange(ns), js])), np.hypot(*np.diff(R, axis=0).T)
    gmax = float((100 * (adz_[1:] + adz_[:-1]) / np.maximum(l2_[1:] + l2_[:-1], 1e-6)).max())
    return R, {"stations": ns, "points": M, "offset_m": do, "estimate": round(best, 1),
               "offset_max_m": float(np.abs(o[js]).max()), "grade_max": round(gmax, 1),
               "cap": None if cap is None else round(cap, 1)}
