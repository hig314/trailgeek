"""One candidate, scored: geometry from the offset field, ground and bench
cross-sections from the Grid, the design grade line, corners and bends, the
drainage stub, and every factor's cost (docs/REFINE_TOOL.md §2-3).

Costs: a per-metre factor is the integral of its curve along the line, per
100 m. A per-corner factor is summed over corners. Length is k per 100 m,
counted from the input's length. The total is the sum of k * cost.
"""
import math

import numpy as np

from . import spline
from .profile import taut_string
from .terrain import Grid
from .settings import FACTOR_META

CORNER_KAPPA = 1 / 50.0     # 1/m: curvature that counts as still turning, around a flip
CORNER_HALF_M = 25.0        # a corner's turn zone reaches at most this far beyond its flips
CORNER_CLUSTER_M = 20.0     # side flips closer than this are one event
CORNER_MIN_TURN_DEG = 60.0  # a side change with less turning than this is a crossing, not a corner
GRADE_OVER_PER = 10.0       # %: each this much past the input's steepest 10 m adds 1 to the grade cost
GRADE_PEAK_SHARE = 0.25     # the steepest 10 m adds this times its curve value to the grade cost
FALL_MIN_SLOPE_PCT = 4.0    # %: flatter ground has no fall line to speak of
CLIMB_TURN_MAX_PCT = 15.0   # %: a corner crossing the fall line on ground this gentle or gentler is a climbing turn
SWITCHBACK_MIN_PCT = 25.0   # %: ... this steep or steeper, switchback-like (a landing must be cut)
PER_M = 100.0               # per-metre factors are charged per this many metres
SITE_MERGE_M = 15.0         # reversal sites closer than this are one site
RESPACE_MAX_M = 0.25        # m: a re-spacing that would move the line more than this is skipped
SIGMA_REF = 3.0             # m: at this uncertainty or more, sketched vertices are filleted at the full sketch radius
FIT_SMOOTH = 0.5            # the start's curvature penalty per (sample per control) per m^2 of uncertainty
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


def intersects_changed(Q, Q_old, stride):
    """intersects(), checking only segments that moved since Q_old against
    all others: a move changes a stretch, so this is the same answer for a
    candidate whose predecessor was clean, at a fraction of the cost."""
    S, So = Q[::stride], Q_old[::stride]
    if len(Q) and (len(Q) - 1) % stride:
        S, So = np.vstack([S, Q[-1]]), np.vstack([So, Q_old[-1]])
    moved = np.abs(S - So).max(1) > 1e-9
    segc = np.flatnonzero(moved[:-1] | moved[1:])
    m = len(S) - 1
    if not len(segc) or m < 3:
        return False
    A, B = S[:-1], S[1:]
    ea, eb = A[segc], B[segc]
    e = eb - ea
    f = B - A
    def cr(ux, uy, vx, vy):
        return ux * vy - uy * vx
    o1 = cr(e[:, None, 0], e[:, None, 1], A[None, :, 0] - ea[:, None, 0], A[None, :, 1] - ea[:, None, 1])
    o2 = cr(e[:, None, 0], e[:, None, 1], B[None, :, 0] - ea[:, None, 0], B[None, :, 1] - ea[:, None, 1])
    o3 = cr(f[None, :, 0], f[None, :, 1], ea[:, None, 0] - A[None, :, 0], ea[:, None, 1] - A[None, :, 1])
    o4 = cr(f[None, :, 0], f[None, :, 1], eb[:, None, 0] - A[None, :, 0], eb[:, None, 1] - A[None, :, 1])
    hit = (o1 * o2 < 0) & (o3 * o4 < 0)
    far = np.abs(segc[:, None] - np.arange(m)[None, :]) >= 2
    return bool((hit & far).any())


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


def fit_controls(B, X, lam, row_w=None):
    """Controls of a clamped spline (basis B, n x K) fitted to points X by
    least squares, ends pinned to X's ends, second differences of the
    controls penalised by lam (times row_w per interior control)."""
    K = B.shape[1]
    X0, Xe = X[0], X[-1]
    Bi = B[:, 1:K - 1]
    rhs = X - np.outer(B[:, 0], X0) - np.outer(B[:, K - 1], Xe)
    D = np.zeros((K - 2, K))
    for i in range(K - 2):
        D[i, i:i + 3] = [1, -2, 1]
    Di = D[:, 1:K - 1]
    fixed = np.outer(D[:, 0], X0) + np.outer(D[:, K - 1], Xe)
    Wd = (lam * (np.ones(K - 2) if row_w is None else row_w))[:, None]
    A = Bi.T @ Bi + Di.T @ (Wd * Di)
    b = Bi.T @ rhs - Di.T @ (Wd * fixed)
    Ci = np.linalg.solve(A, b)
    return np.vstack([X0, Ci, Xe])


def fillet(xy, radius, step, r_min=1.5):
    """The polyline with each vertex replaced by a circular arc tangent to
    both segments: radius `radius`, reduced where the tangent length would
    take more than 45 % of a neighbouring segment, never under r_min.
    Arcs are sampled every `step` metres. Used only as the FIT TARGET for
    the start (the person's line stays the reference): fitting a smooth
    curve to a sharp hairpin tip otherwise forces a cusp."""
    xy = np.asarray(xy, float)
    if len(xy) < 3:
        return xy
    seg = np.diff(xy, axis=0)
    L = np.hypot(seg[:, 0], seg[:, 1])
    U = seg / np.maximum(L, 1e-12)[:, None]
    out = [xy[0]]
    for i in range(1, len(xy) - 1):
        a, b = U[i - 1], U[i]
        cosd = float(np.clip(a @ b, -1, 1))
        D = math.acos(cosd)
        if D < math.radians(2):
            out.append(xy[i])
            continue
        tan_h = math.tan(min(D / 2, math.radians(89.5)))
        r = max(r_min, min(radius, 0.45 * min(L[i - 1], L[i]) / tan_h))
        T = min(r * tan_h, 0.49 * min(L[i - 1], L[i]))
        r = T / tan_h
        V = xy[i]
        p1 = V - a * T
        turn = 1.0 if (a[0] * b[1] - a[1] * b[0]) > 0 else -1.0
        n1 = np.array([-a[1], a[0]]) * turn
        c = p1 + n1 * r
        ang0 = math.atan2(p1[1] - c[1], p1[0] - c[0])
        m = max(2, int(math.ceil(r * D / step)))
        for j in range(m + 1):
            ang = ang0 + turn * D * j / m
            out.append(c + r * np.array([math.cos(ang), math.sin(ang)]))
    out.append(xy[-1])
    out = np.asarray(out)
    keep = np.concatenate([[True], np.hypot(*np.diff(out, axis=0).T) > 1e-6])
    return out[keep]


def polyline_distance(px, py, xy, chunk=20000):
    """For points (px, py): distance to the polyline `xy` and the arc
    position along it of the nearest point."""
    A, B = xy[:-1], xy[1:]
    seg = B - A
    L2 = np.maximum((seg ** 2).sum(1), 1e-12)
    s0 = np.concatenate([[0.0], np.cumsum(np.sqrt(L2))])[:-1]
    dist = np.empty(len(px))
    pos = np.empty(len(px))
    for i in range(0, len(px), chunk):
        x, y = px[i:i + chunk, None], py[i:i + chunk, None]
        t = np.clip(((x - A[:, 0]) * seg[:, 0] + (y - A[:, 1]) * seg[:, 1]) / L2, 0, 1)
        dx, dy = x - (A[:, 0] + t * seg[:, 0]), y - (A[:, 1] + t * seg[:, 1])
        d2 = dx * dx + dy * dy
        j = np.argmin(d2, axis=1)
        r = np.arange(len(j))
        dist[i:i + chunk] = np.sqrt(d2[r, j])
        pos[i:i + chunk] = s0[j] + t[r, j] * np.sqrt(L2[j])
    return dist, pos


def corner_allowance(xy, radius, cap):
    """How far a line that rounds each sketched vertex to `radius` may stand
    off the sketch, as a function of arc position along it: at a vertex that
    turns by D the rounded apex is radius * (sec(D/2) - 1) away, tapering to
    0 over the tangent length radius * tan(D/2) either side. Returns
    (vertex positions, allowances, tangent lengths)."""
    v = np.diff(xy, axis=0)
    L = np.hypot(v[:, 0], v[:, 1])
    s_v = np.cumsum(L)[:-1]
    h = np.arctan2(v[:, 1], v[:, 0])
    D = np.abs((np.diff(h) + np.pi) % (2 * np.pi) - np.pi)
    half = np.minimum(D / 2, np.radians(85))
    A = np.minimum(radius * (1 / np.cos(half) - 1), cap)
    T = np.minimum(radius * np.tan(half), 3 * radius)
    keep = A > 0.05
    return s_v[keep], A[keep], np.maximum(T[keep], 1.0)


def allowance_at(pos, s_v, A, T):
    out = np.zeros(len(pos))
    for sv, a, t in zip(s_v, A, T):
        out = np.maximum(out, a * np.clip(1 - np.abs(pos - sv) / t, 0, 1))
    return out


class Problem:
    """The fixed parts of a refinement (docs/REFINE_TOOL.md, "the sketch as
    evidence", 2026-10-17).

    The person's line is EVIDENCE of an intended path, drawn with sharp
    corners they expect to be rounded, not the frame the answer is built on.
    So a candidate is its own smooth curve, a clamped cubic B-spline through
    control points in the plane with its ends pinned to the line's ends, and
    the line enters only through the distance cost: distance from it, less a
    corner allowance at each sketched vertex (what rounding it to
    `sketch_radius_m` needs), less the line's own uncertainty
    (`input_sigma_m`). Both are precomputed on the terrain grid's cells.

    The start is a smooth fit to the line: least squares with a curvature
    penalty that grows with the uncertainty ("the smooth idea in your
    head"). The line itself is never changed; `score_line` scores it as
    drawn for comparison. Controls are spaced `knot_m` along the line, closer
    where it turns sharply (a weighted arc-length parameterisation), so a
    hairpin gets the controls it needs.
    """

    def __init__(self, legs_xy, grid, settings):
        p = settings["params"]
        self.params = p
        self.grid = grid
        self.h = float(p["spacing_m"])
        self.P, self.joints = resample_legs(legs_xy, self.h)
        n = len(self.P)
        if n < 6:
            raise ValueError("the stretch is too short to refine")
        self.n_in = n
        self.d_in = np.concatenate([[0.0], np.cumsum(np.hypot(*np.diff(self.P, axis=0).T))])
        self.L_in = float(self.d_in[-1])
        self.base_k = {f: float(v["k"]) for f, v in settings["factors"].items()}
        self.f = {f: curve_fn(v["curve"]) for f, v in settings["factors"].items()}
        self.corridor = float(p["corridor_m"])
        self.tmax = float(p["cutfill_max_m"])
        self.bench = float(p["bench_m"])
        self.thr = float(p["min_outslope_pct"]) / 100
        self.dip = float(p["reversal_dip_m"])
        self.fall_tsa = float(p["fall_tsa_deg"])
        self.sigma = float(p["input_sigma_m"])
        self.R_sketch = float(p["sketch_radius_m"])
        self.smooth_w = max(1, int(round(5 / self.h))) | 1
        self.side_w = max(1, int(round(10 / self.h))) | 1
        self.stride = max(1, int(round(5 / self.h)))
        self.grade_ref = None

        # The raw line (all its vertices, joints included) is the reference.
        raw = [np.asarray(xy, float) for xy in legs_xy]
        ref = np.vstack([raw[0]] + [r[1:] for r in raw[1:]])
        keep = np.concatenate([[True], np.hypot(*np.diff(ref, axis=0).T) > 1e-6])
        self.ref = ref[keep]
        self._distance_fields()

        # The fit target: the line with its vertices filleted (more for a
        # loose sketch, less for a precise line), resampled evenly.
        r_fit = max(1.5, self.R_sketch * min(1.0, self.sigma / SIGMA_REF))
        X, _ = resample_legs([fillet(self.ref, r_fit, self.h / 2)], self.h)
        # Weighted arc length: sharp turns get up to 3x the controls.
        u_in, W = self.weighted_u(X)
        K = max(4, int(round(W / float(p["knot_m"]))) + 3)
        self.K = K
        self.nfree = K - 2
        B_in = spline.basis(u_in, K)
        n = len(X)
        # The start: least squares to the line, second differences of the
        # controls penalised by the line's uncertainty.
        lam = FIT_SMOOTH * (n / K) * self.sigma ** 2
        # Candidates are sampled at even steps of the weighted parameter:
        # ~spacing_m apart on straight ground, closer through sharp turns.
        N = max(6, int(round(W / self.h)) + 1)
        self.u = np.linspace(0, 1, N)
        self.B = spline.basis(self.u, K)
        # Each sample depends on 4 controls: keep their indices and weights,
        # so evaluating a candidate is O(samples), not samples x controls.
        j0 = np.clip(np.argmax(self.B > 0, axis=1), 0, K - 4)
        self.B_idx = j0[:, None] + np.arange(4)[None, :]
        self.B_w = np.take_along_axis(self.B, self.B_idx, axis=1)
        self.n = N
        self.Bg = spline.basis(spline.greville(K), K)      # where each control acts on the curve
        self.ctrl_u = spline.greville(K)[1:K - 1]
        # Where the smoothing would fold the line (a switchback whose arms are
        # closer than the smoothing reaches pulls them into a cusp), relax it
        # there and refit, until the start is a clean line.
        row_w = np.ones(K - 2)
        for _ in range(10):
            self.C0 = fit_controls(B_in, X, lam, row_w)
            bad = self._bad_samples(self.B @ self.C0)
            if not len(bad):
                break
            for i in bad:
                j = int(np.clip(np.searchsorted(self.ctrl_u, self.u[i]), 0, K - 3))
                row_w[max(0, j - 3):j + 4] *= 0.25

    def _bad_samples(self, Q):
        """Samples where Q folds back, bunches up, or turns tighter than 1 m."""
        dQ = np.diff(Q, axis=0)
        ds = np.hypot(dQ[:, 0], dQ[:, 1])
        bad = set(np.flatnonzero((dQ[1:] * dQ[:-1]).sum(1) < 0).tolist())
        bad |= set(np.flatnonzero(ds < 0.1 * self.h).tolist())
        th = np.unwrap(np.arctan2(dQ[:, 1], dQ[:, 0]))
        kap = np.abs(np.diff(th)) / np.maximum((ds[1:] + ds[:-1]) / 2, 1e-6)
        bad |= set(np.flatnonzero(kap > 1.0).tolist())
        if not bad and intersects(Q, self.stride):
            bad = set(range(0, len(Q), max(1, len(Q) // 50)))
        return sorted(bad)

    def weighted_u(self, X):
        """Parameter along X by arc length weighted up to 3x where X turns
        sharply (smoothed over twice the sketch radius). Returns (u, total)."""
        seg = np.hypot(*np.diff(X, axis=0).T)
        n = len(X)
        turn = np.zeros(n)
        hd = np.unwrap(np.arctan2(*np.diff(X, axis=0).T[::-1]))
        turn[1:-1] = np.abs(np.diff(hd))
        win = max(1, int(round(2 * self.R_sketch / self.h))) | 1
        kap = running_mean(turn / np.maximum(np.concatenate([[seg[0]], (seg[:-1] + seg[1:]) / 2, [seg[-1]]]), 1e-6), win)
        dw = seg * (1 + np.minimum(2.0, self.R_sketch * kap[1:]))
        W = np.concatenate([[0.0], np.cumsum(dw)])
        return W / W[-1], W[-1]

    def respace(self, c):
        """The same shape with its controls re-spaced (a near-exact refit of
        the current curve), or None if the refit fails."""
        Q = self.curve(c)
        u, _ = self.weighted_u(Q)
        try:
            c2 = fit_controls(spline.basis(u, self.K), Q, 1e-6)[1:-1]
        except np.linalg.LinAlgError:
            return None
        # Only if the shape survives: the refit must stay within RESPACE_MAX_M.
        Q2 = self.curve(c2)
        dev, _ = polyline_distance(Q2[:, 0], Q2[:, 1], Q)
        return c2 if dev.max() <= RESPACE_MAX_M else None

    def _distance_fields(self):
        g = self.grid
        ok = np.isfinite(g.z)
        rows, cols = np.nonzero(ok)
        dist, pos = polyline_distance(g.x0 + cols * g.res, g.y0 + rows * g.res, self.ref)
        s_v, A, T = corner_allowance(self.ref, self.R_sketch, self.corridor)
        eff = np.maximum(0.0, dist - allowance_at(pos, s_v, A, T) - self.sigma)
        D = np.full(g.z.shape, np.nan)
        E = np.full(g.z.shape, np.nan)
        D[rows, cols] = dist
        E[rows, cols] = eff
        self.dist_grid = Grid(D, g.x0, g.y0, g.res)
        self.eff_grid = Grid(E, g.x0, g.y0, g.res)

    def full(self, c):
        """Free controls (K-2, 2) -> all controls, ends pinned."""
        return np.vstack([self.P[0], c, self.P[-1]])

    def curve(self, c):
        C = self.full(c)
        return np.einsum("nk,nkd->nd", self.B_w, C[self.B_idx])

    def start(self):
        return self.C0[1:-1].copy()

    def control_frames(self, c):
        """The curve point each free control mainly moves, and the curve's
        left normal there."""
        C = self.full(c)
        pts = self.Bg @ C
        t = np.gradient(pts, axis=0)
        t = t / np.maximum(np.hypot(t[:, 0], t[:, 1]), 1e-9)[:, None]
        return pts[1:-1], np.column_stack([-t[1:-1, 1], t[1:-1, 0]])

    def folds(self, c, c_prev=None):
        """Does the candidate cross itself? With c_prev (a candidate known
        not to), only the stretch that moved is checked."""
        if c_prev is None:
            return intersects(self.curve(c), self.stride)
        return intersects_changed(self.curve(c), self.curve(c_prev), self.stride)

    def legs_of(self, Q):
        """Split a candidate at the points nearest the input's leg joints."""
        out, a = [], 0
        for j in self.joints[1:-1]:
            jp = self.P[j]
            b = a + 1 + int(np.argmin(np.hypot(*(Q[a + 1:-1] - jp).T)))
            out.append(Q[a:b + 1])
            a = b
        out.append(Q[a:])
        return out

    def score(self, c, t, k=None, detail=False):
        return self.score_line(self.curve(c), t, k, detail, check_folds=True)

    def score_line(self, Q, t, k=None, detail=False, check_folds=False):
        """Score any line (the candidate's samples, or the input as drawn).
        None if impossible (outside the corridor, folded, off the terrain
        data); else a dict with `total`, `costs` (per factor, before k),
        `density` (cost per sample, for aiming moves) and `corner_idx`; with
        detail=True also every array the UI shows."""
        k = self.base_k if k is None else k
        h = self.h
        n = len(Q)
        eff = self.eff_grid.sample(Q[:, 0], Q[:, 1])
        if not np.isfinite(eff).all() or eff.max() > self.corridor + 1e-6:
            return None
        dQ = np.diff(Q, axis=0)
        ds = np.hypot(dQ[:, 0], dQ[:, 1])
        if (ds < 1e-3).any():
            return None
        if check_folds and (((dQ[1:] * dQ[:-1]).sum(1) < 0).any() or (ds < 0.1 * h).any()):
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
        # Trail-slope alignment (TSA): the angle in plan between the trail and
        # the fall line, from the bench cross-sections averaged over ~5 m;
        # 0 = straight down the fall line, 90 = on the contour. Undefined
        # (nan) where the ground is flatter than FALL_MIN_SLOPE_PCT.
        cs5, al5 = running_mean(cross, self.smooth_w), running_mean(along, self.smooth_w)
        tsa = np.degrees(np.arctan2(np.abs(cs5), np.abs(al5)))
        tsa[100 * np.hypot(cs5, al5) < FALL_MIN_SLOPE_PCT] = np.nan
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
            # Climbing turn or switchback (owner, 2026-10-17). A corner (the
            # downhill side flips) always crosses the fall line somewhere; what
            # matters is the ground there. Gentle (a bench, a nose top, a mild
            # slope): the trail can climb straight through the crossing, a
            # climbing turn. Steep: crossing the fall line is a steep pitch
            # unless a landing is excavated, a switchback.
            tz = tsa[a:b + 1]
            cross_at = np.isfinite(tz) & (tz < self.fall_tsa)
            sz = side[a:b + 1]
            crossing = float(sz[cross_at].mean()) if cross_at.any() else float(sz.min())
            tsa_min = float(np.nanmin(tz)) if np.isfinite(tz).any() else None
            kind = ("climbing turn" if crossing <= CLIMB_TURN_MAX_PCT else
                    "switchback-like" if crossing >= SWITCHBACK_MIN_PCT else "between")
            corners.append((mid, a, b, r, float(side[a:b + 1].mean()), math.degrees(dth), min(1000.0, r_arc),
                            crossing, tsa_min, kind))
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

        off = eff
        f = self.f
        per = {"grade": grade, "slope": side, "cutfill": cut, "offset": off, "drainage": run}
        vals = {name: f[name](x) for name, x in per.items()}
        # Fall-line tread (owner, 2026-10-17): where the trail runs within
        # fall_tsa_deg of the fall line, water cannot leave the tread
        # sideways; at 5-8 % it runs off along the trail, flatter it pools,
        # steeper it erodes. Charged per metre at the design grade, so a
        # broad corner's long fall-line arc pays for its length, and corners
        # on convex ground (where the fall line turns with the trail) win
        # without a rule of their own.
        fall = np.nan_to_num(tsa, nan=90.0) < self.fall_tsa
        vals["falltread"] = f["falltread"](grade) * fall
        # Bends are charged per right angle TURNED, weighted by the curve at
        # the radius it was turned at, not per metre: a kink is an event, and
        # per metre a 3 m jog on a 750 m line cost nothing (2026-10-17).
        # Corners are left to the corner factor.
        bend_v = f["bend"](bend_r) * ~zone
        vals["bend"] = bend_v
        # One scale for every factor (2026-10-17): per-metre factors are
        # charged PER 100 M of line, not averaged, so a long route's grade
        # weighs as much against its corners as a short one's. Averaging let
        # six corners outweigh any grade on a 2.5 km climb, and the search
        # removed switchbacks to the grade's cost.
        costs = {name: float((v * w).sum() / PER_M) for name, v in vals.items() if name != "bend"}
        costs["bend"] = float((bend_v * akap * w).sum() / (math.pi / 2))
        # "Consistent grade is minimising the maximum grade" (owner): a short
        # steep pitch is cheap per metre, so the peak is charged as well.
        gmax = float(grade.max())
        costs["grade"] += GRADE_PEAK_SHARE * float(f["grade"](gmax))
        if self.grade_ref is not None and gmax > self.grade_ref:
            costs["grade"] += (gmax - self.grade_ref) / GRADE_OVER_PER
        cr = [f["corner"](c_[3]) for c_ in corners]
        costs["corner"] = float(sum(cr))
        # Every metre alike (owner, 2026-10-17). Counted from the input's
        # length, so the line as drawn pays 0 and a shorter candidate earns
        # a credit: totals stay comparable and the per-factor guard is not
        # loosened by a large constant.
        costs["length"] = (L - self.L_in) / PER_M
        total = sum(k.get(name, 0) * v for name, v in costs.items())

        density = sum(k.get(name, 0) * v for name, v in vals.items() if name != "bend") * w / PER_M
        density = density + k.get("bend", 0) * bend_v * akap * w / (math.pi / 2)
        for c_, x1 in zip(corners, cr):
            a, b = c_[1], c_[2]
            density[a:b + 1] += k.get("corner", 0) * x1 / (b - a + 1)
        out = {"total": float(total), "costs": costs, "density": density,
               "corner_idx": [c_[0] for c_ in corners], "length_m": L}
        if detail:
            out.update({
                "Q": Q, "d": d, "fall": fall, "tsa": tsa, "ground": g, "design": zd, "cut": cut, "grade": grade, "side": side,
                "offset": eff, "dist": self.dist_grid.sample(Q[:, 0], Q[:, 1]), "bend_r": bend_r, "zone": zone, "run": run, "sites": sites,
                "corners": [{"i": c_[0], "d": float(d[c_[0]]), "radius_m": float(c_[3]),
                             "arc_radius_m": float(c_[6]), "side_pct": c_[4], "turn_deg": c_[5],
                             "crossing_pct": c_[7], "tsa_min": c_[8], "kind": c_[9]}
                            for c_ in corners],
                "w": w, "t": float(t),
                "contrib": {name: k.get(name, 0) * v for name, v in costs.items()},
            })
        return out


def histograms(r, bins=30):
    """The input's values per factor, binned over each factor's x range, for
    the curve editor's background: metres per bin for per-metre factors,
    counts for per-corner ones."""
    out = {}
    w = r["w"]
    src = {"grade": (r["grade"], w), "slope": (r["side"], w), "cutfill": (r["cut"], w),
           "offset": (r["offset"], w), "drainage": (r["run"], w),
           "bend": (r["bend_r"], w * ~r["zone"]),
           "corner": (np.array([c["radius_m"] for c in r["corners"]]), None),
           "falltread": (r["grade"], w * r["fall"])}
    for name, (x, wt) in src.items():
        m = FACTOR_META[name]
        lo, hi = m["xmin"], m["xmax"]
        xc = np.clip(x, lo, hi - 1e-9 * (hi - lo))
        hgt, _ = np.histogram(xc, bins=bins, range=(lo, hi), weights=wt)
        out[name] = {"lo": lo, "hi": hi, "counts": [round(float(v), 2) for v in hgt],
                     "unit": "m" if wt is not None else "corners"}
    return out
