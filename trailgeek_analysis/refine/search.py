"""The search: simulated annealing over the offset field and the cut/fill
tube width, several chains, and the candidates offered from them
(docs/REFINE_TOOL.md §3).

Moves:
  bump        a smooth local offset change, centred on a random control or
              (half the time) where the cost is, of random width
  bump+slide  a bump, then the stretch beyond it shifted along the contour
              by what restores its elevations, so the rest of the climb
              stays on grade (the lengthwise dependence)
  corner      a bump centred on a detected corner, both arms together
  tube        the cut/fill tube half-width
"""
import math

import numpy as np

from .score import Problem, histograms

SIGMA0, SIGMA_END = 5.0, 0.3        # m: bump amplitude, first and last iteration
COOL = 1e-3                         # final temperature / initial
TUBE_MIN = 0.02                     # m
GRADE_MAX_SLACK = 1.0               # %: never offer a candidate whose steepest 10 m is worse than the input's by more
MARGIN = 0.05                       # a candidate may be worse on one factor by at most this share of the input's total

CHAIN_VARIANTS = [
    ("best", "Best overall", {}),
    ("closer", "Closer to the input", {"offset": 2.0, "length": 2.0}),
    ("steadier", "Steadier grade", {"grade": 2.0}),
    ("best", "Best overall", {}),
]


def _nearest_knot(prob, i):
    j = int(np.searchsorted(prob.knot_idx, i))
    j = min(max(j, 0), prob.nfree - 1)
    if j > 0 and abs(prob.knot_idx[j - 1] - i) < abs(prob.knot_idx[j] - i):
        j -= 1
    return j


def _bump(c, jc, width, amp):
    k = np.arange(len(c))
    c += amp * np.exp(-0.5 * ((k - jc) / width) ** 2)


def _slide(prob, rng, c_old, c_new, jc, width):
    """Shift the controls beyond the bump along the contour so their ground
    elevation returns to what it was before the bump moved the line."""
    dirn = 1 if rng.random() < 0.5 else -1
    je = jc + dirn * int(math.ceil(2 * width))
    if not 0 <= je < prob.nfree:
        return
    ie = prob.knot_idx[je]
    P, N, B, grid = prob.P, prob.N, prob.B, prob.grid
    o0, o1 = B[ie] @ c_old, B[ie] @ c_new
    z0 = grid.sample(*(P[ie] + o0 * N[ie]))
    z1 = grid.sample(*(P[ie] + o1 * N[ie]))
    dz = float(z1 - z0)
    if not math.isfinite(dz) or abs(dz) < 0.02:
        return
    ks = np.arange(je + dirn, prob.nfree if dirn > 0 else -1, dirn)
    if not len(ks):
        return
    idx = prob.knot_idx[ks]
    oo = B[idx] @ c_new
    pp = P[idx] + (oo + 0.5)[:, None] * N[idx]
    pm = P[idx] + (oo - 0.5)[:, None] * N[idx]
    dzdo = grid.sample(pp[:, 0], pp[:, 1]) - grid.sample(pm[:, 0], pm[:, 1])
    Ls = rng.uniform(30, 150)
    taper = np.exp(-np.abs(prob.d_in[idx] - prob.d_in[ie]) / Ls)
    with np.errstate(divide="ignore", invalid="ignore"):
        adj = np.where(np.abs(dzdo) > 0.05, -dz / dzdo, 0.0) * taper
    adj = np.clip(np.nan_to_num(adj), -5, 5)
    c_new[ks] += adj


def propose(prob, rng, c, t, cur, sigma):
    c2, t2 = c.copy(), t
    u = rng.random()
    if u < 0.1:
        t2 = float(np.clip(t + rng.normal(0, 0.05), TUBE_MIN, prob.tmax))
    elif u < 0.25 and cur["corner_idx"]:
        fi = cur["corner_idx"][rng.integers(len(cur["corner_idx"]))]
        _bump(c2, _nearest_knot(prob, fi), 1.5, rng.normal(0, 1.5 * sigma))
    else:
        dens = cur["density"]
        tot = float(dens.sum())
        if rng.random() < 0.5 and tot > 0:
            jc = _nearest_knot(prob, int(rng.choice(prob.n, p=dens / tot)))
        else:
            jc = int(rng.integers(prob.nfree))
        width = float(rng.choice([0.7, 1.0, 1.5, 2.5, 4.0, 8.0]))
        _bump(c2, jc, width, rng.normal(0, sigma))
        if u < 0.6:
            _slide(prob, rng, c, c2, jc, width)
    np.clip(c2, -prob.corridor, prob.corridor, out=c2)
    return c2, t2


def anneal(prob, rng, iters, k, t0, progress=None):
    """One chain from the input. Returns (best total, c, t)."""
    c = np.zeros(prob.nfree)
    t = t0
    cur = prob.score(c, t, k)
    if cur is None:
        raise ValueError("the input line leaves the terrain data or folds")
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
        c2, t2 = propose(prob, rng, c, t, cur, sigma)
        r = prob.score(c2, t2, k)
        if r is not None:
            delta = r["total"] - cur["total"]
            if delta < 0 or rng.random() < math.exp(-delta / T):
                if not prob.folds(c2):
                    c, t, cur = c2, t2, r
                    if cur["total"] < best[0]:
                        best = (cur["total"], c.copy(), t)
        if progress and i % 25 == 0:
            progress(i, best[0])
    return best


def _json_detail(prob, r, to_lonlat=None):
    """A scored candidate as plain lists, ready for JSON."""
    Q = r["Q"]
    rd = lambda a, nd: [round(float(v), nd) for v in a]
    legs = []
    for a, b in zip(prob.joints[:-1], prob.joints[1:]):
        legs.append(Q[a:b + 1].tolist())
    grade = r["grade"]
    return {
        "total": round(r["total"], 4),
        "costs": {k: round(v, 5) for k, v in r["costs"].items()},
        "contrib": {k: round(v, 5) for k, v in r["contrib"].items()},
        "legs_xy": legs,
        "t": round(r["t"], 3),
        "length_m": round(r["length_m"], 1),
        "profile": {"d": rd(r["d"], 1), "ground": rd(r["ground"], 2), "design": rd(r["design"], 2)},
        "corners": [{k: (round(v, 1) if isinstance(v, float) else v) for k, v in c.items()} for c in r["corners"]],
        "sites_d": [round(float(r["d"][i]), 1) for i in r["sites"]],
        "stats": {
            "grade_max": round(float(grade.max()), 1),
            "grade_p95": round(float(np.percentile(grade, 95)), 1),
            "cut_max": round(float(r["cut"].max()), 2),
            "cut_mean": round(float((r["cut"] * r["w"]).sum() / r["length_m"]), 3),
            "offset_max": round(float(np.abs(r["offset"]).max()), 1),
            "offset_mean": round(float((np.abs(r["offset"]) * r["w"]).sum() / r["length_m"]), 1),
            "corners": len(r["corners"]),
            "corner_min_r": round(min([c["radius_m"] for c in r["corners"]], default=0), 1) if r["corners"] else None,
            "reversals": len(r["sites"]),
        },
    }


def refine(legs_xy, grid, settings, progress=None, seed=None):
    """Refine a stretch made of legs (each an (m, 2) array in a metric CRS).
    `settings` is a full, cleaned settings dict (settings.clean_settings).
    `progress(fraction, best_total)` is called now and then.

    Returns {"input": detail, "candidates": [detail + key/label], "seed",
    "histograms", "factors"}: the input scored with the same tube as the
    candidates, so the comparison is fair."""
    p = settings["params"]
    prob = Problem(legs_xy, grid, settings)
    k = prob.base_k
    best_in = None
    for t in np.linspace(TUBE_MIN, prob.tmax, 8):
        r = prob.score(np.zeros(prob.nfree), float(t), k)
        if r is not None and (best_in is None or r["total"] < best_in[0]["total"]):
            best_in = (r, float(t))
    if best_in is None:
        raise ValueError("the input line leaves the terrain data, or turns too sharply to refine")
    t_in = best_in[1]
    inp = prob.score(np.zeros(prob.nfree), t_in, k, detail=True)
    prob.grade_ref = float(inp["grade"].max())
    if seed is None:
        seed = int(p.get("seed") or 0) or int(np.random.default_rng().integers(1, 2 ** 31 - 1))
    rng = np.random.default_rng(seed)
    iters, chains = int(p["iterations"]), int(p["chains"])
    results = []
    for ci in range(chains):
        key, label, mult = CHAIN_VARIANTS[ci % len(CHAIN_VARIANTS)]
        kk = {f: v * mult.get(f, 1.0) for f, v in k.items()}
        crng = np.random.default_rng(rng.integers(1, 2 ** 31 - 1))
        cb = None
        if progress:
            cb = lambda i, b, ci=ci: progress((ci * iters + i) / (chains * iters), b)
        total, c, t = anneal(prob, crng, iters, kk, t_in, cb)
        r = prob.score(c, t, k, detail=True)
        if r is not None:
            results.append((key, label, c, r))
    if progress:
        progress(1.0, min([r["total"] for *_, r in results], default=inp["total"]))

    def acceptable(r):
        if r["total"] >= inp["total"] - 1e-9:
            return False
        if k.get("grade", 0) > 0 and r["grade"].max() > prob.grade_ref + GRADE_MAX_SLACK:
            return False
        return all(r["contrib"][f] - inp["contrib"][f] <= MARGIN * inp["total"] for f in r["contrib"])

    picked = []
    order = sorted(results, key=lambda x: x[3]["total"])
    wanted = [order[0]] if order else []
    for key in ("closer", "steadier"):
        mine = [x for x in order if x[0] == key]
        if mine:
            wanted.append(mine[0])
    for i, (key, label, c, r) in enumerate(wanted):
        if i == 0:
            key, label = "best", "Best overall"
        if not acceptable(r):
            continue
        o = r["offset"]
        if any(np.abs(o - q[3]["offset"]).max() < 0.5 for q in picked):
            continue
        picked.append((key, label, c, r))
    cands = []
    for key, label, c, r in picked:
        j = _json_detail(prob, r)
        j.update({"key": key, "label": label})
        cands.append(j)
    return {
        "input": _json_detail(prob, inp),
        "candidates": cands,
        "seed": seed,
        "histograms": histograms(inp),
        "chains": [{"key": key, "label": label, "total": round(r["total"], 4)} for key, label, c, r in results],
    }
