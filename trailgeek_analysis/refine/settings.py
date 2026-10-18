"""The refine tool's settings: factors (a cost curve and a weight k each)
and run parameters, with their defaults, display metadata and validation.

A settings dict is {"factors": {name: {"k": float, "curve": [[x, y], ...]}},
"params": {name: number}}. Partial dicts merge over the defaults in order
(site, then user; later project, then user: docs/REFINE_TOOL.md §2), so
every stored layer has the same schema and may hold only what it changes.

Curves are piecewise with at most MAX_NODES nodes, x increasing,
y in CURVE_Y_MIN..CURVE_Y_MAX, flat beyond the first and last node and
log-linear between nodes (score.curve_fn), drawn on the panel's log y
axis (0.1 to 10 at first, zoomable). There is no 0 (owner, 2026-10-17): a
range that should cost next to nothing gets FREE (0.01), and a 0 in an
older saved curve reads as ZERO_AS, the same.

Weights are in metres (owner, 2026-10-18): the cost of a line is its
EQUIVALENT EFFORT, the length of easy trail it is as much work as. Length
along the slope (the climb counted) is the base and has no weight; a
per-metre factor's weight is the extra metres per metre of trail where its
curve is at 1, a per-corner factor's the extra metres per corner, a
per-turn factor's the extra metres per right angle turned. So 100 m with
every curve on its 0.01 floor is about 100 m plus 1 % of the per-metre
weights, and 100 m of steep fall-line tread with tight corners may be 500.
Only ratios matter to the search (the annealer's temperature comes from
the cost differences it sees), so this is a change of units: settings
saved before it (shares of 100, or the older grade 10 / corners 5 / ...,
with a "length" weight) are converted on reading by upgrade().
"""
import copy
import math

MAX_NODES = 9          # 8 segments
CURVE_Y_MIN = 1e-4
CURVE_Y_MAX = 1e4
FREE = 0.01            # the defaults' "next to nothing"
ZERO_AS = FREE         # a 0 in an older curve

# per: "metre" = the curve integrated along the line (extra metres per
# metre at curve 1); "corner" = summed per corner (extra metres per corner);
# "turn" = per right angle turned (extra metres per right angle).
VERSION = 2            # settings in metres; absent = older units (upgrade())
OLD_LENGTH_K = 8.1     # an older layer with no length weight of its own had this one
OLD_PER_M = 100.0      # older per-metre weights were per 100 m

FACTOR_META = {
    "grade": {"label": "Grade", "x": "design |grade| over 10 m, plus a quarter of the steepest 10 m's cost; keep it convex (see the note)", "unit": "%", "per": "metre",
              "xmin": 0, "xmax": 60, "snap": 0.5},
    "corner": {"label": "Corners", "x": "tightest radius of a turn across which the downhill side changes (signed TSA, the fall-line band read as no side); the floor is what every corner costs, so fewer corners win. Digging one in is charged as cut, fill and grade", "unit": "m",
               "per": "corner", "xmin": 0, "xmax": 30, "snap": 0.5},
    "tsa": {"label": "Trail-slope alignment", "x": "angle in plan between the trail and the fall line (TSA): 0 runs straight down it, 90 on the contour; the shaded band is the fall-line band (drag its edge), where the downhill side is undefined", "unit": "°", "per": "metre",
            "xmin": 0, "xmax": 90, "snap": 1, "band": "fall_tsa_deg"},
    "falltread": {"label": "Fall-line tread", "x": "design grade where the trail runs in the fall-line band (water cannot leave sideways)", "unit": "%", "per": "metre",
                "xmin": 0, "xmax": 40, "snap": 0.5},
    "bend": {"label": "Bends", "x": "radius of any other bend, charged per right angle turned at it", "unit": "m", "per": "turn",
             "xmin": 0, "xmax": 40, "snap": 0.5},
    "slope": {"label": "Terrain slope", "x": "side slope at bench scale", "unit": "%", "per": "metre",
              "xmin": 0, "xmax": 150, "snap": 1},
"wet": {"label": "Wet ground", "x": "topographic wetness index ln(a / tan b) on a 5 m grid: higher is wetter (mud); light, no culverts", "unit": "TWI", "per": "metre",
            "xmin": 0, "xmax": 20, "snap": 0.5},
    "cutfill": {"label": "Cut and fill", "x": "design − ground: cut below 0, fill above. Cut effort grows about as depth squared; fill is loose until compacted and soon useless on a slope", "unit": "m", "per": "metre",
                "xmin": -2, "xmax": 0.6, "snap": 0.01},
    "offset": {"label": "Distance from your line", "x": "beyond its uncertainty, after the room a sketched corner needs to round", "unit": "m", "per": "metre",
               "xmin": 0, "xmax": 25, "snap": 0.5},
    "drainage": {"label": "Drainage (stub)", "x": "distance since the last grade reversal", "unit": "m",
                 "per": "metre", "xmin": 0, "xmax": 300, "snap": 5, "stub": True},
}
FACTOR_ORDER = list(FACTOR_META)

DEFAULT_SETTINGS = {
    "version": VERSION,
    "factors": {
        # Anchors are joined log-linearly (2026-10-17), so a rise off the
        # 0.01 floor has an anchor midway at the value the earlier, linear
        # curve had there; otherwise it would hug the floor most of the way.
        # CONVEX up to 60 %. Climbing a height costs (curve / grade) per metre
        # of climb, so where the curve flattens, steeper-and-shorter gets
        # cheaper and the search heads up the fall line, dropping switchbacks
        # (it did, on Upper ascent A). Keep y / x rising up to the steepest
        # grade you would accept.
        # Weights are the shares of 2026-10-17 over length's 8.1, in metres.
        "grade": {"k": 3.3, "curve": [[0, 0.01], [8, 0.01], [12, 0.05], [18, 0.15], [25, 0.3], [35, 0.5], [45, 0.75], [60, 1]]},
        # Never 0: every corner costs at least 0.2 (33 m), so of two otherwise
        # equal lines the one with fewer corners wins (owner, 2026-10-17).
        "corner": {"k": 167, "curve": [[0, 1], [3, 1], [6, 0.5], [10, 0.3], [15, 0.2]]},
        # A first guess (2026-10-18): the fall-line band (0 to 22 degrees,
        # Marion's convention) is as bad as it gets for shedding water; on
        # the contour, nothing. Light, because the fall-line tread factor
        # already charges grade in the band.
        "tsa": {"k": 0.5, "curve": [[0, 1], [22, 1], [30, 0.3], [45, 0.05], [60, 0.01], [90, 0.01]]},
        # U-shaped (owner, 2026-10-17): a fall-line run at 5-8 % sheds water
        # along the tread and holds up; flatter pools, steeper erodes. The
        # steep side is gentle because the grade factor already charges it.
        "falltread": {"k": 1.33, "curve": [[0, 1], [3, 1], [4.5, 0.25], [5, 0.01], [8, 0.01], [10, 0.1], [15, 0.3], [30, 0.6]]},
        "bend": {"k": 33, "curve": [[0, 1], [5, 1], [10, 0.3], [15, 0.15], [20, 0.01]]},
        "slope": {"k": 1.0, "curve": [[0, 0.01], [60, 0.01], [70, 0.15], [80, 0.3], [100, 1]]},
        # A first guess (2026-10-17): open hillside reads ~4-8, hollows and
        # benches ~9-11, channels and filled basins 12 and up.
        "wet": {"k": 1.0, "curve": [[0, 0.01], [8, 0.01], [9, 0.15], [10, 0.3], [12, 0.7], [14, 1]]},
        # Signed (owner, 2026-10-18): cut reaches compacted ground, a sound
        # tread, and its effort grows about as depth squared (2 d^2 here:
        # 0.5 m is 0.5) as the walls lay back; fill is loose until it is
        # compacted, so past ~20 cm it climbs steeply (0.3 m is 1, 0.4 m 5).
        "cutfill": {"k": 1.67, "curve": [[-2, 8], [-1, 2], [-0.5, 0.5], [-0.25, 0.125], [-0.1, 0.02], [0, 0.01],
                                         [0.1, 0.05], [0.2, 0.2], [0.4, 5]]},
        # x is distance beyond the line's uncertainty (3 m by default), so with
        # the defaults: free to ~5 m off a straight stretch, 0.2 at ~10 m, 1 at ~20 m.
        "offset": {"k": 0.67, "curve": [[0, 0.01], [2, 0.01], [4.5, 0.1], [7, 0.2], [17, 1]]},
        "drainage": {"k": 0.33, "curve": [[0, 0.01], [60, 0.01], [90, 0.25], [120, 0.5], [200, 1]]},
    },
    "params": {
        "corridor_m": 25, "cutfill_max_m": 0.75, "corner_cut_max_m": 2.0, "bench_m": 2.5, "spacing_m": 2.0, "knot_m": 8,
        "input_sigma_m": 3.0, "sketch_radius_m": 10.0, "fall_tsa_deg": 22, "corner_min_turn_deg": 60,
        "grade_peak_m": 25, "iterations": 1500, "chains": 4, "min_outslope_pct": 10, "reversal_dip_m": 0.3,
        "seed": 0,
    },
}

# name: (label, min, max, integer?)
PARAM_META = {
    "input_sigma_m": ("How far your line may be from what you mean, m (sketch 3-5, GPS or existing trail 0.5)", 0, 15, False),
    "sketch_radius_m": ("Radius a sketched corner implies, m", 2, 40, False),
    "fall_tsa_deg": ("Fall-line band: TSA below this has no downhill side (corners) and counts as fall-line tread, degrees (also the band on the TSA curve)", 5, 60, False),
    "corner_min_turn_deg": ("A side change turning less than this is a crossing (of a crest or a gully), not a corner, degrees", 15, 150, False),
    "corridor_m": ("Corridor: most distance beyond the uncertainty and corner room, m", 2, 40, False),
    "cutfill_max_m": ("Most cut along the line, m (fill goes as far as costs the same on the cut and fill curve)", 0.05, 1.5, False),
    "corner_cut_max_m": ("Most cut where a corner is dug in, m", 0.05, 4, False),
    "grade_peak_m": ("The steepest 10 m is charged again as this many metres of its grade", 0, 200, False),
    "bench_m": ("Bench width for side slope, m", 1, 6, False),
    "spacing_m": ("Sample spacing, m", 1, 5, False),
    "knot_m": ("Control spacing on straight stretches, m", 3, 40, False),
    "iterations": ("Iterations per chain", 50, 20000, True),
    "chains": ("Chains", 1, 8, True),
    "min_outslope_pct": ("Least side slope that sets the downhill side, %", 2, 50, False),
    "reversal_dip_m": ("Ground dip that counts as a reversal site, m", 0.02, 1, False),
    "seed": ("Random seed (0 = new each run)", 0, 2 ** 31 - 1, True),
}


def upgrade(layer):
    """A settings layer in metres. One saved before VERSION 2 had a "length"
    weight (the panel always saved one; a layer without one is read as
    metres) and per-metre weights per 100 m of it: a per-metre weight
    becomes k / length k, a per-corner or per-turn one k / length k * 100.
    Its cut and fill curve was |design - ground|: mirrored to the cut side
    if that fits in MAX_NODES, else dropped (the default is used)."""
    if not layer or layer.get("version") == VERSION or "length" not in (layer.get("factors") or {}):
        return layer                     # every older layer the panel saved has a length weight
    out = copy.deepcopy(layer)
    out["version"] = VERSION
    fac = out.get("factors") or {}
    old = fac.pop("length", None)
    lk = old.get("k") if isinstance(old, dict) else None
    try:
        lk = float(lk) if lk is not None else OLD_LENGTH_K
    except (TypeError, ValueError):
        lk = OLD_LENGTH_K
    for name, f in fac.items():
        if not isinstance(f, dict):
            continue
        if "k" in f:
            try:
                k = float(f["k"])
            except (TypeError, ValueError):
                continue
            per = FACTOR_META.get(name, {}).get("per")
            f["k"] = 0.0 if lk <= 0 else round(k / lk * (1 if per == "metre" else OLD_PER_M), 4)
        if name == "cutfill" and isinstance(f.get("curve"), list):
            c = f["curve"]
            try:
                fill = [[float(x), y] for x, y in c if float(x) >= 0]
                cut = [[-x, y] for x, y in reversed(fill) if x > 0]
                f["curve"] = cut + fill
                if not 2 <= len(f["curve"]) <= MAX_NODES:
                    raise ValueError
            except (TypeError, ValueError):
                f.pop("curve")
    params = out.get("params") or {}
    if "fall_tsa_deg" in params and params.get("fall_tsa_deg") == 30:
        params.pop("fall_tsa_deg")       # the old default; the band is 22 now
    return out


def merge_settings(*layers):
    """Merge partial settings dicts over the defaults, left to right."""
    out = copy.deepcopy(DEFAULT_SETTINGS)
    for layer in layers:
        if not layer:
            continue
        layer = upgrade(layer)
        for name, f in (layer.get("factors") or {}).items():
            if name in out["factors"] and isinstance(f, dict):
                out["factors"][name].update({k: v for k, v in f.items() if k in ("k", "curve")})
        for name, v in (layer.get("params") or {}).items():
            if name in out["params"]:
                out["params"][name] = v
    return out


def _num(v, what):
    try:
        f = float(v)
    except (TypeError, ValueError):
        raise ValueError(f"{what} must be a number")
    if not math.isfinite(f):
        raise ValueError(f"{what} must be a number")
    return f


def clean_curve(curve, what):
    if not isinstance(curve, list) or not 2 <= len(curve) <= MAX_NODES:
        raise ValueError(f"{what}: a curve needs 2 to {MAX_NODES} nodes")
    out = []
    for node in curve:
        if not isinstance(node, (list, tuple)) or len(node) != 2:
            raise ValueError(f"{what}: each node is [x, y]")
        x, y = _num(node[0], what), _num(node[1], what)
        if y == 0:
            y = ZERO_AS                  # an older curve's "free"
        if not CURVE_Y_MIN <= y <= CURVE_Y_MAX:
            raise ValueError(f"{what}: y must be between {CURVE_Y_MIN:g} and {CURVE_Y_MAX:g}")
        if out and x < out[-1][0]:
            raise ValueError(f"{what}: x must not decrease")
        out.append([x, y])
    return out


def clean_settings(raw, partial=False):
    """Validate a settings dict. Returns a cleaned copy: the full settings
    (merged over the defaults), or with partial=True only what was given,
    for storing as a defaults layer. Raises ValueError with a message."""
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ValueError("settings must be an object")
    raw = upgrade(raw) if raw else raw
    out = {"version": VERSION, "factors": {}, "params": {}}
    for name, f in (raw.get("factors") or {}).items():
        if name not in FACTOR_META:
            raise ValueError(f"unknown factor {name!r}")
        if not isinstance(f, dict):
            raise ValueError(f"{name}: expected an object")
        g = {}
        if "k" in f:
            k = _num(f["k"], f"{name} weight")
            if not 0 <= k <= 10000:
                raise ValueError(f"{name} weight must be between 0 and 10000 m")
            g["k"] = k
        if "curve" in f:
            g["curve"] = clean_curve(f["curve"], FACTOR_META[name]["label"])
        out["factors"][name] = g
    for name, v in (raw.get("params") or {}).items():
        if name not in PARAM_META:
            raise ValueError(f"unknown parameter {name!r}")
        label, lo, hi, integer = PARAM_META[name]
        x = _num(v, label)
        if not lo <= x <= hi:
            raise ValueError(f"{label} must be between {lo} and {hi}")
        out["params"][name] = int(round(x)) if integer else x
    return out if partial else merge_settings(out)
