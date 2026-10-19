"""One candidate, scored: geometry from the offset field, ground and bench
cross-sections from the Grid, the design grade line, corners and bends, the
drainage stub, and every factor's cost (docs/REFINE_TOOL.md §2-3).

Costs are in metres of EQUIVALENT EFFORT (owner, 2026-10-18): the length
along the slope, plus k extra metres per metre at curve 1 for each
per-metre factor (the curve integrated along the slope), k metres per
corner at curve 1, and k metres per right angle turned at curve 1 for bends.
"""
import math

import numpy as np

from . import spline
from .profile import taut_string
from .terrain import Grid
from .settings import FACTOR_META, ZERO_AS

CORNER_KAPPA = 1 / 500.0    # 1/m: curvature that still counts as the corner's turn (was 1/50: broader corners went uncounted)
CORNER_REACH_M = 100.0      # a corner's turn reaches at most this far beyond its side change
CORNER_RUN_MIN_M = 10.0     # a downhill side held for less than this is noise, not a side
CORNER_RAMP_M = 20.0        # a dug-in corner ramps back to the line's profile over this, either side of its turn
DIG_DEPTHS = (0.25, 0.5, 0.75, 1.0, 1.5, 2.0, 3.0, 4.0)   # m: cuts a corner tries, up to corner_cut_max_m
GRADE_OVER_PER = 10.0       # %: each this much past the input's steepest 10 m adds 1 to the grade cost
FALL_MIN_SLOPE_PCT = 4.0    # %: flatter ground has no fall line to speak of
CLIMB_TURN_MAX_PCT = 15.0   # %: a corner crossing the fall line on ground this gentle or gentler is a climbing turn
SWITCHBACK_MIN_PCT = 25.0   # %: ... this steep or steeper, switchback-like (a landing must be cut)
PER_M = 100.0               # per-metre factors are charged per this many metres
SITE_MERGE_M = 15.0         # reversal sites closer than this are one site
RESPACE_MAX_M = 0.25        # m: a re-spacing that would move the line more than this is skipped
SIGMA_REF = 3.0             # m: at this uncertainty or more, sketched vertices are filleted at the full sketch radius
FIT_SMOOTH = 0.5            # the start's curvature penalty per (sample per control) per m^2 of uncertainty
START_SHRINK = (1.0, 0.5, 0.25, 0.1, 0.0)   # the start's rounding and smoothing, tried in turn until it fits the limit
BEND_STEP_M = 0.5           # m: bends are measured on the line resampled this finely, so 1 m and 3 m turns read apart
BEND_WINDOW_M = 1.0         # m: ... as the turn over this much of it (a kink reads ~0.6 m)
ADAPT_TURN_DEG = 10.0       # degrees: with adaptive spacing (the route-finder's chain), at most this turn per sample ...
ADAPT_MIN_M = 0.3           # m: ... but samples no closer than this (a 2 m hairpin gets ~0.35 m)
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


def densify(xy, step):
    """xy with every segment split into pieces no longer than step, keeping
    every vertex (a sketched kink stays a kink)."""
    xy = np.asarray(xy, float)
    out = [xy[:1]]
    for p, q in zip(xy[:-1], xy[1:]):
        m = max(1, int(math.ceil(math.hypot(*(q - p)) / step)))
        f = np.arange(1, m + 1)[:, None] / m
        out.append(p + f * (q - p))
    return np.vstack(out)


def adaptive_resample(xy, h, turn_deg=ADAPT_TURN_DEG, ds_min=ADAPT_MIN_M):
    """xy resampled with spacing that follows its curvature (owner,
    2026-10-18: resolve tight corners): each sample turns at most turn_deg,
    with spacing between ds_min and h; both ends kept."""
    D = densify(xy, min(ds_min, h) / 2)
    _, r = turning(D)
    spacing = np.clip(r * math.radians(turn_deg), ds_min, h)
    seg = np.hypot(*np.diff(D, axis=0).T)
    n = np.concatenate([[0.0], np.cumsum(seg / ((spacing[:-1] + spacing[1:]) / 2))])   # samples so far
    m = max(1, int(math.ceil(n[-1])))
    s = np.concatenate([[0.0], np.cumsum(seg)])
    t = np.interp(np.linspace(0, n[-1], m + 1), n, s)
    return np.column_stack([np.interp(t, s, D[:, 0]), np.interp(t, s, D[:, 1])])


def turning(Q, window=BEND_WINDOW_M):
    """Turn (radians, unsigned) at each vertex of a polyline, and the radius
    there: `window` metres over the turn within window / 2 either side, with
    each vertex's turn spread over half of each of its segments. On a line
    densified to under `window` (densify), a smooth curve reads its radius
    and a vertex of the line as drawn reads as the kink it is, about
    window / turn: a sketched arc whose vertices are further apart than the
    window reads tighter than the arc it stands for."""
    dQ = np.diff(Q, axis=0)
    ds = np.hypot(dQ[:, 0], dQ[:, 1])
    hd = np.arctan2(dQ[:, 1], dQ[:, 0])
    turn = np.zeros(len(Q))
    turn[1:-1] = np.abs((np.diff(hd) + math.pi) % (2 * math.pi) - math.pi)
    s = np.concatenate([[0.0], np.cumsum(ds)])
    mids = np.concatenate([[0.0], (s[:-1] + s[1:]) / 2, [s[-1]]])
    T = np.concatenate([[0.0], np.cumsum(turn)])            # turn done by each mid point
    got = np.interp(s + window / 2, mids, T) - np.interp(s - window / 2, mids, T)
    span = np.minimum(s + window / 2, s[-1]) - np.maximum(s - window / 2, 0)
    r = np.where(got > 1e-9, span / np.maximum(got, 1e-9), 1e4)
    return turn, np.minimum(r, 1e4)


def curve_fn(curve):
    """A cost curve as a function: log-linear between anchors (geometric:
    halfway from 0.1 to 10 is 1), a straight line on the panel's log axis,
    and flat beyond the first and last anchor (owner, 2026-10-17). Costs
    are never 0; a 0 in an older curve reads as ZERO_AS (settings).
    """
    xs = np.array([c[0] for c in curve], float)
    ys = np.array([c[1] for c in curve], float)
    ly = np.log(np.where(ys > 0, ys, ZERO_AS))
    if len(xs) == 1:
        return lambda x: float(np.exp(ly[0])) if np.ndim(x) == 0 else np.full(np.shape(x), np.exp(ly[0]))
    dx = np.diff(xs)

    def f(x):
        x = np.asarray(x, float)
        i = np.clip(np.searchsorted(xs, x, side="right") - 1, 0, len(xs) - 2)
        t = np.clip(np.divide(x - xs[i], dx[i], out=np.ones_like(x), where=dx[i] > 0), 0.0, 1.0)
        out = np.exp(ly[i] + (ly[i + 1] - ly[i]) * t)
        return float(out) if out.ndim == 0 else out
    return f


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


class Problem:
    """The fixed parts of a refinement (docs/REFINE_TOOL.md, "the sketch as
    evidence", 2026-10-17).

    The person's line is EVIDENCE of an intended path, drawn with sharp
    corners they expect to be rounded, not the frame the answer is built on.
    So a candidate is its own smooth curve, a clamped cubic B-spline through
    control points in the plane with its ends pinned to the line's ends, and
    the line enters only as a hard limit: the search stays within
    `corridor_m` of it, plain distance (owner, 2026-10-18: being further from
    the sketch is not worse on the ground, so it is not a cost; and no
    allowance at sketched corners, so a tight corner in a narrow search
    stays tight). The distance is precomputed on the terrain grid's cells.

    The start is a smooth fit to the line: least squares with a curvature
    penalty that grows with the uncertainty ("the smooth idea in your
    head"). The line itself is never changed; `score_line` scores it as
    drawn for comparison. Controls are spaced `knot_m` along the line, closer
    where it turns sharply (a weighted arc-length parameterisation), so a
    hairpin gets the controls it needs.
    """

    def __init__(self, legs_xy, grid, settings, twi_grid=None, existing_xy=None, like=None, adaptive=False):
        """`like`: another Problem on the same stretch whose search limit and
        existing tread this one keeps (the route-finder's chain starts from
        the route, but is limited by distance from the person's line).
        `adaptive`: samples and controls follow the line's curvature
        (adaptive_resample), so tight corners are resolved: the route-finder's
        chain."""
        self.adaptive = adaptive
        # Samples closer than this read as the curve bunching up (a fold).
        self.min_ds = 0.3 * ADAPT_MIN_M if adaptive else 0.1 * float(settings["params"]["spacing_m"])
        p = settings["params"]
        self.params = p
        self.grid = grid
        self.twi_grid = twi_grid         # a Grid of TWI (refine.twi), or None: wet ground costs nothing
        self.existing_xy = [np.asarray(L, float) for L in (existing_xy or []) if len(L) >= 2]   # existing tread, or none: no charge
        self.h = float(p["spacing_m"])
        self.P, self.joints = resample_legs(legs_xy, self.h)
        n = len(self.P)
        if n < 6:
            raise ValueError("the stretch is too short to refine")
        self.n_in = n
        self.d_in = np.concatenate([[0.0], np.cumsum(np.hypot(*np.diff(self.P, axis=0).T))])
        self.L_in = float(self.d_in[-1])
        self.base_k = {f: float(v["k"]) for f, v in settings["factors"].items()}
        self.base_k["length"] = 1.0                  # the unit: every metre along the slope is a metre
        self.f = {f: curve_fn(v["curve"]) for f, v in settings["factors"].items()}
        # Fill matched to a cut: the most fill that costs no more than cut of
        # the same depth's curve value, read off the cut and fill curve.
        # Never past the curve's last node: beyond it the curve is flat, which
        # would make any fill as cheap as the last node.
        x_end = max(0.0, float(settings["factors"]["cutfill"]["curve"][-1][0]))
        xs = np.linspace(0, x_end, 401)
        fv = self.f["cutfill"](xs)
        self._fill_x, self._fill_cmax = xs, np.maximum.accumulate(fv)
        self.corridor = float(p["corridor_m"])
        self.limit = self.corridor          # the search's limit; a chain may narrow it
        self.tmax = float(p["cutfill_max_m"])
        self.bench = float(p["bench_m"])
        self.thr = float(p["min_outslope_pct"]) / 100
        self.dip = float(p["reversal_dip_m"])
        self.fall_tsa = float(p["fall_tsa_deg"])
        self.min_turn = math.radians(float(p["corner_min_turn_deg"]))
        self.corner_cut_max = float(p["corner_cut_max_m"])
        self.grade_peak_m = float(p["grade_peak_m"])
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
        self.Pf = densify(self.ref, BEND_STEP_M)     # the input as drawn, finely, for its bends
        if like is not None:
            self.dist_grid, self.existing_grid = like.dist_grid, like.existing_grid
        else:
            self._distance_fields()

        # The start must lie within the search's limit. With a narrow limit
        # the rounding of sketched corners and the smoothing are reduced
        # until it does (a tight corner in a narrow search stays tight).
        for shrink in START_SHRINK:
            # The fit target: the line with its vertices filleted (more for a
            # loose sketch, less for a precise line), resampled evenly.
            r_fit = max(1.5, shrink * self.R_sketch * min(1.0, self.sigma / SIGMA_REF))
            if self.adaptive:
                X = adaptive_resample(fillet(self.ref, r_fit, ADAPT_MIN_M / 2), self.h)
            else:
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
            lam = FIT_SMOOTH * (n / K) * (shrink * self.sigma) ** 2
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
            # The same curve BEND_STEP_M apart, for measuring bends.
            m = max(1, int(round(self.h / BEND_STEP_M)))
            Bf = spline.basis(np.linspace(0, 1, (N - 1) * m + 1), K)
            jf = np.clip(np.argmax(Bf > 0, axis=1), 0, K - 4)
            self.Bf_idx = jf[:, None] + np.arange(4)[None, :]
            self.Bf_w = np.take_along_axis(Bf, self.Bf_idx, axis=1)
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
            if np.nanmax(self.dist_grid.sample(*(self.B @ self.C0).T)) <= self.limit:
                break

    def _bad_samples(self, Q):
        """Samples where Q folds back, bunches up, or turns tighter than 1 m."""
        dQ = np.diff(Q, axis=0)
        ds = np.hypot(dQ[:, 0], dQ[:, 1])
        bad = set(np.flatnonzero((dQ[1:] * dQ[:-1]).sum(1) < 0).tolist())
        bad |= set(np.flatnonzero(ds < self.min_ds).tolist())
        th = np.unwrap(np.arctan2(dQ[:, 1], dQ[:, 0]))
        kap = np.abs(np.diff(th)) / np.maximum((ds[1:] + ds[:-1]) / 2, 1e-6)
        bad |= set(np.flatnonzero(kap > 1.0).tolist())
        if not bad and intersects(Q, self.stride):
            bad = set(range(0, len(Q), max(1, len(Q) // 50)))
        return sorted(bad)

    def weighted_u(self, X):
        """Parameter along X by arc length weighted up to 3x where X turns
        sharply (smoothed over twice the sketch radius). Adaptive: weighted by
        the local curvature instead, so that each sample turns at most
        ADAPT_TURN_DEG (down to ADAPT_MIN_M apart); controls follow, as they
        are spread evenly in the same parameter. Returns (u, total)."""
        seg = np.hypot(*np.diff(X, axis=0).T)
        if self.adaptive:
            _, r = turning(X)
            spacing = np.clip(r * math.radians(ADAPT_TURN_DEG), ADAPT_MIN_M, self.h)
            dw = seg * self.h / ((spacing[:-1] + spacing[1:]) / 2)
            W = np.concatenate([[0.0], np.cumsum(dw)])
            return W / W[-1], W[-1]
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
        dist, _ = polyline_distance(g.x0 + cols * g.res, g.y0 + rows * g.res, self.ref)
        D = np.full(g.z.shape, np.nan)
        D[rows, cols] = dist
        self.dist_grid = Grid(D, g.x0, g.y0, g.res)
        self.existing_grid = None
        if self.existing_xy:
            E = np.full(len(rows), np.inf)
            for L in self.existing_xy:
                e, _ = polyline_distance(g.x0 + cols * g.res, g.y0 + rows * g.res, L)
                E = np.minimum(E, e)
            G = np.full(g.z.shape, np.nan)
            G[rows, cols] = E
            self.existing_grid = Grid(G, g.x0, g.y0, g.res)

    def full(self, c):
        """Free controls (K-2, 2) -> all controls, ends pinned."""
        return np.vstack([self.P[0], c, self.P[-1]])

    def curve(self, c):
        C = self.full(c)
        return np.einsum("nk,nkd->nd", self.B_w, C[self.B_idx])

    def curve_fine(self, c):
        C = self.full(c)
        return np.einsum("nk,nkd->nd", self.Bf_w, C[self.Bf_idx])

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

    def fill_for(self, cut):
        """The deepest fill whose cost on the cut and fill curve is no more
        than that of cutting `cut` m (both measured from 0)."""
        c = float(self.f["cutfill"](-cut))
        i = int(np.searchsorted(self._fill_cmax, c, side="right"))
        return float(self._fill_x[max(0, i - 1)])

    def _grade(self, d, zd):
        """Design grade in %, over 10 m (5 m either side, shorter at the ends):
        the climb and the descent within those 10 m over their length. On a
        steady slope that is the rise over the run; through a dip it counts
        both sides (owner, 2026-10-19: the rise from end to end of the 10 m
        read a 3 m deep gully, down one wall and up the other, as level)."""
        L = d[-1]
        dp, dm = np.minimum(d + 5, L), np.maximum(d - 5, 0)
        up_down = np.concatenate([[0.0], np.cumsum(np.abs(np.diff(zd)))])
        return 100 * (np.interp(dp, d, up_down) - np.interp(dm, d, up_down)) / np.maximum(dp - dm, 1e-6)

    def _corners(self, d, theta, kap, bend_r, cross, tsa, side):
        """Corners by signed TSA (owner, 2026-10-18). The downhill side (+1
        right, -1 left, from the cross slope over ~10 m) is UNDEFINED in the
        fall-line band (TSA < fall_tsa_deg) and on ground with less cross
        slope than min_outslope_pct; a side held for less than
        CORNER_RUN_MIN_M is noise. A corner is a change of side across an
        undefined stretch (or none), where the line turns by at least
        corner_min_turn_deg (less is crossing a crest or a gully). Its turn
        reaches out from the change for as long as the line keeps curving
        the same way (radius under 1 / CORNER_KAPPA), up to CORNER_REACH_M,
        so a broad corner is counted whole; its radius is the tighter of the
        turn's mean and its tightest 5 m, so a kink inside a broad corner
        costs as the kink it is. Returns dicts, in order along the line."""
        n = len(d)
        cross_s = running_mean(cross, self.side_w)
        sg = np.where(cross_s > self.thr, 1, np.where(cross_s < -self.thr, -1, 0))
        sg[np.isfinite(tsa) & (tsa < self.fall_tsa)] = 0
        sg[~np.isfinite(tsa)] = 0
        runs = []                                     # (first, last, side), long enough to count
        i = 0
        while i < n:
            j = i
            while j + 1 < n and sg[j + 1] == sg[i]:
                j += 1
            if sg[i] != 0 and d[j] - d[i] >= CORNER_RUN_MIN_M:
                runs.append((i, j, int(sg[i])))
            i = j + 1
        out = []
        prev_b = -1
        for (_, a0, s0), (b0, _, s1) in zip(runs[:-1], runs[1:]):
            if s0 == s1:
                continue
            dth0 = theta[b0] - theta[a0]
            dirn = np.sign(dth0) if abs(dth0) > 1e-3 else np.sign(kap[(a0 + b0) // 2])
            a, b = a0, b0
            while a - 1 > prev_b and kap[a - 1] * dirn > CORNER_KAPPA and d[a0] - d[a - 1] <= CORNER_REACH_M:
                a -= 1
            while b + 1 < n and kap[b + 1] * dirn > CORNER_KAPPA and d[b + 1] - d[b0] <= CORNER_REACH_M:
                b += 1
            dth = abs(theta[b] - theta[a])
            if dth < self.min_turn:
                continue
            r_arc = (d[b] - d[a]) / max(dth, 1e-3)
            r_tight = float(bend_r[a:b + 1].min())
            # Climbing turn or switchback (owner, 2026-10-17): what matters is
            # the ground where the turn crosses the fall line. Gentle: the
            # trail climbs straight through; steep: a landing is dug. A label
            # only: the digging is costed by _dig.
            tz = tsa[a:b + 1]
            cross_at = np.isfinite(tz) & (tz < self.fall_tsa)
            sz = side[a:b + 1]
            crossing = float(sz[cross_at].mean()) if cross_at.any() else float(sz.min())
            out.append({
                "a": a, "b": b, "mid": (a0 + b0) // 2, "r": min(1000.0, r_arc, r_tight), "r_arc": min(1000.0, r_arc),
                "side_pct": float(sz.mean()), "turn_deg": math.degrees(dth), "crossing": crossing,
                "tsa_min": float(np.nanmin(tz)) if np.isfinite(tz).any() else None,
                "kind": ("climbing turn" if crossing <= CLIMB_TURN_MAX_PCT else
                         "switchback-like" if crossing >= SWITCHBACK_MIN_PCT else "between"),
            })
            prev_b = b
        return out

    def _dig(self, d, g, zd, c_, k):
        """Try cutting corner c_ deeper into the slope: over its turn and
        CORNER_RAMP_M either side, the design line may go down to each of
        DIG_DEPTHS (up to corner_cut_max_m) below the ground and up to the
        matching fill, pinned to the line's profile at the window's ends.
        Keeps the cheapest, in grade plus cut and fill per metre and the
        window's steepest 10 m (as the grade peak charge), in zd (in place). Returns the deepest cut of the corner as kept, m."""
        n = len(d)
        a = int(np.searchsorted(d, d[c_["a"]] - CORNER_RAMP_M))
        b = min(n - 1, int(np.searchsorted(d, d[c_["b"]] + CORNER_RAMP_M)))
        if b - a < 3:
            return float(max(0.0, (g[a:b + 1] - zd[a:b + 1]).max()))
        dw, gw = d[a:b + 1], g[a:b + 1]
        wv = np.gradient(dw)
        fg, fc = self.f["grade"], self.f["cutfill"]
        kg, kc = k.get("grade", 0), k.get("cutfill", 0)

        def cost(z):
            gr = self._grade(dw, z)
            return float(((kg * fg(gr) + kc * fc(z - gw)) * wv).sum() + kg * self.grade_peak_m * float(fg(gr.max())))

        best_z, best = zd[a:b + 1], cost(zd[a:b + 1])
        cur_cut = float(max(0.0, (gw - best_z).max()))
        for D in DIG_DEPTHS:
            if D <= cur_cut + 1e-6 or D > self.corner_cut_max + 1e-6:
                continue
            lo, hi = gw - D, gw + self.fill_for(D)
            lo[0] = hi[0] = zd[a]
            lo[-1] = hi[-1] = zd[b]
            z = taut_string(dw, lo, hi)
            cz = cost(z)
            if cz < best:
                best_z, best = z, cz
        zd[a:b + 1] = best_z
        return round(float(max(0.0, (g[c_["a"]:c_["b"] + 1] - zd[c_["a"]:c_["b"] + 1]).max())), 2)

    def score(self, c, t, k=None, detail=False):
        return self.score_line(self.curve(c), t, k, detail, check_folds=True, Qf=self.curve_fine(c))

    def score_line(self, Q, t, k=None, detail=False, check_folds=False, Qf=None):
        """Score any line (the candidate's samples, or the input as drawn).
        None if impossible (outside the corridor, folded, off the terrain
        data); else a dict with `total`, `costs` (per factor, before k),
        `density` (cost per sample, for aiming moves) and `corner_idx`; with
        detail=True also every array the UI shows."""
        k = self.base_k if k is None else k
        h = self.h
        n = len(Q)
        dist = self.dist_grid.sample(Q[:, 0], Q[:, 1])
        if not np.isfinite(dist).all() or dist.max() > self.limit + 1e-6:
            return None
        dQ = np.diff(Q, axis=0)
        ds = np.hypot(dQ[:, 0], dQ[:, 1])
        if (ds < 1e-3).any():
            return None
        if check_folds and (((dQ[1:] * dQ[:-1]).sum(1) < 0).any() or (ds < self.min_ds).any()):
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

        # Trail-slope alignment (TSA): the angle in plan between the trail and
        # the fall line, from the bench cross-sections averaged over ~5 m;
        # 0 = straight down the fall line, 90 = on the contour. Undefined
        # (nan) where the ground is flatter than FALL_MIN_SLOPE_PCT.
        cs5, al5 = running_mean(cross, self.smooth_w), running_mean(along, self.smooth_w)
        tsa = np.degrees(np.arctan2(np.abs(cs5), np.abs(al5)))
        tsa[100 * np.hypot(cs5, al5) < FALL_MIN_SLOPE_PCT] = np.nan
        tsa_v = np.nan_to_num(tsa, nan=90.0)          # flat ground: no fall line to run down

        # Bends measured finely (owner, 2026-10-18: 1 m and 3 m turns differ
        # on a hiking trail): on the line BEND_STEP_M apart (the input with
        # its sketched vertices kept, a candidate's own curve), the turn and
        # radius at each fine vertex over BEND_WINDOW_M. Each fine vertex belongs to
        # the nearest sample by distance along the line; a sample's bend
        # radius is the tightest of its fine vertices.
        if Qf is None:
            Qf = self.Pf if Q is self.P else Q
        turn_f, r_f = turning(Qf)
        df = np.concatenate([[0.0], np.cumsum(np.hypot(*np.diff(Qf, axis=0).T))])
        own = np.clip(np.searchsorted(d, df * (L / max(df[-1], 1e-9)) - h / 2), 0, n - 1)
        bend_r = np.full(n, 1e4)
        np.minimum.at(bend_r, own, r_f)
        corners = self._corners(d, theta, kap, bend_r, cross, tsa, side)
        zone = np.zeros(n, bool)
        for c_ in corners:
            zone[c_["a"]:c_["b"] + 1] = True

        # The design grade line through the cut/fill tube: cut down to t,
        # fill up to what costs the same on the cut and fill curve (owner,
        # 2026-10-18: fill is the weaker material). Ends on the ground.
        lo, hi = g - t, g + self.fill_for(t)
        lo[0] = hi[0] = g[0]
        lo[-1] = hi[-1] = g[-1]
        zd = taut_string(d, lo, hi)
        # Corners dug in (option 2, owner 2026-10-18): where the line turns
        # across the fall line on steep ground it either climbs steeply
        # through the turn or is cut into the slope to hold its grade. Each
        # corner tries deeper cuts over its turn and a ramp either side and
        # keeps the cheapest in grade plus cut and fill.
        for c_ in corners:
            c_["dig_m"] = self._dig(d, g, zd, c_, k)
        e = zd - g                                    # signed: cut < 0 < fill
        cut = np.abs(e)
        grade = self._grade(d, zd)

        sites = reversal_sites(d, zd, g, self.dip)
        site_d = d[sites] if sites else np.zeros(0)
        if len(site_d):
            last = np.searchsorted(site_d, d, side="right") - 1
            run = d - np.where(last >= 0, site_d[np.maximum(last, 0)], 0.0)
        else:
            run = d.copy()

        # Length along the slope (owner, 2026-10-18): the design line's own
        # length, climb included. Per-metre factors are charged per metre of it.
        d3 = np.concatenate([[0.0], np.cumsum(np.hypot(ds, np.diff(zd)))])
        L3 = float(d3[-1])
        w3 = np.gradient(d3)

        f = self.f
        wet = (np.nan_to_num(self.twi_grid.sample(Q[:, 0], Q[:, 1]), nan=0.0)
               if self.twi_grid is not None else np.zeros(n))
        off = (np.nan_to_num(self.existing_grid.sample(Q[:, 0], Q[:, 1]), nan=0.0)
               if self.existing_grid is not None else np.zeros(n))
        per = {"grade": grade, "slope": side, "cutfill": e, "drainage": run, "wet": wet, "tsa": tsa_v, "existing": off}
        vals = {name: f[name](x) for name, x in per.items()}
        if self.twi_grid is None:
            vals["wet"] = np.zeros(n)        # no wetness layer: the factor is off, not at its floor
        if self.existing_grid is None:
            vals["existing"] = np.zeros(n)   # no existing trail selected: nothing to stay on
        # Fall-line tread (owner, 2026-10-17): where the trail runs in the
        # fall-line band, water cannot leave the tread sideways; at 5-8 % it
        # runs off along the trail, flatter it pools, steeper it erodes.
        # Charged per metre at the design grade, so a broad corner's long
        # fall-line arc pays for its length, and corners on convex ground
        # (where the fall line turns with the trail) win without a rule of
        # their own.
        fall = tsa_v < self.fall_tsa
        vals["falltread"] = f["falltread"](grade) * fall
        # Bends are charged per right angle TURNED, weighted by the curve at
        # the radius it was turned at, not per metre: a kink is an event, and
        # per metre a 3 m jog on a 750 m line cost nothing (2026-10-17).
        # Corners are left to the corner factor.
        free = ~zone[own]
        bend_f = f["bend"](r_f) * turn_f * free / (math.pi / 2)      # right angles, weighted by the curve
        bend_v = np.bincount(own, bend_f, minlength=n)
        # Equivalent effort, in metres (owner, 2026-10-18): the length along
        # the slope, plus for each per-metre factor k extra metres per metre
        # at curve 1, per corner k metres at curve 1, per right angle k.
        costs = {name: float((v * w3).sum()) for name, v in vals.items()}
        costs["bend"] = float(bend_v.sum())
        # "Consistent grade is minimising the maximum grade" (owner): a short
        # steep pitch is cheap per metre, so the peak is charged again as
        # grade_peak_m metres of its grade.
        gmax = float(grade.max())
        costs["grade"] += self.grade_peak_m * float(f["grade"](gmax))
        if self.grade_ref is not None and gmax > self.grade_ref:
            costs["grade"] += (gmax - self.grade_ref) / GRADE_OVER_PER * PER_M
        cr = [f["corner"](c_["r"]) for c_ in corners]
        costs["corner"] = float(sum(cr))
        costs["length"] = L3
        total = sum(k.get(name, 0) * v for name, v in costs.items())

        density = sum(k.get(name, 0) * v for name, v in vals.items()) * w3
        density = density + k.get("bend", 0) * bend_v + w3
        for c_, x1 in zip(corners, cr):
            a, b = c_["a"], c_["b"]
            density[a:b + 1] += k.get("corner", 0) * x1 / (b - a + 1)
        out = {"total": float(total), "costs": costs, "density": density,
               "corner_idx": [c_["mid"] for c_ in corners], "length_m": L3, "length_map_m": L}
        if detail:
            out.update({
                "Q": Q, "d": d, "fall": fall, "tsa": tsa, "tsa_v": tsa_v, "ground": g, "design": zd, "cut": cut, "e": e,
                "grade": grade, "side": side, "w3": w3,
                "twi": wet, "existing": off, "dist": dist, "bend_r": bend_r, "bend_turn": np.bincount(own, turn_f * free, minlength=n),
                "bend_v": bend_v, "vals": vals, "zone": zone, "run": run, "sites": sites,
                "corners": [{"i": c_["mid"], "d": float(d[c_["mid"]]), "radius_m": float(c_["r"]),
                             "arc_radius_m": float(c_["r_arc"]), "side_pct": c_["side_pct"], "turn_deg": c_["turn_deg"],
                             "crossing_pct": c_["crossing"], "tsa_min": c_["tsa_min"], "kind": c_["kind"],
                             "dig_m": c_["dig_m"]}
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
    w = r["w3"]
    src = {"grade": (r["grade"], w), "slope": (r["side"], w), "cutfill": (r["e"], w), "tsa": (r["tsa_v"], w),
           "drainage": (r["run"], w), "wet": (r["twi"], w), "existing": (r["existing"], w),
           "bend": (r["bend_r"], r["bend_turn"] / (math.pi / 2)),
           "corner": (np.array([c["radius_m"] for c in r["corners"]]), None),
           "falltread": (r["grade"], w * r["fall"])}
    for name, (x, wt) in src.items():
        m = FACTOR_META[name]
        lo, hi = m["xmin"], m["xmax"]
        xc = np.clip(x, lo, hi - 1e-9 * (hi - lo))
        hgt, _ = np.histogram(xc, bins=bins, range=(lo, hi), weights=wt)
        out[name] = {"lo": lo, "hi": hi, "counts": [round(float(v), 2) for v in hgt],
                     "unit": "corners" if wt is None else "right angles" if name == "bend" else "m"}
    return out
