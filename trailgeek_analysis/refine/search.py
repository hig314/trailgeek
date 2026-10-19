"""The search: simulated annealing over the candidate's control points and
the cut/fill tube width, several chains, and the candidates offered from them
(docs/REFINE_TOOL.md §3, and "the sketch as evidence").

A candidate is its own smooth curve (score.Problem); every chain starts from
the smooth fit to the person's line. Moves act along the CANDIDATE's own
sideways direction at each control:
  bump        a smooth sideways change around a control, of random width,
              centred at random or (half the time) where the cost is
  bump+slide  a bump, then the controls beyond it shifted along the contour
              by what restores their elevations, so the rest of the climb
              stays on grade (the lengthwise dependence)
  corner      a bump centred on a detected corner
  smooth      the controls low-passed over a stretch: irons out kinks, lets
              two turns merge or a spurious corner go
  tube        the cut/fill tube half-width
Every RESPACE_EVERY iterations the controls are re-spaced along the curve
(a refit of the same shape), so sideways-only moves cannot bunch them.
"""
import math

import numpy as np

from . import spline
from .route import route_line
from .score import BEND_STEP_M, PER_M, Problem, densify, fit_controls, histograms, resample_legs
from .settings import FACTOR_META

SIGMA0, SIGMA_END = 5.0, 0.3        # m: bump amplitude, first and last iteration
COOL = 1e-3                         # final temperature / initial
TUBE_MIN = 0.02                     # m
GRADE_MAX_SLACK = 1.0               # %: never offer a candidate whose steepest 10 m is worse than the input's by more
MARGIN = 0.10                       # a candidate may be worse on one factor by at most this share of the input's effort
                                    # beyond its length, or of that factor's unit (100 m at curve 1, or one corner or
                                    # right angle at curve 1), whichever is larger
RESPACE_EVERY = 250
RESPACE_TOL = 0.01                  # a re-spacing is kept if it changes the total by less than this share

# (key, label, weight multipliers, share of the search distance)
CHAIN_VARIANTS = [
    ("best", "Best overall", {}, 1.0),
    ("closer", "Within half the distance", {}, 0.5),
    ("steadier", "Steadier grade", {"grade": 2.0}, 1.0),
    ("best", "Best overall", {}, 1.0),
]
EDGE_M = 1.0                        # m: within this of the search's limit counts as "along the edge"
ROUTE_SKETCH_R_M = 3.0              # m: the route's own corners are rounded this much for its chain's start
ROUTE_SIGMA_M = 0.5                 # m: ... and it is fitted closely (it is a path, not a sketch)
ROUTE_KNOT_M = 4.0                  # m: at most this between its controls, so switchbacks survive the fit


def _control_of_sample(prob, i):
    return int(np.clip(np.searchsorted(prob.ctrl_u, prob.u[i]), 0, prob.nfree - 1))


def _bump(prob, c, c_ref, jc, width, amp):
    _, nrm = prob.control_frames(c_ref)
    k = np.arange(len(c))
    c += (amp * np.exp(-0.5 * ((k - jc) / width) ** 2))[:, None] * nrm


def _smooth(prob, c, jc, width, strength):
    C = prob.full(c)
    r = int(math.ceil(3 * width))
    ker = np.exp(-0.5 * (np.arange(-r, r + 1) / width) ** 2)
    ker /= ker.sum()
    pad = np.pad(C, ((r, r), (0, 0)), mode="edge")
    sm = np.column_stack([np.convolve(pad[:, 0], ker, mode="valid"), np.convolve(pad[:, 1], ker, mode="valid")])
    k = np.arange(len(c))
    wgt = strength * np.exp(-0.5 * ((k - jc) / (2 * width)) ** 2)
    c += wgt[:, None] * (sm[1:-1] - c)


def _slide(prob, rng, c_old, c_new, jc, width):
    """Shift the controls beyond the bump along the contour so their ground
    elevation returns to what it was before the bump moved the line."""
    dirn = 1 if rng.random() < 0.5 else -1
    je = jc + dirn * int(math.ceil(2 * width))
    if not 0 <= je < prob.nfree:
        return
    grid = prob.grid
    p_old, _ = prob.control_frames(c_old)
    p_new, nrm = prob.control_frames(c_new)
    dz = float(grid.sample(*p_new[je]) - grid.sample(*p_old[je]))
    if not math.isfinite(dz) or abs(dz) < 0.02:
        return
    ks = np.arange(je + dirn, prob.nfree if dirn > 0 else -1, dirn)
    if not len(ks):
        return
    pp, nn = p_new[ks], nrm[ks]
    dzdn = grid.sample(*(pp + 0.5 * nn).T) - grid.sample(*(pp - 0.5 * nn).T)
    along = np.concatenate([[0.0], np.cumsum(np.hypot(*np.diff(np.vstack([p_new[je], pp]), axis=0).T))])[1:]
    taper = np.exp(-along / rng.uniform(30, 150))
    with np.errstate(divide="ignore", invalid="ignore"):
        adj = np.where(np.abs(dzdn) > 0.05, -dz / dzdn, 0.0) * taper
    adj = np.clip(np.nan_to_num(adj), -5, 5)
    c_new[ks] += adj[:, None] * nn


def propose(prob, rng, c, t, cur, sigma):
    c2, t2 = c.copy(), t
    u = rng.random()
    corners = cur["corner_idx"]
    if u < 0.1:
        t2 = float(np.clip(t + rng.normal(0, 0.05), TUBE_MIN, prob.tmax))
    elif u < 0.25 and corners:
        jc = _control_of_sample(prob, corners[rng.integers(len(corners))])
        _bump(prob, c2, c, jc, 1.5, rng.normal(0, 1.5 * sigma))
    elif u < 0.35:
        if corners and rng.random() < 0.5:
            jc = _control_of_sample(prob, corners[rng.integers(len(corners))])
        else:
            jc = int(rng.integers(prob.nfree))
        _smooth(prob, c2, jc, float(rng.choice([1.0, 1.5, 2.5])), float(rng.uniform(0.3, 1.0)))
    else:
        dens = cur["density"]
        tot = float(dens.sum())
        if rng.random() < 0.5 and tot > 0:
            jc = _control_of_sample(prob, int(rng.choice(len(dens), p=dens / tot)))
        else:
            jc = int(rng.integers(prob.nfree))
        width = float(rng.choice([0.7, 1.0, 1.5, 2.5, 4.0, 8.0]))
        _bump(prob, c2, c, jc, width, rng.normal(0, sigma))
        if u < 0.6:
            _slide(prob, rng, c, c2, jc, width)
    return c2, t2


def anneal(prob, rng, iters, k, t0, progress=None):
    """One chain from the smooth fit. Returns (best total, c, t)."""
    c = prob.start()
    t = t0
    cur = prob.score(c, t, k)
    if cur is None:
        raise ValueError("the smoothed line leaves the search distance or the terrain data")
    best = (cur["total"], c.copy(), t)
    worse = []
    for _ in range(30):
        c2, t2 = propose(prob, rng, c, t, cur, SIGMA0)
        r = prob.score(c2, t2, k)
        if r is not None and r["total"] > cur["total"]:
            worse.append(r["total"] - cur["total"])
    T0 = float(np.median(worse)) / math.log(2) if worse else max(cur["total"] * 1e-3, 1e-6)
    for i in range(iters):
        frac = i / max(1, iters)
        T = T0 * COOL ** frac
        sigma = SIGMA0 * (SIGMA_END / SIGMA0) ** frac
        if i and i % RESPACE_EVERY == 0:
            c3 = prob.respace(c)
            r = prob.score(c3, t, k) if c3 is not None else None
            if r is not None and abs(r["total"] - cur["total"]) <= RESPACE_TOL * max(cur["total"], 1e-9) \
                    and not prob.folds(c3):
                c, cur = c3, r
        c2, t2 = propose(prob, rng, c, t, cur, sigma)
        r = prob.score(c2, t2, k)
        if r is not None:
            delta = r["total"] - cur["total"]
            if delta < 0 or rng.random() < math.exp(-delta / T):
                if not prob.folds(c2, c):
                    c, t, cur = c2, t2, r
                    if cur["total"] < best[0]:
                        best = (cur["total"], c.copy(), t)
        if progress and i % 25 == 0:
            progress(i, best[0])
    return best


WET_TWI = 10.0                      # the table's "wet ground" metres: TWI at least this


TRACK_KEYS = {"grade": "grade", "slope": "side", "tsa": "tsa_v", "cutfill": "e", "wet": "twi", "drainage": "run",
              "existing": "existing"}


def _tracks(prob, r):
    """Per sample, for the factor panel: each factor's value, the extra
    effort per metre it charges there (k x curve; bends: their metres over the
    sample's length), the bend radius, and the sample's position (xy, turned
    into lon/lat by the caller)."""
    k = prob.base_k
    rd = lambda a, nd: [round(float(v), nd) for v in a]
    out = {name: rd(r[key], 2) for name, key in TRACK_KEYS.items()}
    out["bend"] = rd(np.minimum(r["bend_r"], 1000.0), 2)
    cost = {name: rd(k.get(name, 0) * r["vals"][name], 4) for name in r["vals"]}
    cost["bend"] = rd(k.get("bend", 0) * r["bend_v"] / np.maximum(r["w"], 1e-6), 4)
    out["cost"] = cost
    out["xy"] = [[round(float(x), 2), round(float(y), 2)] for x, y in r["Q"]]
    return out


def _json_detail(prob, r, legs):
    """A scored line as plain lists, ready for JSON."""
    rd = lambda a, nd: [round(float(v), nd) for v in a]
    grade = r["grade"]
    dist = r["dist"]
    return {
        "total": round(r["total"], 1),
        "costs": {k: round(v, 3) for k, v in r["costs"].items()},
        "contrib": {k: round(v, 2) for k, v in r["contrib"].items()},
        "legs_xy": [L.tolist() for L in legs],
        "t": round(r["t"], 3),
        "length_m": round(r["length_m"], 1),
        "length_map_m": round(r["length_map_m"], 1),
        "profile": {"d": rd(r["d"], 1), "ground": rd(r["ground"], 2), "design": rd(r["design"], 2)},
        "tracks": _tracks(prob, r),
        "corners": [{k: (round(v, 1) if isinstance(v, float) else v) for k, v in c.items()} for c in r["corners"]],
        "sites_d": [round(float(r["d"][i]), 1) for i in r["sites"]],
        "stats": {
            "grade_max": round(float(grade.max()), 1),
            "grade_p95": round(float(np.percentile(grade, 95)), 1),
            "cut_max": round(float(max(0.0, -r["e"].min())), 2),
            "fill_max": round(float(max(0.0, r["e"].max())), 2),
            "cut_mean": round(float((r["cut"] * r["w"]).sum() / r["length_map_m"]), 3),
            "corner_dig_max": round(max([c["dig_m"] for c in r["corners"]]), 2) if r["corners"] else None,
            "offset_max": round(float(dist.max()), 1),
            "offset_mean": round(float((dist * r["w"]).sum() / r["length_map_m"]), 1),
            "edge_m": round(float((r["w"] * (dist >= prob.corridor - EDGE_M)).sum()), 1),
            "corners": len(r["corners"]),
            "corner_min_r": round(min([c["radius_m"] for c in r["corners"]]), 1) if r["corners"] else None,
            "climbing_turns": sum(1 for c in r["corners"] if c["kind"] == "climbing turn"),
            "switchbacks": sum(1 for c in r["corners"] if c["kind"] == "switchback-like"),
            "fall_m": round(float((r["w"] * r["fall"]).sum()), 1),
            "bend_min_r": round(float(r["bend_r"].min()), 1),
            "reversals": len(r["sites"]),
            "wet_m": round(float((r["w"] * (r["twi"] >= WET_TWI)).sum()), 1),
        },
    }


def _best_tube(score, k):
    best = None
    for t in np.linspace(TUBE_MIN, 0.75, 8):
        r = score(float(t), k)
        if r is not None and (best is None or r["total"] < best[0]["total"]):
            best = (r, float(t))
    return best


def refine(legs_xy, grid, settings, progress=None, seed=None, twi_grid=None, existing_xy=None):
    """Refine a stretch made of legs (each an (m, 2) array in a metric CRS).
    `settings` is a full, cleaned settings dict (settings.clean_settings).
    `progress(fraction, best_total)` is called now and then.

    Returns {"input": the line as drawn, "candidates": [... + key/label],
    "seed", "histograms", "chains"}. The line as drawn is scored with the
    same tube as the candidates, so the comparison is fair; the smooth fit
    to it is offered as the candidate "Your line, smoothed", so the gain
    from rounding alone and the gain from fitting the terrain show apart."""
    p = settings["params"]
    prob = Problem(legs_xy, grid, settings, twi_grid, existing_xy)
    k = prob.base_k
    found = _best_tube(lambda t, kk: prob.score_line(prob.P, t, kk), k)
    if found is None:
        raise ValueError("the selected legs run off the terrain data, so they cannot be scored")
    t_in = min(found[1], prob.tmax)
    inp = prob.score_line(prob.P, t_in, k, detail=True)
    prob.grade_ref = float(inp["grade"].max())
    sm_found = _best_tube(lambda t, kk: prob.score(prob.start(), min(t, prob.tmax), kk), k)
    t0 = sm_found[1] if sm_found else t_in
    if seed is None:
        seed = int(p.get("seed") or 0) or int(np.random.default_rng().integers(1, 2 ** 31 - 1))
    rng = np.random.default_rng(seed)
    iters, chains = int(p["iterations"]), int(p["chains"])
    results = []
    # The route-finder's chain (owner, 2026-10-18): the cheapest path over a
    # grid of the terrain within the limit, which can wind where the line goes
    # straight up, polished by the annealer like any other chain.
    route = route_line(prob, k) if chains > 0 else None
    runs = chains + (1 if route is not None else 0)
    for ci in range(chains):
        key, label, mult, reach = CHAIN_VARIANTS[ci % len(CHAIN_VARIANTS)]
        kk = {f: v * mult.get(f, 1.0) for f, v in k.items()}
        crng = np.random.default_rng(rng.integers(1, 2 ** 31 - 1))
        cb = None
        if progress:
            cb = lambda i, b, ci=ci: progress((ci * iters + i) / (runs * iters), b)
        prob.limit = prob.corridor * reach
        try:
            total, c, t = anneal(prob, crng, iters, kk, min(t0, prob.tmax), cb)
        except ValueError:
            if reach == 1.0:
                raise
            continue                                  # the start does not fit the narrower limit
        finally:
            prob.limit = prob.corridor
        r = prob.score(c, t, k, detail=True)
        if r is not None:
            results.append((key, label, c, r))
    if route is not None:
        pr = Problem([route], prob.grid, dict(settings, params=dict(p, sketch_radius_m=ROUTE_SKETCH_R_M,
                                                                     input_sigma_m=ROUTE_SIGMA_M,
                                                                     knot_m=min(float(p["knot_m"]), ROUTE_KNOT_M))),
                     prob.twi_grid, like=prob)
        pr.existing_xy, pr.grade_ref = prob.existing_xy, prob.grade_ref
        crng = np.random.default_rng(rng.integers(1, 2 ** 31 - 1))
        cb = (lambda i, b: progress((chains * iters + i) / (runs * iters), b)) if progress else None
        routed = []
        try:
            total, c, t = anneal(pr, crng, iters, k, min(t0, prob.tmax), cb)
            r = pr.score(c, t, k, detail=True)
            if r is not None:
                routed.append(r)
        except ValueError:
            pass                                      # its smooth start strays past the limit
        # The route itself, as found: a smooth curve can lose a tight turn it had.
        Qr, _ = resample_legs([route], prob.h)
        found_r = _best_tube(lambda tt, kk: pr.score_line(Qr, min(tt, prob.tmax), kk, Qf=densify(route, BEND_STEP_M)), k)
        if found_r is not None:
            routed.append(pr.score_line(Qr, min(found_r[1], prob.tmax), k, detail=True, Qf=densify(route, BEND_STEP_M)))
        if routed:
            results.append(("routed", "From the route-finder", None, min(routed, key=lambda r: r["total"])))
    if progress:
        progress(1.0, min([r["total"] for *_, r in results], default=inp["total"]))

    def acceptable(r):
        if r["total"] >= inp["total"] - 1e-9:
            return False
        if k.get("grade", 0) > 0 and r["grade"].max() > prob.grade_ref + GRADE_MAX_SLACK:
            return False
        extra = inp["total"] - inp["contrib"].get("length", 0)
        return all(r["contrib"][f] - inp["contrib"][f] <= MARGIN * max(extra, unit(f))
                   for f in r["contrib"])

    def unit(f):
        if f == "length":
            return PER_M
        return k.get(f, 0) * (PER_M if FACTOR_META[f]["per"] == "metre" else 1)

    order = sorted(results, key=lambda x: x[3]["total"])
    wanted = [("best", "Best overall" + (" (route-finder)" if order[0][0] == "routed" else "")) + order[0][2:]] if order else []
    for key in ("closer", "steadier", "routed"):
        mine = [x for x in order if x[0] == key]
        if mine:
            wanted.append(mine[0])
    if sm_found is not None and chains > 0:
        c0 = prob.start()
        r0 = prob.score(c0, t0, k, detail=True)
        if r0 is not None:
            wanted.append(("smoothed", "Your line, smoothed", c0, r0))
    picked = []
    for key, label, c, r in wanted:
        if not acceptable(r):
            continue
        if any(r["Q"].shape == q[3]["Q"].shape and np.hypot(*(r["Q"] - q[3]["Q"]).T).max() < 0.5 for q in picked):
            continue
        picked.append((key, label, c, r))
    cands = []
    for key, label, c, r in picked:
        j = _json_detail(prob, r, prob.legs_of(r["Q"]))
        j.update({"key": key, "label": label})
        cands.append(j)
    in_legs = [prob.P[a:b + 1] for a, b in zip(prob.joints[:-1], prob.joints[1:])]
    return {
        "input": _json_detail(prob, inp, in_legs),
        "candidates": cands,
        "seed": seed,
        "histograms": histograms(inp),
        "chains": [{"key": key, "label": label, "total": round(r["total"], 1)} for key, label, c, r in results],
    }
