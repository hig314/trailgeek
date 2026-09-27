"""The evaluator: from sampled terrain along an alignment to per-sample
grade / slope / TSA, running averages, and per-leg and whole-route
statistics including the construction / maintenance effort rubric.

Inputs are arrays over the resampled points (see resample.py):
    d        cumulative distance, m
    leg      leg index per sample
    heading  local direction of travel, radians, in the same CRS as gx/gy
    z        elevation, m (nan = no data)
    gx, gy   terrain gradient dz/dx, dz/dy (m/m), same CRS (nan = no data)
    src      index of the DEM that supplied the sample (-1 = none)

Definitions (docs/TRAIL_ANALYSIS.md §1.4):
    grade %  (z_i - z_prev) / (d_i - d_prev) * 100, prev = last sample with
             data, so a gap is bridged by one long run (as the old code did)
    slope %  |gradient| * 100: the side-slope of the ground the tread is cut into
    TSA °    trail-slope alignment, the angle in plan between the trail and
             the fall line: 0 = straight down the fall line (worst), 90 = on
             the contour (best). Here it is geometric: acos(|cos(heading -
             gradient direction)|). For a planar slope this equals the old
             acos(|grade| / slope), but it does not need clipping where local
             noise makes |grade| > slope. Null where slope < TSA_MIN_SLOPE,
             because on flat ground the fall line has no direction.
"""
import math

import numpy as np

from .units import length_m, percent, rate_m_per_day

TSA_MIN_SLOPE = 3.0          # %, below this TSA is null
PERCENTILES = (2, 5, 25, 50, 75, 95, 98)

DEFAULT_SETTINGS = {
    "sample_spacing": "5 ft",
    "averaging": {"grade_%": [9, 21], "TSA": [9], "slope_%": [5], "elev_m": [5, 9]},
    "thresholds": {"grade_%": [5, 18, 35], "slope_%": [10, 20, 73, 100], "TSA": [45, 60, 68]},
    "effort_rubric": None,     # None = no effort estimate
}

# Leg kinds whose construction the rubric estimates (see core.models.Leg).
BUILD_KINDS = {"new", "reroute", "restore"}


# ---------------------------------------------------------------------------
# Per-sample quantities
# ---------------------------------------------------------------------------

def grades(d, z):
    """Grade % at each sample relative to the previous sample with data."""
    g = np.full(len(d), np.nan)
    ok = np.flatnonzero(np.isfinite(z))
    if len(ok) >= 2:
        i, j = ok[1:], ok[:-1]
        run = d[i] - d[j]
        with np.errstate(invalid="ignore", divide="ignore"):
            g[i] = np.where(run > 0, (z[i] - z[j]) / run * 100.0, np.nan)
    return g


def tsa_deg(heading, gx, gy):
    slope = np.hypot(gx, gy) * 100.0
    with np.errstate(invalid="ignore"):
        aspect = np.arctan2(gy, gx)
        t = np.degrees(np.arccos(np.clip(np.abs(np.cos(heading - aspect)), 0.0, 1.0)))
    t[~(slope >= TSA_MIN_SLOPE)] = np.nan
    return t


def running_mean(v, w):
    """Centred moving mean over an odd window of `w` samples that skips
    nulls. Near the ends the window shrinks symmetrically (radius =
    distance to the end), so a steady climb keeps its true end elevations
    and smoothed climb is not shaved at both ends. All-null windows stay
    null. Replaces the old calculate_running_average, which summed nulls as
    zero and used an ad-hoc edge divisor."""
    w = int(w)
    if w <= 1 or len(v) == 0:
        return v.copy()
    half = w // 2
    ok = np.isfinite(v)
    vals = np.where(ok, v, 0.0)
    c = np.r_[0.0, np.cumsum(vals)]
    n = np.r_[0, np.cumsum(ok)]
    idx = np.arange(len(v))
    rad = np.minimum(np.minimum(idx, len(v) - 1 - idx), half)
    lo = idx - rad
    hi = idx + rad + 1
    cnt = n[hi] - n[lo]
    with np.errstate(invalid="ignore", divide="ignore"):
        out = (c[hi] - c[lo]) / cnt
    out[cnt == 0] = np.nan
    return out


# ---------------------------------------------------------------------------
# Statistics over a set of intervals
# ---------------------------------------------------------------------------

def _band_index(v, thresholds):
    """Band 0 is [0, t0), band k is [t_{k-1}, t_k), the last is [t_last, inf)."""
    return np.searchsorted(np.asarray(thresholds, float), v, side="right")


def _runs(mask, w):
    """Longest total weight (length) of consecutive True samples. Every
    sample of a run counts, including a run of one (the old code dropped
    one point from every run and never registered single-point runs)."""
    if not mask.any():
        return 0.0
    edges = np.diff(np.r_[0, mask.astype(np.int8), 0])
    starts, ends = np.flatnonzero(edges == 1), np.flatnonzero(edges == -1)
    c = np.r_[0.0, np.cumsum(w)]
    return float((c[ends] - c[starts]).max())


def band_table(v, w, thresholds):
    """Share, length and longest continuous run of each band. `v` per sample
    (already absolute where that is meant), `w` the interval length ending
    at each sample. Null samples are excluded from shares and break runs."""
    ok = np.isfinite(v)
    total = float(w[ok].sum())
    b = np.full(len(v), -1)
    b[ok] = _band_index(v[ok], thresholds)
    edges = [0.0] + list(thresholds) + [None]
    rows = []
    for k in range(len(thresholds) + 1):
        m = b == k
        L = float(w[m].sum())
        rows.append({
            "min": edges[k], "max": edges[k + 1],
            "pct": round(100.0 * L / total, 1) if total else None,
            "length_m": round(L, 1),
            "longest_m": round(_runs(m, w), 1),
        })
    return rows


def exceedance(v, w, t, below=False):
    ok = np.isfinite(v)
    total = float(w[ok].sum())
    m = ok & ((v < t) if below else (v >= t))
    L = float(w[m].sum())
    return {"pct": round(100.0 * L / total, 1) if total else None, "length_m": round(L, 1),
            "longest_m": round(_runs(m, w), 1)}


def _pcts(v):
    v = v[np.isfinite(v)]
    if not len(v):
        return None
    q = np.percentile(v, PERCENTILES)
    out = {f"p{p}": round(float(x), 1) for p, x in zip(PERCENTILES, q)}
    out.update(min=round(float(v.min()), 1), max=round(float(v.max()), 1), mean=round(float(v.mean()), 1))
    return out


def _climb(z):
    z = z[np.isfinite(z)]
    if len(z) < 2:
        return 0.0, 0.0
    dz = np.diff(z)
    return float(dz[dz > 0].sum()), float(-dz[dz < 0].sum())


def effort(slope_v, grade_abs_v, w, rubric):
    """Days to build and to maintain the stretch, per TRAIL_ANALYSIS.md §1.7:
    each sample's interval is binned by smoothed side-slope and by smoothed
    |grade|, and time += band_length / rate for both, summed. Samples with
    no data are left out and reported as `unassessed_m`."""
    out = {"construct_days": 0.0, "maintain_days": 0.0, "unassessed_m": 0.0, "bands": []}
    for key, v in (("slope_effect", slope_v), ("grade_effect", grade_abs_v)):
        ok = np.isfinite(v)
        out["unassessed_m"] = max(out["unassessed_m"], float(w[~ok].sum()))
        for band in rubric.get(key, []):
            lo, hi = percent(band["min"]), percent(band["max"])
            m = ok & (v >= lo) & (v < hi)
            L = float(w[m].sum())
            c = L / rate_m_per_day(band["construct"]) if L else 0.0
            mt = L / rate_m_per_day(band["maintain"]) if L else 0.0
            out["construct_days"] += c
            out["maintain_days"] += mt
            out["bands"].append({"effect": key, "min": lo, "max": hi, "length_m": round(L, 1),
                                 "construct_days": round(c, 2), "maintain_days": round(mt, 2),
                                 "note": band.get("note", "")})
    out["construct_days"] = round(out["construct_days"], 2)
    out["maintain_days"] = round(out["maintain_days"], 2)
    out["unassessed_m"] = round(out["unassessed_m"], 1)
    return out


# ---------------------------------------------------------------------------
# The whole evaluation
# ---------------------------------------------------------------------------

def _col(avg, key, default_w):
    ws = avg.get(key) or []
    return ws[0] if ws else default_w


def evaluate(d, leg, heading, z, gx, gy, src=None, legs=None, settings=None,
             lidar_sources=(), lon=None, lat=None, profile_points=1500):
    """Evaluate an alignment. `legs` is a list of dicts with at least
    `kind` (existing / new / reroute / restore) and optionally
    `effort_factor` and `name`, indexed like the `leg` array. Returns a
    JSON-ready dict: {summary, legs, profile, columns}."""
    st = {**DEFAULT_SETTINGS, **(settings or {})}
    avg = st.get("averaging") or {}
    thr = st.get("thresholds") or {}
    d = np.asarray(d, float)
    leg = np.asarray(leg, int)
    z = np.asarray(z, float)
    gx = np.asarray(gx, float)
    gy = np.asarray(gy, float)
    heading = np.asarray(heading, float)
    src = np.full(len(d), -1) if src is None else np.asarray(src, int)
    n = len(d)
    legs = legs or [{"kind": "new"} for _ in range(int(leg.max()) + 1 if n else 0)]

    grade = grades(d, z)
    slope = np.hypot(gx, gy) * 100.0
    tsa = tsa_deg(heading, gx, gy)

    cols = {"elev_m": z, "grade_%": grade, "slope_%": slope, "TSA": tsa}
    smooth = {}
    for key, ws in avg.items():
        base = cols.get(key)
        if base is None:
            continue
        for wdw in ws:
            smooth[f"r{wdw}_{key}"] = running_mean(base, wdw)

    # The columns the summary, bands and rubric read (the old rubric's
    # "slope_column": r5_slope, "grade_column": r9_grade).
    g_use = smooth.get(f"r{_col(avg, 'grade_%', 1)}_grade_%", grade)
    s_use = smooth.get(f"r{_col(avg, 'slope_%', 1)}_slope_%", slope)
    t_use = smooth.get(f"r{_col(avg, 'TSA', 1)}_TSA", tsa)
    ews = avg.get("elev_m") or []
    z_use = smooth.get(f"r{max(ews)}_elev_m", z) if ews else z
    g_abs = np.abs(g_use)

    w = np.r_[0.0, np.diff(d)] if n else np.zeros(0)
    lidar = np.isin(src, list(lidar_sources)) if len(lidar_sources) else np.zeros(n, bool)

    def stats(mask, kind=None, factor=1.0):
        ww = np.where(mask, w, 0.0)
        L = float(ww.sum())
        has = np.isfinite(z)
        up, down = _climb(z_use[mask])
        raw_up, raw_down = _climb(z[mask])
        zs = z[mask]
        zs = zs[np.isfinite(zs)]
        out = {
            "length_m": round(L, 1),
            "samples": int(mask.sum()),
            "coverage_pct": round(100.0 * float(ww[has].sum()) / L, 1) if L else None,
            "lidar_pct": round(100.0 * float(ww[lidar].sum()) / L, 1) if L else None,
            "climb_m": round(up, 1), "descent_m": round(down, 1),
            "raw_climb_m": round(raw_up, 1), "raw_descent_m": round(raw_down, 1),
            "z_min": round(float(zs.min()), 1) if len(zs) else None,
            "z_max": round(float(zs.max()), 1) if len(zs) else None,
            "z_start": round(float(zs[0]), 1) if len(zs) else None,
            "z_end": round(float(zs[-1]), 1) if len(zs) else None,
            "grade": _pcts(g_abs[mask]),
            "slope": _pcts(s_use[mask]),
            "tsa": _pcts(t_use[mask]),
            "grade_bands": band_table(np.where(mask, g_abs, np.nan), ww, thr.get("grade_%", [])),
            "slope_bands": band_table(np.where(mask, s_use, np.nan), ww, thr.get("slope_%", [])),
            "tsa_bands": band_table(np.where(mask, t_use, np.nan), ww, thr.get("TSA", [])),
            "grade_over": {str(t): exceedance(np.where(mask, g_abs, np.nan), ww, t)
                           for t in thr.get("grade_%", [])},
            "slope_over": {str(t): exceedance(np.where(mask, s_use, np.nan), ww, t)
                           for t in thr.get("slope_%", [])},
            "tsa_under": {str(t): exceedance(np.where(mask, t_use, np.nan), ww, t, below=True)
                          for t in thr.get("TSA", [])},
        }
        rub = st.get("effort_rubric")
        if rub:
            e = effort(np.where(mask, s_use, np.nan), np.where(mask, g_abs, np.nan), ww, rub)
            builds = kind is None or kind in BUILD_KINDS
            e["construct_days"] = round(e["construct_days"] * (factor if builds else 0.0), 2)
            out["effort"] = e
        return out

    # Interval (i-1, i] belongs to leg[i]; sample 0 opens the first leg.
    leg_rows = []
    total_construct = total_maintain = 0.0
    build_m = existing_m = 0.0
    for j, spec in enumerate(legs):
        m = leg == j
        if not m.any():
            leg_rows.append({"index": j, "kind": spec.get("kind", "new"), "length_m": 0.0})
            continue
        kind = spec.get("kind", "new")
        factor = float(spec.get("effort_factor", 1.0) or 0.0)
        s = stats(m, kind, factor)
        s.update(index=j, kind=kind, name=spec.get("name", ""), effort_factor=factor,
                 d_start=round(float(d[np.flatnonzero(m)[0]] - w[np.flatnonzero(m)[0]]), 1),
                 d_end=round(float(d[np.flatnonzero(m)[-1]]), 1))
        if "effort" in s:
            total_construct += s["effort"]["construct_days"]
            total_maintain += s["effort"]["maintain_days"]
        if kind in BUILD_KINDS:
            build_m += s["length_m"]
        else:
            existing_m += s["length_m"]
        leg_rows.append(s)

    summary = stats(np.ones(n, bool))
    summary.pop("effort", None)
    summary.update(build_m=round(build_m, 1), existing_m=round(existing_m, 1),
                   construct_days=round(total_construct, 2), maintain_days=round(total_maintain, 2))
    zs = z[np.isfinite(z)]
    summary["runs_uphill"] = bool(zs[-1] > zs[0]) if len(zs) >= 2 else None

    # Downsampled profile for the chart (the statistics above use every sample).
    step = max(1, int(math.ceil(n / float(profile_points)))) if n else 1
    keep = np.unique(np.r_[np.arange(0, n, step), n - 1]) if n else np.zeros(0, int)

    def r(a, nd):
        return [None if not np.isfinite(x) else round(float(x), nd) for x in a[keep]]

    profile = {"d": r(d, 1), "z": r(z, 1), "g": r(g_use, 1), "s": r(s_use, 1), "t": r(t_use, 0),
               "src": [int(x) for x in src[keep]], "leg": [int(x) for x in leg[keep]]}
    if lon is not None and lat is not None:
        profile["lon"] = r(np.asarray(lon, float), 6)
        profile["lat"] = r(np.asarray(lat, float), 6)
    return {
        "summary": summary,
        "legs": leg_rows,
        "profile": profile,
        "columns": {"grade": f"r{_col(avg, 'grade_%', 1)}_grade_%", "slope": f"r{_col(avg, 'slope_%', 1)}_slope_%",
                    "tsa": f"r{_col(avg, 'TSA', 1)}_TSA", "elev_for_climb": f"r{max(ews)}_elev_m" if ews else "elev_m"},
        "spacing_m": round(float(np.median(np.diff(d))), 3) if n > 1 else None,
        "thresholds": thr,
    }


def spacing_m(settings):
    return length_m((settings or {}).get("sample_spacing", DEFAULT_SETTINGS["sample_spacing"]))
