"""The methods page's analytic sandbox (owner, 2026-10-19/20): surfaces defined
by formula, where the cheapest climb can be worked out, so the refine
engine's answer can be shown beside the known one.

Every factor has a weight (owner, 2026-10-20: "no neglected factor"): the
defaults' weights and curves, two of them VARIED (grade and corners, VARY)
and the rest fixed. Fixed at 0 or off, and said so on the page: drainage (a
stub, and on a climb that never reverses it charges every metre past 60 m
more and more, which no line on these surfaces can avoid), wetness (no
wetness layer), existing tread (none), excess climb (the defaults' 0).

Why the answer is known. Climbing at grade g (rise over run) on ground of
slope s, every per-metre factor is a function of g and s alone on these
smooth surfaces: grade; trail-slope alignment, the angle to the fall line,
acos(g / s); fall-line tread where that angle is in the fall-line band;
side slope, s itself; cut and fill at its floor (a line that follows the
ground needs none). So a metre along the slope costs m(g, s), a metre of
height takes sqrt(1 + g^2) / g of them, and

    c(g, s) = m(g, s) sqrt(1 + g^2) / g          per metre climbed.

A climb that never descends gains each metre of height once, so with no
turns to pay for, the cheapest line climbs each metre at the g <= s that
minimises c there, under one more choice: the steepest 10 m is charged
again (grade_peak_m), so the line may hold itself under a cap G it would
otherwise exceed. climb_plan searches G. Where the chosen g is below s the
line crosses the contours at the angle whose sine is g / s; where it is s,
it goes straight up the fall line.

  cone   a rounded cone, z = H - s sqrt(r^2 + a^2): nearly constant slope,
         so a logarithmic spiral until the rounded top, then straight up
  dome   a spherical cap: the slope grows from 0 at the top to steep at the
         rim, so the spiral changes its angle as it climbs
  plane  a uniform slope between two points straight up it, in a corridor:
         a zigzag, and the number of switchbacks (each a corner) is the one
         trade the formula leaves open, settled by scoring each count with
         the engine's own scoring: a reference, not a proof

Exact only with turns free: the formula's line pays the engine's bend
charges for its curvature, and the corner weight varies, so the engine may
beat it by turning less. The page labels it so.

Filling in between the settings (owner, 2026-10-20): the grid's settings
are run thoroughly (several seeds, long chains, then polished from each
neighbour's line). Between two neighbouring settings the line is TRACED in
small steps from each end (trace_step): each step searches only a narrow
strip around the step before's line, leaning toward the other end's line,
so it follows one family of routes. At each step the cheaper of the two
tracings is shown: where the routes are alike the line slides across;
where they are different routes, it jumps where their costs cross.
"""
import math

import numpy as np

from .refine.route import _simplify
from .refine.score import BEND_STEP_M, Problem, adaptive_resample, curve_fn, densify, fillet, polyline_distance
from .refine.search import _best_tube, _json_detail, anneal, refine
from .refine.settings import merge_settings
from .refine.terrain import Grid

# The two varied factors and their grid (owner, 2026-10-20: grade and corner
# cost as the test case). Weights in the refine panel's metres.
VARY = (
    ("grade", (1.0, 2.0, 3.3, 5.0, 10.0, 30.0)),
    ("corner", (0.0, 20.0, 50.0, 100.0, 167.0, 300.0)),
)
STEPS = 10                    # an edge of the grid is traced in this many steps

# Fixed weights that differ from the defaults, and why (shown on the page).
FIXED = {
    "drainage": (0.0, "a stub; on a climb that never reverses it charges every metre past 60 m, so it is off here"),
}
INACTIVE = {
    "wet": "no wetness layer in the sandbox",
    "existing": "no existing trail here",
}

# The sandbox's grade curve: the defaults' with one node more, so it keeps
# rising past 60 % (settings.py: keep y / x rising). The defaults' curve goes
# flat at 60 %, past which steeper is cheaper per metre of height and the
# best grade jumps from a traverse to the fall line where the ground steepens.
GRADE_EXTRA = [100, 2.5]

# The thorough runs at the grid's settings.
THOROUGH = {"chains": 6, "iterations": 2500}
SEEDS = (11, 23)
POLISH_ITERS = 1200
POLISH_W = 8.0                # m: polishing from a neighbour's line searches this far either side of it
TRACE_ITERS = 1500
TRACE_W = 3.0                 # m: a tracing step's strip, either side of the step before's line
TRACE_LEAN = 4.0              # m: ... and of that line moved this far toward the other end's line
JUMP_M = 10.0                 # m: consecutive lines further apart than this (anywhere) are a jump: a
                              # step moves a line 3 m or less, a change of route 30-80 m (dome, 2026-10-20)

PLANE_ROUND_M = (2.0, 4.0, 8.0, 16.0)        # m: switchback radii tried for the plane's reference line
PROFILE_PTS = 80
SIMPLIFY_M = 0.15
DIG_MIN_M = 0.05              # m: cut or fill shown as a tick from this depth
DIG_EVERY_M = 1.5             # m: at most one tick this far along the line


def settings_at(weights, **params):
    """The sandbox's settings with the varied factors at `weights`
    ({factor: k}): every factor at its default but FIXED, grade's curve
    carried on past 60 %."""
    d = merge_settings()
    f = {name: {"k": k} for name, (k, _) in FIXED.items()}
    f["grade"] = {"curve": d["factors"]["grade"]["curve"] + [GRADE_EXTRA]}
    for name, k in weights.items():
        f.setdefault(name, {})["k"] = float(k)
    return merge_settings({"factors": f, "params": params})


def weights_at(i, j, t=None, axis=None):
    """The varied weights at grid node (i, j), or t of the way from it to the
    next node along `axis` (0: grade, 1: corner). Between two positive
    weights the step is geometric (5 to 10 halfway is 7.1), else linear."""
    w = [VARY[0][1][i], VARY[1][1][j]]
    if t is not None:
        a = w[axis]
        b = VARY[axis][1][(i, j)[axis] + 1]
        w[axis] = a * (b / a) ** t if a > 0 and b > 0 else a + (b - a) * t
    return {VARY[0][0]: w[0], VARY[1][0]: w[1]}


# ---- the formula ----

def per_metre(g, s, st):
    """m(g, s): equivalent metres per metre along the slope of a line at grade
    g on smooth ground of slope s (fractions), turns aside: the per-metre
    factors as score_line charges them, on a line that needs no cut."""
    g, s = np.broadcast_arrays(np.asarray(g, float), np.asarray(s, float))
    F, p = st["factors"], st["params"]
    f = {n: curve_fn(F[n]["curve"]) for n in ("grade", "tsa", "falltread", "slope", "cutfill")}
    k = {n: F[n]["k"] for n in f}
    flat = 100 * s < 4.0                                   # score.FALL_MIN_SLOPE_PCT: no fall line
    tsa = np.where(flat, 90.0, np.degrees(np.arccos(np.clip(g / np.maximum(s, 1e-12), 0, 1))))
    fall = tsa < float(p["fall_tsa_deg"])
    return (1 + k["grade"] * f["grade"](100 * g) + k["tsa"] * f["tsa"](tsa)
            + k["falltread"] * f["falltread"](100 * g) * fall + k["slope"] * f["slope"](100 * s)
            + k["cutfill"] * f["cutfill"](np.zeros_like(g)))


def climb_cost(g, s, st):
    """c(g, s): equivalent metres per metre climbed."""
    g = np.asarray(g, float)
    return per_metre(g, s, st) * np.sqrt(1 + g * g) / g


G_STEP = 0.0025
G_GRID = np.arange(0.005, 1.5, G_STEP)


def climb_plan(s, dz, st):
    """The cheapest grades for a climb whose metres of height dz are on
    ground of slope s (arrays, one entry per stretch), turns aside: at each
    stretch the g <= s minimising c, under the cap G (searched) that
    minimises the total with the steepest-pitch charge. Returns (g per
    stretch, the total in equivalent metres)."""
    s, dz = np.asarray(s, float), np.asarray(dz, float)
    C = climb_cost(G_GRID[None, :], s[:, None], st)
    C[G_GRID[None, :] > s[:, None] + 1e-9] = np.inf
    kg = st["factors"]["grade"]["k"]
    fg = curve_fn(st["factors"]["grade"]["curve"])
    peak = float(st["params"]["grade_peak_m"])
    straight = climb_cost(np.maximum(s, 1e-6), s, st)      # gentler than any grade here: straight up
    cur = np.full(len(s), np.inf)
    idx = np.full(len(s), -1)
    best = None
    for j in range(len(G_GRID)):
        better = C[:, j] < cur
        cur = np.where(better, C[:, j], cur)
        idx = np.where(better, j, idx)
        g = np.where(idx >= 0, G_GRID[np.maximum(idx, 0)], s)
        # Within a step of the grid of grades of the slope is the slope: the
        # fall line. (Else, near a summit, a grade a hair under the slope
        # spirals into it forever, with sub-metre turns.)
        fall = s - g < G_STEP + 1e-9
        g = np.where(fall, s, g)
        cs = np.where(idx >= 0, np.where(fall, straight, cur), straight)
        total = float((cs * dz).sum()) + kg * peak * float(fg(100 * g.max()))
        if best is None or total < best[1] - 1e-9:
            best = (g.copy(), total)
    return best


class Surface:
    name = label = blurb = ""
    kind = "analytic"                  # the page's file is <kind>-<name>.json
    has_formula = True
    extent = 130.0
    corridor = 120.0

    def z(self, X, Y):
        raise NotImplementedError

    def grid(self, res=1.0):
        e = self.extent
        return Grid.from_function(self.z, -e, -e, e, e, res)

    def line(self):
        raise NotImplementedError

    def exact(self, st, prob=None):
        raise NotImplementedError

    def best_grade(self, st):
        raise NotImplementedError


class _Round(Surface):
    """A surface of revolution about the origin, climbed from (r0, 0) to the top."""
    r0 = 110.0
    DR = 0.05

    def line(self):
        return np.array([[self.r0, 0.0], [0.0, 0.0]])

    def _plan(self, st):
        r = np.arange(self.r0, 0.0, -self.DR)
        s = self.slope(r)
        g, _ = climb_plan(s, s * self.DR, st)
        return r, s, g

    def best_grade(self, st):
        return float(self._plan(st)[2].max())

    def exact(self, st, prob=None):
        # dtheta/dr = -sqrt(s^2 - g^2) / (r g) where g < s, else 0 (fall line).
        r, s, g = self._plan(st)
        g = np.maximum(g, 1e-6)
        rate = np.where(s > g + 1e-6, np.sqrt(np.maximum(s * s - g * g, 0)) / (np.maximum(r, 1e-9) * g), 0.0)
        th = np.concatenate([[0.0], np.cumsum(0.5 * (rate[1:] + rate[:-1]) * self.DR)])
        xy = np.column_stack([r * np.cos(th), r * np.sin(th)])
        xy = np.vstack([xy, [[0.0, 0.0]]])
        keep = np.concatenate([[True], np.hypot(*np.diff(xy, axis=0).T) > 0.05])
        return xy[keep]


class Cone(_Round):
    name, label = "cone", "Rounded cone"
    blurb = ("A cone of steady slope with a rounded top. The cheapest climb crosses every contour at the same "
             "angle, a logarithmic spiral, until the top flattens below the best grade, then goes straight up.")
    S, H, A = 0.6, 80.0, 10.0

    def z(self, X, Y):
        return self.H - self.S * np.sqrt(X * X + Y * Y + self.A ** 2)

    def slope(self, r):
        return self.S * r / np.sqrt(r * r + self.A ** 2)


class Dome(_Round):
    name, label = "dome", "Dome"
    blurb = ("A spherical cap: flat on top, steepening to the rim. The cheapest climb crosses the contours at a "
             "changing angle as the slope eases, then goes straight up where it is gentler than the best grade.")
    R, RIM = 150.0, 105.0
    r0 = 100.0

    def z(self, X, Y):
        r = np.minimum(np.hypot(X, Y), self.RIM)
        return np.sqrt(self.R ** 2 - r * r) - math.sqrt(self.R ** 2 - self.RIM ** 2)

    def slope(self, r):
        r = np.minimum(r, self.RIM)
        return r / np.sqrt(self.R ** 2 - r * r)


class Plane(Surface):
    name, label = "plane", "Uniform slope"
    blurb = ("A uniform slope climbed between two points straight up it, within 30 m either side. The cheapest "
             "line zigzags at the best grade; how many switchbacks is the trade between corners and grade, "
             "settled by scoring each count.")
    S, D, W = 0.5, 160.0, 30.0
    MARGIN = 4.0
    extent = 100.0
    corridor = 30.0

    def z(self, X, Y):
        return self.S * (Y + self.extent)

    def slope(self, r):
        return np.full(np.shape(r), self.S)

    def line(self):
        return np.array([[0.0, -self.D / 2], [0.0, self.D / 2]])

    def best_grade(self, st):
        g, _ = climb_plan(np.array([self.S]), np.array([self.S * self.D]), st)
        return float(g[0])

    def zigzag(self, m, g):
        """Climb at grade g with m switchbacks, alternately A either side:
        lateral travel 2 A m over D up the slope. None if it cannot fit."""
        P0, P1 = self.line()
        if m == 0:
            return np.array([P0, P1])
        need = self.D * math.sqrt(max((self.S / g) ** 2 - 1, 0.0))
        A = need / (2 * m)
        if A > self.W - self.MARGIN or A < 1.0:
            return None
        xs = [0.0] + [A * (1 if i % 2 == 0 else -1) for i in range(m)] + [0.0]
        lat = np.concatenate([[0.0], np.cumsum(np.abs(np.diff(xs)))])
        ys = P0[1] + self.D * lat / lat[-1]
        return np.column_stack([xs, ys])

    def exact(self, st, prob=None):
        """The zigzag at the best grade (or as near as the corridor lets it
        come) with the switchback count and radius the engine's scoring finds
        cheapest. A reference, not a proof."""
        g = self.best_grade(st)
        best = None
        for m in range(0, 40):
            Z = self.zigzag(m, g)
            if Z is None and m > 0:
                A = self.W - self.MARGIN
                gm = self.S * self.D / math.hypot(self.D, 2 * A * m)
                if gm < g:
                    continue
                Z = self.zigzag(m, gm * 1.0001)
            if Z is None:
                continue
            for rr in (PLANE_ROUND_M if m else (0.0,)):
                Q = fillet(Z, rr, BEND_STEP_M, r_min=0.5) if m else Z
                t = score_fixed(prob, Q) if prob is not None else None
                if t is not None and (best is None or t["total"] < best[0]):
                    best = (t["total"], Q)
        return best[1] if best else self.line()


class Terrain(Surface):
    """A real patch (owner, 2026-10-26: the Graduation Peak gully, grade and
    corners varied): lidar on a 1 m grid in local metres about the patch's
    centre, read from `path`, which build_methods fetches into its cache (the
    terrain is not committed). The start and end are made up: no scouting
    line is published. There is no formula on real ground, so no reference
    line; the straight line is the comparison."""
    name, label, kind = "gully-across", "Graduation Peak: across the gullies", "terrain"
    blurb = ("Real ground, from the Kachemak Bay 2023 lidar, below Graduation Peak: corner to corner across the "
             "patch, from 20 m in from the north-west corner to 20 m in from the south-east one (made up), 31 m "
             "of climb in 386 m (8 %), across two incised gullies whose walls are 80-120 %. There is no formula "
             "here: the straight line is the comparison.")
    has_formula = False
    CENTRE_LONLAT = (-151.66042, 59.43459)
    # Owner, 2026-10-26: 20 m in along the diagonals from the patch's north-west
    # and south-east corners (the fetched grid runs x -151..151, y -150..150),
    # to use the patch. Was (-60, -95) to (125, -40), up the spur. Gentle
    # overall, so grade alone rarely asks for a corner; TerrainUp is the
    # steep diagonal on the same ground, for the contrast.
    START, END = (-136.9, 135.9), (136.9, -135.9)
    corridor = 40.0
    extent = 150.0                     # m either side of the centre, fetched
    path = None
    _grid = None

    def grid(self, res=1.0):
        if self._grid is None:
            if self.path is None:
                raise ValueError("the gully's terrain is fetched by manage.py build_methods; no path set")
            d = np.load(self.path)
            self._grid = Grid(d["z"], float(d["x0"]), float(d["y0"]), float(d["res"]))
        g = self._grid
        k = max(1, int(round(res / g.res)))
        return g if k == 1 else Grid(g.z[::k, ::k], g.x0, g.y0, g.res * k)

    def line(self):
        return np.array([self.START, self.END])

    def exact(self, st, prob=None):
        return None

    def best_grade(self, st):
        return None

    def ground_slope(self):
        """The 90th percentile of the ground's slope within the corridor."""
        g = self.grid()
        gy, gx = np.gradient(g.z, g.res)
        dist = _corridor_distance(self, g)
        return float(np.nanpercentile(np.hypot(gx, gy)[dist <= self.corridor], 90))


class TerrainUp(Terrain):
    """The same patch along the other diagonal (owner, 2026-10-26: both, for
    the contrast): south-west to north-east, up the main gully, steep enough
    overall that holding a grade needs corners."""
    name, label = "gully-up", "Graduation Peak: up the gully"
    blurb = ("Real ground, from the Kachemak Bay 2023 lidar, below Graduation Peak: corner to corner across the "
             "patch, from 20 m in from the south-west corner to 20 m in from the north-east one (made up), 90 m "
             "of climb in 386 m (23 %), along the main gully, whose walls are 80-120 %. The same ground as "
             "\"across the gullies\", on the steep diagonal. There is no formula here: the straight line is "
             "the comparison.")
    START, END = (-136.9, -135.9), (136.9, 135.9)


SURFACES = {s.name: s for s in (Cone(), Dome(), Plane(), Terrain(), TerrainUp())}


def max_slope(surface):
    if hasattr(surface, "ground_slope"):
        return surface.ground_slope()
    return float(surface.slope(np.array(getattr(surface, "r0", 0.0))))


def problem(surface, st, grid=None, **kw):
    return Problem([surface.line()], grid or surface.grid(), st, **kw)


def score_fixed(prob, xy):
    """Score a given line (densely sampled) with the engine's scoring, at its
    best tube; the detail dict, or None."""
    xy = np.asarray(xy, float)
    Q, Qf = adaptive_resample(xy, prob.h), densify(xy, BEND_STEP_M)
    found = _best_tube(lambda t, kk: prob.score_line(Q, min(t, prob.tmax), kk, Qf=Qf), prob.base_k)
    if found is None:
        return None
    return prob.score_line(Q, min(found[1], prob.tmax), prob.base_k, detail=True, Qf=Qf)


# ---- lines as JSON ----

def line_json(c):
    """A line in refine's candidate form (search._json_detail) as the page
    wants it: plan, profile, totals, and where it is dug in or filled."""
    xy_full = np.asarray(c["tracks"]["xy"], float)
    e = np.asarray(c["tracks"]["cutfill"], float)
    xy = _simplify(xy_full, SIMPLIFY_M) if len(xy_full) > 2 else xy_full
    p = c["profile"]
    step = max(1, len(p["d"]) // PROFILE_PTS)
    idx = sorted(set(range(0, len(p["d"]), step)) | {len(p["d"]) - 1})
    tq = np.gradient(xy_full, axis=0)
    tq = tq / np.maximum(np.hypot(tq[:, 0], tq[:, 1]), 1e-9)[:, None]
    along = np.concatenate([[0.0], np.cumsum(np.hypot(*np.diff(xy_full, axis=0).T))])
    dig, last = [], -np.inf
    for (x, y), (tx, ty), ee, d_ in zip(xy_full, tq, e, along):
        if abs(ee) >= DIG_MIN_M and d_ - last >= DIG_EVERY_M:
            dig.append([round(float(x), 1), round(float(y), 1), round(float(-ty), 2), round(float(tx), 2), round(float(ee), 2)])
            last = d_
    st = c["stats"]
    return {
        "xy": [[round(float(x), 2), round(float(y), 2)] for x, y in xy],
        "total": round(float(c["total"]), 1),
        "contrib": {k: round(float(v), 1) for k, v in c["contrib"].items() if abs(v) >= 0.05},
        "length_m": round(float(c["length_m"]), 1),
        "grade_max": st["grade_max"],
        "grade_mean": round(100 * st["net_climb_m"] / max(float(c["length_map_m"]), 1e-9), 1),
        "corners": st["corners"],
        "cut_max": st["cut_max"],
        "fill_max": st["fill_max"],
        "profile": {key: [p[key][i] for i in idx] for key in ("d", "ground", "design")},
        "dig": dig,
        "t": c["t"],
        "_xy": xy_full,                        # full samples, for the next step; dropped when written
    }


def _detail_json(prob, r):
    return line_json(_json_detail(prob, r, [r["Q"]]))


# ---- the runs ----

def thorough(surface, weights, seed, grid=None):
    """The engine as the panel runs it (route-finder and chains), longer,
    offering its cheapest line."""
    st = settings_at(weights, corridor_m=surface.corridor, seed=seed, **THOROUGH)
    grid = grid or surface.grid()
    out = refine([surface.line()], grid, st, offer_all=True)
    best = min(out["candidates"], key=lambda c: c["total"]) if out["candidates"] else out["input"]
    j = line_json(best)
    j["input"] = round(out["input"]["total"], 1)
    return j


def trace_step(surface, weights, prev, toward=None, w=TRACE_W, lean=TRACE_LEAN, iters=None,
               seed=0, grid=None):
    """One step of a tracing (or a polish): the search starts from `prev`
    (a line_json) and keeps within `w` of it, or of it moved up to `lean`
    toward the line `toward`, whichever is nearer. Returns a line_json, or
    None if `prev` does not fit."""
    iters = iters or TRACE_ITERS
    st = settings_at(weights, corridor_m=w, input_sigma_m=0.5, sketch_radius_m=1.5, knot_m=4.0)
    grid = grid or surface.grid()
    P = prev["_xy"]
    extra = []
    if toward is not None and lean > 0:
        T = toward["_xy"]
        _, pos = polyline_distance(P[:, 0], P[:, 1], T)
        sT = np.concatenate([[0.0], np.cumsum(np.hypot(*np.diff(T, axis=0).T))])
        v = np.column_stack([np.interp(pos, sT, T[:, 0]), np.interp(pos, sT, T[:, 1])]) - P
        L = np.hypot(v[:, 0], v[:, 1])
        extra = [P + v * np.minimum(1.0, lean / np.maximum(L, 1e-9))[:, None]]
    try:
        prob = Problem([P], grid, st, limit_xy=extra, adaptive=True)
    except ValueError:
        return None
    # Within the strip AND within the surface's own search limit (2026-10-21:
    # measured from the step before's line alone, steps drifted out of the
    # plane's 30 m corridor a few metres at a time, to the grid's edge 100 m
    # out): the distance that counts is the larger, the corridor's scaled to
    # the strip's width.
    D = np.maximum(prob.dist_grid.z, _corridor_distance(surface, grid) * (w / surface.corridor))
    prob.dist_grid = Grid(D, grid.x0, grid.y0, grid.res)
    k = prob.base_k
    rng = np.random.default_rng(seed)
    try:
        total, c, t = anneal(prob, rng, iters, k, min(float(prev["t"]), prob.tmax))
    except ValueError:
        return None
    r = prob.score(c, t, k, detail=True)
    return _detail_json(prob, r) if r is not None else None


_CORRIDOR = {}


def _corridor_distance(surface, grid):
    """Distance from the surface's input line on the grid's cells (cached)."""
    key = (surface.name, grid.x0, grid.y0, grid.res, grid.z.shape)
    if key not in _CORRIDOR:
        _CORRIDOR[key] = problem(surface, settings_at(weights_at(0, 0), corridor_m=surface.corridor), grid).dist_grid.z
    return _CORRIDOR[key]


def formula_line(surface, weights, grid=None):
    """The formula's line at these weights, scored by the engine's scoring
    (None on real ground, which has no formula)."""
    if not surface.has_formula:
        return None
    st = settings_at(weights, corridor_m=surface.corridor)
    grid = grid or surface.grid()
    prob = problem(surface, st, grid)
    xy = surface.exact(st, prob=prob)
    r = score_fixed(prob, xy)
    if r is None:
        return None
    j = _detail_json(prob, r)
    j["best_grade_pct"] = round(100 * surface.best_grade(st), 1)
    return j


def apart(a, b):
    """The furthest either line strays from the other, m."""
    A, B = a["_xy"], b["_xy"]
    return max(float(polyline_distance(A[:, 0], A[:, 1], B)[0].max()),
               float(polyline_distance(B[:, 0], B[:, 1], A)[0].max()))


def terrain_json(surface, res=2.0):
    g = surface.grid(res)
    return {"x0": g.x0, "y0": g.y0, "res": g.res, "w": g.w, "h": g.h,
            "z": [round(float(v), 2) for v in g.z.ravel()]}


def factors_json(st):
    """Every factor with its weight and curve, for the page's table and its
    cost-per-metre-climbed chart."""
    from .refine.settings import FACTOR_META, FACTOR_ORDER
    vary = {n for n, _ in VARY}
    out = []
    for n in FACTOR_ORDER:
        m = FACTOR_META[n]
        out.append({"key": n, "label": m["label"], "per": m["per"], "unit": m["unit"],
                    "k": st["factors"][n]["k"], "curve": st["factors"][n]["curve"],
                    "varies": n in vary,
                    "note": FIXED.get(n, (None, INACTIVE.get(n, "")))[1] or INACTIVE.get(n, "")})
    return out
