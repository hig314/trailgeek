"""Precompute the methods page's sandbox (owner, 2026-10-19/20): the refine
engine's line at every setting of the varied weights, written as static JSON
the page reads, so moving a slider swaps lines without a round trip.

    manage.py build_methods analytic [--surface dome] [--jobs 8] [--quick]

`analytic`: the surfaces of trailgeek_analysis/sandbox.py, the formula ones
and the real patch (the Graduation Peak gully, fetched from the lidar into
.methods_cache/ on first use; no formula there, so no stage 4). In four
stages (the module's docstring has the reasoning):
  1. the grid's settings, run thoroughly (SEEDS, THOROUGH chains/iterations);
  2. each polished from each neighbour's line (a better route found next
     door is not missed);
  3. each edge of the grid traced in STEPS steps from both ends;
  4. the formula's line at every node and step.
Writes core/static/core/methods/<kind>-<surface>.json (analytic-cone,
terrain-gully, ...). --quick: short
runs and one seed, to try the pipeline (the file says so, and the page).
--formula-only: redo stage 4 in an existing file, keeping its engine lines
(after a change to the formula alone; a change to the scoring needs a full
build). --recombine: stage 4 and the file from the runs cached in
.methods_cache/ (after a change to how they are combined).

Run it with numpy on one thread per worker, or the workers fight over the
cores (2026-10-20: 1155 % CPU and an hour for the cone's tracings):
    docker compose exec -e OMP_NUM_THREADS=1 -e OPENBLAS_NUM_THREADS=1 web python manage.py build_methods analytic
"""
import json
import pickle
import time
from concurrent.futures import ProcessPoolExecutor

from django.conf import settings
from django.core.management.base import BaseCommand

from trailgeek_analysis import sandbox as sb

from pathlib import Path

OUT = Path(settings.BASE_DIR) / "core" / "static" / "core" / "methods"
CACHE = Path(settings.BASE_DIR) / ".methods_cache"            # the runs, for --recombine; not committed
NI, NJ = len(sb.VARY[0][1]), len(sb.VARY[1][1])
NODES = [(i, j) for i in range(NI) for j in range(NJ)]
# An edge is (axis, i, j): from node (i, j) to the next along axis (0 grade, 1 corners).
RETRACE_ROUNDS = 3
sb.Terrain.path = str(CACHE / "terrain-gully.npz")            # set here, so the workers read it too
EDGES = [(0, i, j) for i in range(NI - 1) for j in range(NJ)] + [(1, i, j) for i in range(NI) for j in range(NJ - 1)]
_grids = {}


def _grid(name):
    if name not in _grids:
        _grids[name] = sb.SURFACES[name].grid()
    return _grids[name]


def _quick(on):
    if on:
        sb.THOROUGH.update(chains=2, iterations=600)
        sb.TRACE_ITERS = 400
        sb.POLISH_ITERS = 400


def _thorough(a):
    name, i, j, seed, quick = a
    _quick(quick)
    return (i, j), sb.thorough(sb.SURFACES[name], sb.weights_at(i, j), seed, grid=_grid(name))


def _polish(a):
    name, i, j, line, quick = a
    _quick(quick)
    r = sb.trace_step(sb.SURFACES[name], sb.weights_at(i, j), line, w=sb.POLISH_W, lean=0,
                      iters=sb.POLISH_ITERS, seed=i * 31 + j, grid=_grid(name))
    return (i, j), r


def _trace(a):
    """Trace the edge from node `src` toward node `dst` along `axis`: the
    lines at t = 1/STEPS ... 1 of the way (t measured from the edge's lower
    node, so a backward tracing runs t = 1 - 1/STEPS ... 0)."""
    name, axis, i, j, forward, src, dst, quick = a
    _quick(quick)
    S, out, cur = sb.SURFACES[name], [], src
    for n in range(1, sb.STEPS + 1):
        t = n / sb.STEPS if forward else 1 - n / sb.STEPS
        nxt = sb.trace_step(S, sb.weights_at(i, j, t, axis), cur, toward=dst, seed=1000 * axis + 100 * i + 10 * j + n,
                            grid=_grid(name))
        out.append((round(t, 6), nxt))
        cur = nxt or cur
    return (axis, i, j, forward), out


def _formula(a):
    name, key, w = a
    return key, sb.formula_line(sb.SURFACES[name], w, grid=_grid(name))


def _strip(line):
    if line is None:
        return None
    return {k: v for k, v in line.items() if not k.startswith("_")}


class Command(BaseCommand):
    help = "Precompute the methods page's sandbox as static JSON."

    def add_arguments(self, parser):
        parser.add_argument("which", choices=["analytic"])
        parser.add_argument("--surface", action="append", choices=list(sb.SURFACES))
        parser.add_argument("--jobs", type=int, default=8)
        parser.add_argument("--quick", action="store_true")
        parser.add_argument("--formula-only", action="store_true")
        parser.add_argument("--recombine", action="store_true", help="stage 4 and the file from the cached runs")

    def say(self, msg):
        self.stdout.write(f"[{time.time() - self.t0:6.0f} s] {msg}")
        self.stdout.flush()

    def handle(self, which, surface=None, jobs=8, quick=False, formula_only=False, recombine=False, **opts):
        self.t0 = time.time()
        OUT.mkdir(parents=True, exist_ok=True)
        for name in surface or list(sb.SURFACES):
            if isinstance(sb.SURFACES[name], sb.Terrain):
                self.fetch_terrain(sb.SURFACES[name])        # before the workers start
        with ProcessPoolExecutor(max_workers=max(1, jobs)) as pool:
            for name in surface or list(sb.SURFACES):
                if formula_only:
                    self.formula_only(pool, name)
                    continue
                if not recombine:
                    self.build(pool, name, quick)
                self.combine(pool, name)

    def fetch_terrain(self, S):
        """The real patch's ground at 1 m (lidar first, as the refine panel
        reads it), in local metres about its centre, cached in CACHE."""
        path = Path(S.path)
        if path.exists():
            return
        import numpy as np
        from django.contrib.gis.geos import Point

        from core import geo
        from core.dem import transform_xy
        from core.evaluation import choose_sources
        from core.refine import build_grid

        lon, lat = S.CENTRE_LONLAT
        srid = geo.utm_srid(lon, lat)
        x, y = transform_xy(np.array([lon]), np.array([lat]), 4326, srid)
        cx, cy = round(float(x[0])), round(float(y[0]))
        g, info = build_grid([np.array([[cx - 1.0, cy], [cx + 1.0, cy]])], srid,
                             choose_sources(Point(lon, lat, srid=4326)), S.extent, res=1.0, margin_m=0.0)
        if np.isnan(g.z).any():
            raise ValueError(f"{S.name}: {np.isnan(g.z).mean():.1%} of the patch has no ground")
        CACHE.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(path, z=g.z.astype(np.float32), x0=g.x0 - cx, y0=g.y0 - cy, res=g.res)
        self.say(f"{S.name}: fetched {g.z.shape[1]} x {g.z.shape[0]} m, {info['lidar_pct']} % lidar "
                 f"({', '.join(s['title'] for s in info['sources'])})")

    def formula_only(self, pool, name):
        if not sb.SURFACES[name].has_formula:
            return
        path = OUT / f"{sb.SURFACES[name].kind}-{name}.json"
        doc = json.loads(path.read_text())
        tasks = [(name, ("n", key), n["weights"]) for key, n in doc["nodes"].items()]
        for key, rail in doc["rails"].items():
            for k, s_ in enumerate(rail["samples"]):
                tasks.append((name, ("r", key, k), s_["weights"]))
        for (kind, *where), line in pool.map(_formula, tasks, chunksize=4):
            if kind == "n":
                doc["nodes"][where[0]]["formula"] = _strip(line)
            else:
                doc["rails"][where[0]]["samples"][where[1]]["formula"] = _strip(line)
        path.write_text(json.dumps(doc, separators=(",", ":")))
        self.say(self.style.SUCCESS(f"{name}: {len(tasks)} formula lines redone in {path.relative_to(settings.BASE_DIR)}"))

    def build(self, pool, name, quick):
        """Stages 1-3, the engine's runs, cached to CACHE (so the way they are
        combined can change without running the engine again)."""
        seeds = sb.SEEDS[:1] if quick else sb.SEEDS

        # 1. Thorough runs at the grid's settings.
        node = {}
        for (ij, line) in pool.map(_thorough, [(name, i, j, s, quick) for i, j in NODES for s in seeds]):
            if ij not in node or line["total"] < node[ij]["total"]:
                node[ij] = line
        self.say(f"{name}: {len(NODES)} settings run thoroughly")

        # 2. Polish each from each neighbour's line.
        tasks = []
        for i, j in NODES:
            for di, dj in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nb = (i + di, j + dj)
                if nb in node:
                    tasks.append((name, i, j, node[nb], quick))
        better = 0
        for ij, line in pool.map(_polish, tasks):
            if line is not None and line["total"] < node[ij]["total"] - 0.05:
                line["input"] = node[ij].get("input")
                node[ij] = line
                better += 1
        self.say(f"{name}: polished from the neighbours, {better} improved")

        # 3. Trace every edge from both ends. A tracing that arrives at the
        # far node more cheaply than its own line replaces it (it found a
        # better route there); then the edges at that node are traced again
        # from its new line, or they would start from a line it no longer
        # shows (a jump at the node). Until none is replaced, at most
        # RETRACE_ROUNDS times.
        def ends(axis, i, j):
            return node[(i, j)], node[(i + 1, j) if axis == 0 else (i, j + 1)]

        def far_of(axis, i, j, fwd):
            return ((i + 1, j) if axis == 0 else (i, j + 1)) if fwd else (i, j)

        traced, todo = {}, list(EDGES)
        for round_ in range(1 + RETRACE_ROUNDS):
            tasks = []
            for axis, i, j in todo:
                a, b = ends(axis, i, j)
                tasks += [(name, axis, i, j, True, a, b, quick), (name, axis, i, j, False, b, a, quick)]
            traced.update(dict(pool.map(_trace, tasks)))
            changed = set()
            for axis, i, j in todo:
                for fwd in (True, False):
                    t, line = traced[(axis, i, j, fwd)][-1]
                    far = far_of(axis, i, j, fwd)
                    if line is not None and line["total"] < node[far]["total"] - 0.05:
                        line["input"] = node[far].get("input")
                        node[far] = line
                        changed.add(far)
            self.say(f"{name}: traced {len(todo)} edges, {len(changed)} nodes reached more cheaply")
            if not changed:
                break
            todo = [e for e in EDGES if e[1:] in changed or far_of(*e, True) in changed]

        CACHE.mkdir(parents=True, exist_ok=True)
        with open(CACHE / f"{name}.pkl", "wb") as fh:
            pickle.dump({"node": node, "traced": traced, "quick": quick}, fh)
        self.say(f"{name}: runs cached")

    def combine(self, pool, name):
        """Stage 4 and the page's file, from the cached runs."""
        with open(CACHE / f"{name}.pkl", "rb") as fh:
            cached = pickle.load(fh)
        node, traced, quick = cached["node"], cached["traced"], cached["quick"]
        S = sb.SURFACES[name]

        def ends(axis, i, j):
            return node[(i, j)], node[(i + 1, j) if axis == 0 else (i, j + 1)]

        # 4. The formula's line at every node and step (none on real ground).
        formula = {}
        if S.has_formula:
            ftasks = [(name, ("n", i, j), sb.weights_at(i, j)) for i, j in NODES]
            for axis, i, j in EDGES:
                for n in range(1, sb.STEPS):
                    ftasks.append((name, ("r", axis, i, j, n), sb.weights_at(i, j, n / sb.STEPS, axis)))
            formula = dict(pool.map(_formula, ftasks, chunksize=4))
            self.say(f"{name}: {len(ftasks)} formula lines")

        # Rails (owner, 2026-10-20): the tracing from the lower end up to one
        # switch, the tracing from the upper end after it, the switch where the
        # total along the edge is least. (The cheaper of the two at each step
        # flickered between two routes costing within the searches' noise of
        # each other, up to six jumps on an edge.) A jump wherever consecutive
        # lines are more than JUMP_M apart.
        rails, jumps = {}, 0
        for axis, i, j in EDGES:
            a, b = ends(axis, i, j)
            fw = {round(t, 6): L for t, L in traced[(axis, i, j, True)]}
            bw = {round(t, 6): L for t, L in traced[(axis, i, j, False)]}
            ts = [round(n / sb.STEPS, 6) for n in range(1, sb.STEPS)]
            big = float("inf")
            cost = lambda L: L["total"] if L is not None else big
            # switch k: steps before it from the low tracing, from it on the high one
            k_best = min(range(len(ts) + 1),
                         key=lambda k: (sum(cost(fw.get(t)) for t in ts[:k]) + sum(cost(bw.get(t)) for t in ts[k:]),
                                        abs(k - len(ts) / 2)))
            seq, samples = [a], []
            for n, t in enumerate(ts, 1):
                side = "low" if n - 1 < k_best else "high"
                L, other = (fw.get(t), bw.get(t)) if side == "low" else (bw.get(t), fw.get(t))
                if L is None:
                    L, other, side = other, None, ("high" if side == "low" else "low")
                if L is None:
                    continue
                samples.append({"t": t, "weights": sb.weights_at(i, j, t, axis), "from": side,
                                "other_total": other["total"] if other is not None else None,
                                "engine": L, "formula": _strip(formula.get(("r", axis, i, j, n)))})
                seq.append(L)
            seq.append(b)
            for k, s_ in enumerate(samples):
                s_["jump"] = sb.apart(seq[k], seq[k + 1]) > sb.JUMP_M
            end_jump = sb.apart(seq[-2], seq[-1]) > sb.JUMP_M
            jumps += sum(s_["jump"] for s_ in samples) + end_jump
            for s_ in samples:
                s_["engine"] = _strip(s_["engine"])
            rails[f"{axis}_{i}_{j}"] = {"samples": samples, "end_jump": end_jump}

        st0 = sb.settings_at(sb.weights_at(0, 0))
        doc = {
            "surface": {"name": name, "kind": S.kind, "formula": S.has_formula,
                        "label": S.label, "blurb": S.blurb, "corridor_m": S.corridor,
                        "line": S.line().tolist(), "max_slope_pct": round(100 * sb.max_slope(S), 1)},
            "terrain": sb.terrain_json(S),
            "factors": sb.factors_json(st0),
            "params": {k: st0["params"][k] for k in ("fall_tsa_deg", "grade_peak_m", "cutfill_max_m", "corner_cut_max_m")},
            "axes": [{"key": key, "values": list(vals)} for key, vals in sb.VARY],
            "steps": sb.STEPS,
            "nodes": {f"{i}_{j}": {"weights": sb.weights_at(i, j), "engine": _strip(node[(i, j)]),
                                   "input": node[(i, j)].get("input"), "formula": _strip(formula.get(("n", i, j)))}
                      for i, j in NODES},
            "rails": rails,
            "quick": quick,
            "built": time.strftime("%Y-%m-%d"),
        }
        path = OUT / f"{S.kind}-{name}.json"
        path.write_text(json.dumps(doc, separators=(",", ":")))
        self.say(self.style.SUCCESS(f"{name}: {jumps} jumps; wrote {path.relative_to(settings.BASE_DIR)} "
                                    f"({path.stat().st_size // 1024} kB)"))
