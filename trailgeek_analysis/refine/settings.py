"""The refine tool's settings: factors (a cost curve and a weight k each)
and run parameters, with their defaults, display metadata and validation.

A settings dict is {"factors": {name: {"k": float, "curve": [[x, y], ...]}},
"params": {name: number}}. Partial dicts merge over the defaults in order
(site, then user; later project, then user: docs/REFINE_TOOL.md §2), so
every stored layer has the same schema and may hold only what it changes.

Curves are piecewise linear with at most MAX_NODES nodes, x increasing,
y in CURVE_Y_MIN..CURVE_Y_MAX, flat beyond the first and last node and
log-linear between nodes (score.curve_fn), drawn on the panel's log y
axis (0.1 to 10 at first, zoomable). There is no 0 (owner, 2026-10-17): a
range that should cost next to nothing gets FREE (0.01), and a 0 in an
older saved curve reads as ZERO_AS, the same.

Weights k are shares: the panel keeps them adding up to 100 (owner,
2026-10-17). Only their ratios matter to the search (the annealer's
temperature comes from the cost differences it sees, and its guards scale
with k), so settings saved with the older weights (grade 10, corners 5, ...)
score the same; the panel shows them as shares.
"""
import copy
import math

MAX_NODES = 9          # 8 segments
CURVE_Y_MIN = 1e-4
CURVE_Y_MAX = 1e4
FREE = 0.01            # the defaults' "next to nothing"
ZERO_AS = FREE         # a 0 in an older curve

# per: "metre" = the curve integrated along the line, per 100 m; "corner" =
# summed per corner; "turn" = per right angle turned. One scale, so k means
# the same on any route. A "flat" factor has no curve worth shaping: length
# is k per 100 m of line, every metre alike.
#
# Default weights are the earlier ones (grade 10, corners 5, fall-line tread
# 4, cut and fill 5, slope, wet ground and length 3, distance 2, bends and
# drainage 1) as shares of 100.

FACTOR_META = {
    "grade": {"label": "Grade", "x": "design |grade| over 10 m, plus a quarter of the steepest 10 m's cost; keep it convex (see the note)", "unit": "%", "per": "metre",
              "xmin": 0, "xmax": 60, "snap": 0.5},
    "corner": {"label": "Corners", "x": "tightest radius of a turn in which the downhill side changes; the curve's floor is what every corner costs, so fewer corners win", "unit": "m",
               "per": "corner", "xmin": 0, "xmax": 30, "snap": 0.5},
    "falltread": {"label": "Fall-line tread", "x": "design grade where the trail runs near the fall line (water cannot leave sideways)", "unit": "%", "per": "metre",
                "xmin": 0, "xmax": 40, "snap": 0.5},
    "bend": {"label": "Bends", "x": "radius of any other bend, charged per right angle turned at it", "unit": "m", "per": "turn",
             "xmin": 0, "xmax": 40, "snap": 0.5},
    "slope": {"label": "Terrain slope", "x": "side slope at bench scale", "unit": "%", "per": "metre",
              "xmin": 0, "xmax": 150, "snap": 1},
"wet": {"label": "Wet ground", "x": "topographic wetness index ln(a / tan b) on a 5 m grid: higher is wetter (mud); light, no culverts", "unit": "TWI", "per": "metre",
            "xmin": 0, "xmax": 20, "snap": 0.5},
    "cutfill": {"label": "Cut and fill", "x": "|design − ground|", "unit": "m", "per": "metre",
                "xmin": 0, "xmax": 0.75, "snap": 0.01},
    "offset": {"label": "Distance from your line", "x": "beyond its uncertainty, after the room a sketched corner needs to round", "unit": "m", "per": "metre",
               "xmin": 0, "xmax": 25, "snap": 0.5},
    "length": {"label": "Length", "x": "every metre the same; charged on the difference from your line, so as drawn it is 0", "unit": "m", "per": "metre",
               "xmin": 0, "xmax": 1, "snap": 1, "flat": True},
    "drainage": {"label": "Drainage (stub)", "x": "distance since the last grade reversal", "unit": "m",
                 "per": "metre", "xmin": 0, "xmax": 300, "snap": 5, "stub": True},
}
FACTOR_ORDER = list(FACTOR_META)

DEFAULT_SETTINGS = {
    "factors": {
        # Anchors are joined log-linearly (2026-10-17), so a rise off the
        # 0.01 floor has an anchor midway at the value the earlier, linear
        # curve had there; otherwise it would hug the floor most of the way.
        # CONVEX up to 60 %. Climbing a height costs (curve / grade) per metre
        # of climb, so where the curve flattens, steeper-and-shorter gets
        # cheaper and the search heads up the fall line, dropping switchbacks
        # (it did, on Upper ascent A). Keep y / x rising up to the steepest
        # grade you would accept.
        "grade": {"k": 27.1, "curve": [[0, 0.01], [8, 0.01], [12, 0.05], [18, 0.15], [25, 0.3], [35, 0.5], [45, 0.75], [60, 1]]},
        # Never 0: every corner costs at least 0.2, so of two otherwise equal
        # lines the one with fewer corners wins (owner, 2026-10-17).
        "corner": {"k": 13.5, "curve": [[0, 1], [3, 1], [6, 0.5], [10, 0.3], [15, 0.2]]},
        # U-shaped (owner, 2026-10-17): a fall-line run at 5-8 % sheds water
        # along the tread and holds up; flatter pools, steeper erodes. The
        # steep side is gentle because the grade factor already charges it.
        "falltread": {"k": 10.8, "curve": [[0, 1], [3, 1], [4.5, 0.25], [5, 0.01], [8, 0.01], [10, 0.1], [15, 0.3], [30, 0.6]]},
        "bend": {"k": 2.7, "curve": [[0, 1], [5, 1], [10, 0.3], [15, 0.15], [20, 0.01]]},
        "slope": {"k": 8.1, "curve": [[0, 0.01], [60, 0.01], [70, 0.15], [80, 0.3], [100, 1]]},
        # A first guess (2026-10-17): open hillside reads ~4-8, hollows and
        # benches ~9-11, channels and filled basins 12 and up.
        "wet": {"k": 8.1, "curve": [[0, 0.01], [8, 0.01], [9, 0.15], [10, 0.3], [12, 0.7], [14, 1]]},
        "cutfill": {"k": 13.5, "curve": [[0, 0.01], [0.1, 0.01], [0.2, 0.15], [0.3, 0.3], [0.5, 1]]},
        # x is distance beyond the line's uncertainty (3 m by default), so with
        # the defaults: free to ~5 m off a straight stretch, 0.2 at ~10 m, 1 at ~20 m.
        "offset": {"k": 5.4, "curve": [[0, 0.01], [2, 0.01], [4.5, 0.1], [7, 0.2], [17, 1]]},
        # Per metre, a plain multiple of length (owner, 2026-10-17; was a
        # curve of length / input length): 100 m more costs as much as 30 m
        # at full grade cost. A weight of 5 against grade's 10 rewarded
        # cutting corners past the offset guard. The curve is kept for the schema, not used.
        "length": {"k": 8.1, "curve": [[0, 1], [1, 1]]},
        "drainage": {"k": 2.7, "curve": [[0, 0.01], [60, 0.01], [90, 0.25], [120, 0.5], [200, 1]]},
    },
    "params": {
        "corridor_m": 25, "cutfill_max_m": 0.75, "bench_m": 2.5, "spacing_m": 2.0, "knot_m": 8,
        "input_sigma_m": 3.0, "sketch_radius_m": 10.0, "fall_tsa_deg": 30,
        "iterations": 1500, "chains": 4, "min_outslope_pct": 10, "reversal_dip_m": 0.3,
        "seed": 0,
    },
}

# name: (label, min, max, integer?)
PARAM_META = {
    "input_sigma_m": ("How far your line may be from what you mean, m (sketch 3-5, GPS or existing trail 0.5)", 0, 15, False),
    "sketch_radius_m": ("Radius a sketched corner implies, m", 2, 40, False),
    "fall_tsa_deg": ("Within this angle of the fall line the tread counts as fall-line, degrees", 5, 60, False),
    "corridor_m": ("Corridor: most distance beyond the uncertainty and corner room, m", 2, 40, False),
    "cutfill_max_m": ("Most cut or fill allowed, m", 0.05, 1.5, False),
    "bench_m": ("Bench width for side slope, m", 1, 6, False),
    "spacing_m": ("Sample spacing, m", 1, 5, False),
    "knot_m": ("Control spacing on straight stretches, m", 3, 40, False),
    "iterations": ("Iterations per chain", 50, 20000, True),
    "chains": ("Chains", 1, 8, True),
    "min_outslope_pct": ("Least side slope that sets the downhill side, %", 2, 50, False),
    "reversal_dip_m": ("Ground dip that counts as a reversal site, m", 0.02, 1, False),
    "seed": ("Random seed (0 = new each run)", 0, 2 ** 31 - 1, True),
}


def merge_settings(*layers):
    """Merge partial settings dicts over the defaults, left to right."""
    out = copy.deepcopy(DEFAULT_SETTINGS)
    for layer in layers:
        if not layer:
            continue
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
    out = {"factors": {}, "params": {}}
    for name, f in (raw.get("factors") or {}).items():
        if name not in FACTOR_META:
            raise ValueError(f"unknown factor {name!r}")
        if not isinstance(f, dict):
            raise ValueError(f"{name}: expected an object")
        g = {}
        if "k" in f:
            k = _num(f["k"], f"{name} weight")
            if not 0 <= k <= 1000:
                raise ValueError(f"{name} weight must be between 0 and 1000")
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
