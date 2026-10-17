"""One candidate, scored: geometry from the offset field, ground and bench
cross-sections from the Grid, the design grade line, corners and bends, the
drainage stub, and every factor's cost (docs/REFINE_TOOL.md §2-3).

Costs: a per-metre factor is the integral of its curve along the line
divided by the INPUT length, so making the line longer does not dilute it
(length has its own factor). A per-corner factor is summed over corners. The
total is the sum of k * cost.
"""
import math

import numpy as np

from . import spline
from .profile import taut_string
from .settings import FACTOR_META

CORNER_KAPPA = 1 / 50.0     # 1/m: curvature that counts as still turning, around a flip
CORNER_HALF_M = 25.0        # a corner's turn zone reaches at most this far beyond its flips
CORNER_CLUSTER_M = 20.0     # side flips closer than this are one event
CORNER_MIN_TURN_DEG = 60.0  # a side change with less turning than this is a crossing, not a corner
GRADE_OVER_PER = 10.0       # %: each this much past the input's steepest 10 m adds 1 to the grade cost
GRADE_PEAK_SHARE = 0.25     # the steepest 10 m adds this times its curve value to the grade cost
SITE_MERGE_M = 15.0         # reversal sites closer than this are one site
DIP_WINDOW_M = 10.0         # a ground dip is measured against the ground this far either side


def running_mean(v, w):
    """Centred mean over an odd window of w samples, shrinking at the ends."""
    if w <= 1:
        return v.copy()
    h = w // 2
    c = np.concatenate([[0.0], np.cumsum(v)])
    n = len(v)
    i = np.arange(n)
    lo, hi = np.maximum(0, i - h), np.minimum(n, i + h + 1)
    return (c[hi] - c[lo]) / (hi - lo)


def resample_legs(legs_xy, spacing):
    """Even spacing within each leg, joints kept as samples. Returns the
    points and the sample index of every leg boundary (first 0, last n-1)."""
    parts, joints = [], [0]
    for xy in legs_xy:
        xy = np.asarray(xy, float)
        keep = np.concatenate([[True], np.hypot(*np.diff(xy, axis=0).T) > 1e-6])
        xy = xy[keep]
        if len(xy) < 2:
            raise ValueError("a leg has fewer than two distinct points")
        cum = np.concatenate([[0.0], np.cumsum(np.hypot(*np.diff(xy, axis=0).T))])
        m = max(1, int(round(cum[-1] / spacing)))
        t = np.linspace(0, cum[-1], m + 1)
        p = np.column_stack([np.interp(t, cum, xy[:, 0]), np.interp(t, cum, xy[:, 1])])
        if parts:
            p = p[1:]
        parts.append(p)
        joints.append(joints[-1] + m)
    return np.vstack(parts), joints


def curve_fn(curve):
    xs = np.array([c[0] for c in curve], float)
    ys = np.array([c[1] for c in curve], float)
    return lambda x: np.interp(x, xs, ys)


def side_of_downhill(cross, thr):
    """+1 where the ground falls to the right (left side higher), -1 to the
    left, held through stretches flatter than `thr` (hysteresis), so a flat
    patch or a boulder does not flip it. Leading flat samples take the
    first decided side."""
    s = np.where(cross > thr, 1, np.where(cross < -thr, -1, 0))
    nz = s != 0
    if not nz.any():
        return np.zeros_like(s)
    idx = np.where(nz, np.arange(len(s)), 0)
    idx = np.maximum.accumulate(idx)
    out = s[idx]
    out[:np.argmax(nz)] = s[np.argmax(nz)]
    return out


def reversal_sites(d, zd, g, dip):
    """THE DRAINAGE STUB (docs/REFINE_TOOL.md §2): deliberately primitive.
    A site is (a) a low point of the design line, where it already turns from
    descending to climbing, or (b) a ground dip deeper than `dip` against the
    ground DIP_WINDOW_M either side: an easy place to build one. No
    groundwater, no contributing area, no look at the terrain below the
    trail yet."""
    L = d[-1]
    dz = np.diff(zd)
    lows = np.flatnonzero((dz[:-1] < -1e-6) & (dz[1:] >= -1e-6)) + 1
    # A low point counts only when the line is at least `dip` higher on
    # both sides within the window: a millimetre wobble drains nothing.
    keep = []
    for i in lows.tolist():
        a = np.searchsorted(d, d[i] - DIP_WINDOW_M)
        b = np.searchsorted(d, d[i] + DIP_WINDOW_M, side="right")
        if min(zd[a:i + 1].max(), zd[i:b].max()) - zd[i] >= dip:
            keep.append(i)
    lows = np.asarray(keep, int)
    gl = np.interp(np.maximum(d - DIP_WINDOW_M, 0), d, g)
    gr = np.interp(np.minimum(d + DIP_WINDOW_M, L), d, g)
    e = g - (gl + gr) / 2
    inner = e[1:-1]
    dips = np.flatnonzero((inner < -dip) & (inner <= e[:-2]) & (inner <= e[2:])) + 1
    out = []
    for i in sorted(set(lows.tolist()) | set(dips.tolist())):
        if not out or d[i] - d[out[-1]] >= SITE_MERGE_M:
            out.append(i)
    return out


def intersects(Q, stride):
    """True if the polyline crosses itself (checked on every `stride`-th
    vertex, non-adjacent segments only)."""
    S = Q[::stride]
    if len(Q) and (len(Q) - 1) % stride:
        S = np.vstack([S, Q[-1]])
    A, B = S[:-1], S[1:]
    m = len(A)
    if m < 3:
        return False
    def cross(px, py, qx, qy):
        return px * qy - py * qx
    ex, ey = (B - A)[:, 0], (B - A)[:, 1]
    # o1 = (B_i - A_i) x (A_j - A_i), etc., as (m, m) arrays
    ajx = A[None, :, 0] - A[:, None, 0]
    ajy = A[None, :, 1] - A[:, None, 1]
    bjx = B[None, :, 0] - A[:, None, 0]
    bjy = B[None, :, 1] - A[:, None, 1]
    o1 = cross(ex[:, None], ey[:, None], ajx, ajy)
    o2 = cross(ex[:, None], ey[:, None], bjx, bjy)
    o3 = cross(ex[None, :], ey[None, :], -ajx, -ajy)
    o4 = cross(ex[None, :], ey[None, :], (B[:, None, 0] - A[None, :, 0]), (B[:, None, 1] - A[None, :, 1]))
    hit = (o1 * o2 < 0) & (o3 * o4 < 0)
    return bool(np.triu(hit, 2).any())


class Problem:
    """The fixed parts of a refinement: the input resampled, its normals,
    the spline basis, the terrain and the curves. `score(c, t)` scores the
    candidate with free control values `c` and cut/fill tube half-width `t`."""

    def __init__(self, legs_xy, grid, settings):
        p = settings["params"]
        self.params = p
        self.grid = grid
        self.h = float(p["spacing_m"])
        self.P, self.joints = resample_legs(legs_xy, self.h)
        n = len(self.P)
        if n < 6:
            raise ValueError("the stretch is too short to refine")
        self.n = n
        self.d_in = np.concatenate([[0.0], np.cumsum(np.hypot(*np.diff(self.P, axis=0).T))])
        self.L_in = float(self.d_in[-1])
        i = np.arange(n)
        a = self.P[np.minimum(i + 2, n - 1)] - self.P[np.maximum(i - 2, 0)]
        T = a / np.maximum(np.hypot(a[:, 0], a[:, 1]), 1e-9)[:, None]
        self.N = np.column_stack([-T[:, 1], T[:, 0]])          # left normal
        K = max(4, int(round(self.L_in / float(p["knot_m"]))) + 3)
        B = spline.basis(self.d_in / self.L_in, K)
        self.B = B[:, 1:K - 1]                                 # ends pinned at 0
        self.nfree = K - 2
        g = spline.greville(K)[1:K - 1] * self.L_in
        self.knot_idx = np.clip(np.searchsorted(self.d_in, g), 0, n - 1)
        self.base_k = {f: float(v["k"]) for f, v in settings["factors"].items()}
        self.f = {f: curve_fn(v["curve"]) for f, v in settings["factors"].items()}
        self.corridor = float(p["corridor_m"])
        self.tmax = float(p["cutfill_max_m"])
        self.bench = float(p["bench_m"])
        self.thr = float(p["min_outslope_pct"]) / 100
        self.dip = float(p["reversal_dip_m"])
        self.smooth_w = max(1, int(round(5 / self.h))) | 1
        self.side_w = max(1, int(round(10 / self.h))) | 1
        # The input's steepest 10 m, once known (search.refine sets it): going
        # past it costs without limit, so a saturated curve cannot hide a
        # worse pitch ("consistent grade is minimising the maximum", owner).
        self.grade_ref = None
        # A hand-drawn hairpin can turn more than a right angle within one
        # sample. That is the input as given (rounding it is the tool's job),
        # so only folds a candidate ADDS are refused.
        dP = np.diff(self.P, axis=0)
        self.input_folds = np.concatenate([(dP[1:] * dP[:-1]).sum(1) < 0])
        self.stride = max(1, int(round(5 / self.h)))

    def offsets(self, c):
        return self.B @ c

    def folds(self, c):
        o = self.offsets(c)
        return intersects(self.P + o[:, None] * self.N, self.stride)

    def score(self, c, t, k=None, detail=False):
        """None if the candidate is impossible (outside the corridor, folded,
        off the terrain data); else a dict with `total`, `costs` (per
        factor, before k), `density` (cost per sample, for aiming moves) and
        `corner_idx`; with detail=True also every array the UI shows."""
        k = self.base_k if k is None else k
        h, n = self.h, self.n
        o = self.offsets(c)
        if np.abs(o).max() > self.corridor + 1e-6:
            return None
        Q = self.P + o[:, None] * self.N
        dQ = np.diff(Q, axis=0)
        ds = np.hypot(dQ[:, 0], dQ[:, 1])
        if (ds < 0.25 * h).any() or (((dQ[1:] * dQ[:-1]).sum(1) < 0) & ~self.input_folds).any():
            return None
        d = np.concatenate([[0.0], np.cumsum(ds)])
        L = float(d[-1])
        w = np.gradient(d)
        tq = np.gradient(Q, axis=0)
        tq = tq / np.maximum(np.hypot(tq[:, 0], tq[:, 1]), 1e-9)[:, None]
        nq = np.column_stack([-tq[:, 1], tq[:, 0]])
        sample = self.grid.sample
        g = sample(Q[:, 0], Q[:, 1])
        if not np.isfinite(g).all():
            return None
        b2 = self.bench / 2
        zl = sample(Q[:, 0] + b2 * nq[:, 0], Q[:, 1] + b2 * nq[:, 1])
        zr = sample(Q[:, 0] - b2 * nq[:, 0], Q[:, 1] - b2 * nq[:, 1])
        zf = sample(Q[:, 0] + b2 * tq[:, 0], Q[:, 1] + b2 * tq[:, 1])
        zb = sample(Q[:, 0] - b2 * tq[:, 0], Q[:, 1] - b2 * tq[:, 1])
        for a in (zl, zr, zf, zb):
            bad = ~np.isfinite(a)
            a[bad] = g[bad]
        cross = (zl - zr) / self.bench
        along = (zf - zb) / self.bench
        side = 100 * np.hypot(cross, along)

        theta = np.unwrap(np.arctan2(tq[:, 1], tq[:, 0]))
        kap = running_mean(np.gradient(theta, d), self.smooth_w)

        # Corners (owner's definition: the downhill side changes; a gully
        # crossed while climbing is not one), read so that a broad corner
        # through a fall-line stretch is ONE corner (owner, 2026-10-17: noise
        # there flipped the side back and forth and one smooth corner read as
        # several, which the search then "fixed" with tight kinks):
        #   1. the side comes from the cross slope averaged over ~10 m, with
        #      hysteresis;
        #   2. flips closer than CORNER_CLUSTER_M are one event, and events
        #      whose turning zones touch are one zone;
        #   3. a zone is a corner only if the side AFTER it differs from the
        #      side BEFORE it (out and back is noise) and the line turns by at
        #      least CORNER_MIN_TURN_DEG across it (a side change on a
        #      straight line is crossing a crest or a valley floor, not a turn);
        #   4. its radius is the TIGHTEST local radius in the zone, so a kink
        #      inside a broad corner costs as the kink it is.
        cross_s = running_mean(cross, self.side_w)
        sgn = side_of_downhill(cross_s, self.thr)
        flips = (np.flatnonzero((sgn[1:] != sgn[:-1]) & (sgn[1:] != 0) & (sgn[:-1] != 0)) + 1).tolist()
        akap = np.abs(kap)
        turning = akap > CORNER_KAPPA
        zones = []
        k_ = 0
        while k_ < len(flips):
            f0 = f1 = flips[k_]
            while k_ + 1 < len(flips) and d[flips[k_ + 1]] - d[f1] <= CORNER_CLUSTER_M:
                k_ += 1
                f1 = flips[k_]
            k_ += 1
            a = f0
            while a > 0 and turning[a - 1] and d[f0] - d[a - 1] <= CORNER_HALF_M:
                a -= 1
            b = f1
            while b < n - 1 and turning[b + 1] and d[b + 1] - d[f1] <= CORNER_HALF_M:
                b += 1
            a, b = max(0, min(a, f0 - 1)), min(n - 1, max(b, f1))
            if zones and a <= zones[-1][1] + 1:
                zones[-1] = (zones[-1][0], max(b, zones[-1][1]), zones[-1][2] + [f0, f1])
            else:
                zones.append((a, b, [f0, f1]))
        zone = np.zeros(n, bool)
        corners = []
        wn = self.side_w
        for a, b, fl in zones:
            before = cross_s[max(0, a - wn):a].mean() if a > 0 else cross_s[0]
            after = cross_s[b + 1:b + 1 + wn].mean() if b < n - 1 else cross_s[-1]
            if not (abs(before) > self.thr / 2 and abs(after) > self.thr / 2 and np.sign(before) != np.sign(after)):
                continue
            dth = abs(theta[b] - theta[a])
            if math.degrees(dth) < CORNER_MIN_TURN_DEG:
                continue
            r_arc = (d[b] - d[a]) / max(dth, 1e-3)
            r_tight = 1 / max(float(akap[a:b + 1].max()), 1e-4)
            r = min(1000.0, r_arc, r_tight)
            mid = (a + b) // 2
            corners.append((mid, a, b, r, float(side[a:b + 1].mean()), math.degrees(dth), min(1000.0, r_arc)))
            zone[a:b + 1] = True
        bend_r = 1 / np.maximum(akap, 1e-4)

        # The design grade line through the cut/fill tube.
        zd = taut_string(d, g - t, g + t)
        cut = np.abs(zd - g)
        dp, dm = np.minimum(d + 5, L), np.maximum(d - 5, 0)
        grade = 100 * np.abs(np.interp(dp, d, zd) - np.interp(dm, d, zd)) / np.maximum(dp - dm, 1e-6)

        sites = reversal_sites(d, zd, g, self.dip)
        site_d = d[sites] if sites else np.zeros(0)
        if len(site_d):
            last = np.searchsorted(site_d, d, side="right") - 1
            run = d - np.where(last >= 0, site_d[np.maximum(last, 0)], 0.0)
        else:
            run = d.copy()

        off = np.abs(o)
        f = self.f
        per = {"grade": grade, "slope": side, "cutfill": cut, "offset": off, "drainage": run}
        vals = {name: f[name](x) for name, x in per.items()}
        # Bends are charged per right angle TURNED, weighted by the curve at
        # the radius it was turned at, not per metre: a kink is an event, and
        # per metre a 3 m jog on a 750 m line cost nothing (2026-10-17).
        # Corners are left to the corner factor.
        bend_v = f["bend"](bend_r) * ~zone
        vals["bend"] = bend_v
        costs = {name: float((v * w).sum() / self.L_in) for name, v in vals.items() if name != "bend"}
        costs["bend"] = float((bend_v * akap * w).sum() / (math.pi / 2))
        # "Consistent grade is minimising the maximum grade" (owner): a short
        # steep pitch is cheap per metre, so the peak is charged as well.
        gmax = float(grade.max())
        costs["grade"] += GRADE_PEAK_SHARE * float(f["grade"](gmax))
        if self.grade_ref is not None and gmax > self.grade_ref:
            costs["grade"] += (gmax - self.grade_ref) / GRADE_OVER_PER
        cr = [f["corner"](c_[3]) for c_ in corners]
        cl = [f["landing"](c_[4]) for c_ in corners]
        costs["corner"] = float(sum(cr))
        costs["landing"] = float(sum(cl))
        costs["length"] = float(f["length"](L / self.L_in))
        total = sum(k.get(name, 0) * v for name, v in costs.items())

        density = sum(k.get(name, 0) * v for name, v in vals.items() if name != "bend")
        density = density + k.get("bend", 0) * bend_v * akap * self.L_in / (math.pi / 2)
        for (fi, a, b, r, land, _, _r), x1, x2 in zip(corners, cr, cl):
            density[a:b + 1] += (k.get("corner", 0) * x1 + k.get("landing", 0) * x2) / (b - a + 1)
        out = {"total": float(total), "costs": costs, "density": density,
               "corner_idx": [c_[0] for c_ in corners], "length_m": L}
        if detail:
            out.update({
                "Q": Q, "d": d, "ground": g, "design": zd, "cut": cut, "grade": grade, "side": side,
                "offset": o, "bend_r": bend_r, "zone": zone, "run": run, "sites": sites,
                "corners": [{"i": c_[0], "d": float(d[c_[0]]), "radius_m": float(c_[3]),
                             "arc_radius_m": float(c_[6]), "landing_pct": c_[4], "turn_deg": c_[5]}
                            for c_ in corners],
                "w": w, "t": float(t),
                "contrib": {name: k.get(name, 0) * v for name, v in costs.items()},
            })
        return out


def histograms(r, bins=30):
    """The input's values per factor, binned over each factor's x range, for
    the curve editor's background: metres per bin for per-metre factors,
    counts for per-corner ones, the one value for length."""
    out = {}
    w = r["w"]
    src = {"grade": (r["grade"], w), "slope": (r["side"], w), "cutfill": (r["cut"], w),
           "offset": (np.abs(r["offset"]), w), "drainage": (r["run"], w),
           "bend": (r["bend_r"], w * ~r["zone"]),
           "corner": (np.array([c["radius_m"] for c in r["corners"]]), None),
           "landing": (np.array([c["landing_pct"] for c in r["corners"]]), None)}
    for name, (x, wt) in src.items():
        m = FACTOR_META[name]
        lo, hi = m["xmin"], m["xmax"]
        xc = np.clip(x, lo, hi - 1e-9 * (hi - lo))
        hgt, _ = np.histogram(xc, bins=bins, range=(lo, hi), weights=wt)
        out[name] = {"lo": lo, "hi": hi, "counts": [round(float(v), 2) for v in hgt],
                     "unit": "m" if wt is not None else "corners"}
    out["length"] = {"value": 1.0}
    return out
