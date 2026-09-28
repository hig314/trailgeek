"""Resample an alignment's legs at a fixed spacing, in a projected (metric)
CRS. Each leg is divided into equal steps no longer than `spacing`, so leg
boundaries fall exactly on a sample and every leg's length is exact.

Accounting convention used everywhere downstream: sample 0 starts the line,
and the interval (i-1, i] belongs to the leg of sample i. A boundary sample
is the last sample of the leg that ends there, so no interval is counted
twice and per-leg lengths add up to the total.
"""
import numpy as np


def _leg_points(xy, spacing):
    xy = np.asarray(xy, dtype=float)
    seg = np.hypot(np.diff(xy[:, 0]), np.diff(xy[:, 1]))
    keep = np.r_[True, seg > 0]                 # drop repeated vertices
    xy = xy[keep]
    if len(xy) < 2:
        return xy, np.zeros(len(xy))
    seg = np.hypot(np.diff(xy[:, 0]), np.diff(xy[:, 1]))
    cum = np.r_[0.0, np.cumsum(seg)]
    total = cum[-1]
    n = max(1, int(np.ceil(total / spacing - 1e-9)))
    s = np.linspace(0.0, total, n + 1)
    x = np.interp(s, cum, xy[:, 0])
    y = np.interp(s, cum, xy[:, 1])
    return np.column_stack([x, y]), s


def resample_legs(legs_xy, spacing):
    """legs_xy: list of (N_i, 2) coordinate arrays in metres, each leg
    starting where the previous one ended. Returns a dict of arrays:
    x, y, d (cumulative metres), leg (index), heading (radians, atan2 of the
    local direction in the same CRS, east = 0, counter-clockwise)."""
    if spacing <= 0:
        raise ValueError("spacing must be positive")
    xs, ys, ds, legs = [], [], [], []
    offset = 0.0
    for j, xy in enumerate(legs_xy):
        pts, s = _leg_points(xy, spacing)
        if len(pts) < 2:
            continue
        start = 0 if not xs else 1              # the shared vertex is written once
        xs.append(pts[start:, 0])
        ys.append(pts[start:, 1])
        ds.append(offset + s[start:])
        lg = np.full(len(s) - start, j, dtype=int)
        legs.append(lg)
        offset += s[-1]
    if not xs:
        raise ValueError("alignment has no length")
    x, y, d, leg = (np.concatenate(a) for a in (xs, ys, ds, legs))
    # Heading: central difference, one-sided at the ends.
    dx = np.gradient(x) if len(x) > 1 else np.zeros(1)
    dy = np.gradient(y) if len(y) > 1 else np.zeros(1)
    return {"x": x, "y": y, "d": d, "leg": leg, "heading": np.arctan2(dy, dx)}
