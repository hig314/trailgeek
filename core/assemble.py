"""Assemble one continuous alignment from one option per section (owner,
2026-10-26; PLAN.md section 0, "presenting a project as one route").

Each chosen alignment is a piece; the pieces are joined in section order.
Joins are a first guess, reported for the person to check:

  end      the previous piece's end meets the next piece's start
  leaves   the next piece starts on the middle of the previous one, which is
           cut there (the climb leaving the Lagoon Bypass: Link B ~335 m
           along it, Link A ~891 m, so the bypass is one option and is cut
           where the chosen link leaves it)
  joins    the previous piece ends on the middle of the next one, whose
           beginning is cut

whichever is nearest (an end-to-end join wins a tie by END_PREFERENCE_M).
A gap up to SNAP_M is closed by moving the joint; a longer one is bridged
by a new-construction leg. Each piece runs the
way that makes the joins shortest, as drawn on a tie, unless the person
says otherwise. Work is in the pieces' UTM zone; legs keep their kind,
effort factor and trail.
"""
import math

import numpy as np

from . import geo
from .dem import transform_xy

JOIN_TOL_M = 0.5          # closer than this the pieces meet
SNAP_M = 3.0              # a gap up to this is closed by moving the joint (2026-10-26: Tutka Tusk's pieces
                          # meet within 0-2 m, and 1 m "new construction" legs were clutter); longer, a bridge
END_PREFERENCE_M = 1.0    # a middle join must be this much nearer than an end-to-end one to win
MIN_PIECE_M = 1.0         # a piece cut shorter than this is an error, not a piece


class AssembleError(ValueError):
    pass


def _arc(xy):
    return np.concatenate([[0.0], np.cumsum(np.hypot(*np.diff(xy, axis=0).T))])


def _nearest_on(xy, p):
    """(distance, arc length along xy, point) of the nearest point of the
    polyline xy to p."""
    a, b = xy[:-1], xy[1:]
    ab = b - a
    L2 = (ab ** 2).sum(1)
    t = np.clip(((p - a) * ab).sum(1) / np.maximum(L2, 1e-12), 0, 1)
    q = a + t[:, None] * ab
    d = np.hypot(*(q - p).T)
    i = int(np.argmin(d))
    s = _arc(xy)
    return float(d[i]), float(s[i] + t[i] * math.sqrt(L2[i])), q[i]


def _cut(xy, s0, s1):
    """The part of polyline xy between arc lengths s0 and s1."""
    s = _arc(xy)
    inner = (s > s0) & (s < s1)
    p0 = np.array([np.interp(s0, s, xy[:, 0]), np.interp(s0, s, xy[:, 1])])
    p1 = np.array([np.interp(s1, s, xy[:, 0]), np.interp(s1, s, xy[:, 1])])
    return np.vstack([p0, xy[inner], p1])


class Piece:
    """A chosen alignment: its legs in the pieces' metric CRS, either way."""

    def __init__(self, section, alignment, legs, srid):
        self.section, self.alignment = section, alignment
        self.legs = []
        for leg in legs:
            cs = np.asarray(leg.geom.coords, float)[:, :2]
            x, y = transform_xy(cs[:, 0], cs[:, 1], 4326, srid)
            self.legs.append({"xy": np.column_stack([x, y]), "kind": leg.kind, "effort_factor": leg.effort_factor,
                              "trail_id": leg.trail_id, "name": leg.name or alignment.name, "notes": leg.notes})
        self.reversed = False

    def line(self, rev=None):
        rev = self.reversed if rev is None else rev
        legs = self.oriented_legs(rev)
        return np.vstack([legs[0]["xy"]] + [L["xy"][1:] for L in legs[1:]])

    def oriented_legs(self, rev):
        if not rev:
            return self.legs
        return [dict(L, xy=L["xy"][::-1]) for L in self.legs[::-1]]


def _join(P, N):
    """The join between oriented lines P (previous) and N (next): a dict with
    its kind, gap and cut positions."""
    dA = float(np.hypot(*(P[-1] - N[0])))
    best = {"kind": "end", "gap_m": dA, "cut_prev_m": None, "cut_next_m": None, "at_prev": P[-1], "at_next": N[0]}
    LP, LN = _arc(P)[-1], _arc(N)[-1]
    dB, sB, qB = _nearest_on(P, N[0])
    # A middle join must be in the middle: not at the far end of the piece it
    # cuts (that would cut the piece away; a piece the wrong way round, forced).
    if MIN_PIECE_M < sB < LP - JOIN_TOL_M and dB + END_PREFERENCE_M < best["gap_m"]:
        best = {"kind": "leaves", "gap_m": dB, "cut_prev_m": sB, "cut_next_m": None, "at_prev": qB, "at_next": N[0]}
    dC, sC, qC = _nearest_on(N, P[-1])
    if JOIN_TOL_M < sC < LN - MIN_PIECE_M and dC + END_PREFERENCE_M < best["gap_m"]:
        best = {"kind": "joins", "gap_m": dC, "cut_prev_m": None, "cut_next_m": sC, "at_prev": P[-1], "at_next": qC}
    return best


def _orient(pieces, fixed):
    """Directions for the pieces that make the joins shortest in total (fewer
    reversals on a tie); `fixed[i]` is True / False to force piece i's."""
    n = len(pieces)
    opts = [[fixed[i]] if fixed[i] is not None else [False, True] for i in range(n)]
    lines = [{r: p.line(r) for r in opts[i]} for i, p in enumerate(pieces)]
    # cost[i][r] = (total gap, reversals) of the best directions for 0..i with i in r
    cost = [{r: (0.0, int(r)) for r in opts[0]}]
    back = [{}]
    for i in range(1, n):
        c, b = {}, {}
        for r in opts[i]:
            best = None
            for rp, (g, nrev) in cost[i - 1].items():
                j = _join(lines[i - 1][rp], lines[i][r])
                cand = (g + j["gap_m"] + (END_PREFERENCE_M if j["kind"] != "end" else 0.0), nrev + int(r))
                if best is None or cand < best[0]:
                    best = (cand, rp)
            c[r], b[r] = best
        cost.append(c)
        back.append(b)
    r = min(cost[-1], key=lambda k: cost[-1][k])
    out = [r]
    for i in range(n - 1, 0, -1):
        r = back[i][r]
        out.append(r)
    return out[::-1]


def assemble(choices, reverse=None):
    """`choices`: [(section, alignment)] in section order, each alignment
    with its legs. `reverse`: per piece True / False to force its direction,
    None to let the joins decide. Returns (legs, report): legs as the API's
    leg dicts in lon/lat (coords, kind, effort_factor, trail_id, name,
    notes), consecutive legs sharing their joint; report per piece and per
    join, for the panel and Alignment.assembly."""
    if not choices:
        raise AssembleError("choose an option for at least one section")
    reverse = list(reverse or [None] * len(choices))
    pts = []
    for _, a in choices:
        if a.geom is None:
            raise AssembleError(f"{a.name} has no legs")
        pts.append(a.geom.centroid)
    srid = geo.utm_srid(float(np.mean([p.x for p in pts])), float(np.mean([p.y for p in pts])))
    pieces = [Piece(s, a, list(a.legs.order_by("order")), srid) for s, a in choices]
    for p, r in zip(pieces, _orient(pieces, reverse)):
        p.reversed = r
    lines = [p.line() for p in pieces]
    keep = [[0.0, float(_arc(L)[-1])] for L in lines]
    joins = []
    for i in range(1, len(pieces)):
        j = _join(lines[i - 1], lines[i])
        if j["cut_prev_m"] is not None:
            keep[i - 1][1] = j["cut_prev_m"]
        if j["cut_next_m"] is not None:
            keep[i][0] = j["cut_next_m"]
        joins.append(j)
    for p, (s0, s1) in zip(pieces, keep):
        if s1 - s0 < MIN_PIECE_M:
            raise AssembleError(f"{p.alignment.name} is cut away entirely by its joins; check the sections' order "
                                "and the pieces' directions")

    legs_xy, report = [], {"pieces": [], "joins": []}
    for i, (p, (s0, s1)) in enumerate(zip(pieces, keep)):
        if i:
            j = joins[i - 1]
            a_, b_ = np.asarray(j["at_prev"]), np.asarray(j["at_next"])
            bridged = j["gap_m"] > SNAP_M
            if bridged:
                legs_xy.append({"xy": np.vstack([a_, b_]), "kind": "new", "effort_factor": 1.0, "trail_id": None,
                                "name": f"Join: {pieces[i - 1].alignment.name} to {p.alignment.name}", "notes": ""})
            report["joins"].append({
                "after": pieces[i - 1].section.name if pieces[i - 1].section else pieces[i - 1].alignment.name,
                "before": p.section.name if p.section else p.alignment.name,
                "kind": j["kind"], "gap_m": round(j["gap_m"], 1), "bridge_m": round(j["gap_m"], 1) if bridged else 0.0,
                "snapped_m": round(j["gap_m"], 1) if JOIN_TOL_M < j["gap_m"] <= SNAP_M else 0.0,
                "cut_prev_m": None if j["cut_prev_m"] is None else round(j["cut_prev_m"], 1),
                "cut_next_m": None if j["cut_next_m"] is None else round(j["cut_next_m"], 1),
            })
        # This piece's legs, kept between s0 and s1 along it.
        at = 0.0
        for L in p.oriented_legs(p.reversed):
            xy = L["xy"]
            ln = float(_arc(xy)[-1])
            lo, hi = max(s0, at) - at, min(s1, at + ln) - at
            at += ln
            if hi - lo <= 1e-6:
                continue
            legs_xy.append(dict(L, xy=_cut(xy, lo, hi) if (lo > 1e-6 or hi < ln - 1e-6) else xy))
        report["pieces"].append({
            "section_id": p.section.pk if p.section else None, "section": p.section.name if p.section else None,
            "alignment_id": p.alignment.pk, "alignment": p.alignment.name, "reversed": p.reversed,
            "length_m": round(float(_arc(lines[i])[-1]), 1), "kept_m": [round(s0, 1), round(s1, 1)],
        })
    # Joints exact: each leg starts where the last one ended.
    for k in range(1, len(legs_xy)):
        legs_xy[k]["xy"] = np.vstack([legs_xy[k - 1]["xy"][-1:], legs_xy[k]["xy"][1:]])
    out = []
    for L in legs_xy:
        xy = L["xy"]
        keep_ = np.concatenate([[True], np.hypot(*np.diff(xy, axis=0).T) > 1e-6])
        xy = xy[keep_]
        if len(xy) < 2:
            continue
        lon, lat = transform_xy(xy[:, 0], xy[:, 1], srid, 4326)
        out.append({"coords": [[float(a), float(b)] for a, b in zip(lon, lat)], "kind": L["kind"],
                    "effort_factor": L["effort_factor"], "trail_id": L["trail_id"], "name": L["name"][:200],
                    "notes": L["notes"] or ""})
    for k in range(1, len(out)):
        out[k]["coords"][0] = list(out[k - 1]["coords"][-1])
    report["length_m"] = round(sum(float(_arc(L["xy"])[-1]) for L in legs_xy), 1)
    return out, report
