"""The route-finder: a seed for the search that can wind (owner, 2026-10-18).

The annealer (search.py) only nudges a smooth curve fitted to the person's
line, so it cannot invent a run of switchbacks up a slope the line goes
straight up. This finds the cheapest path between the line's ends over a
grid of the terrain within the search's limit, by the same factors, and
hands it to the annealer as one more chain's start; the annealer polishes
it and the real scoring (score.py) judges it with every other candidate.

The grid: cells CELL_M apart where the terrain is known and within the
limit. A step goes to one of 32 neighbours (the 8 around, the 8 a
knight's move away, and those at (3, 1) and (3, 2): DIRS). The state is
(cell, heading), so turns can be charged. Costs, in the same equivalent
metres as score.py:

  a step     its length along the slope x (1 + the per-metre factors there):
             grade on the ground (the route has no cut and fill; the climb
             and the descent along the step, read every SUB_M, over its
             length, as score.py reads grade, so a step cannot leap a
             gully narrower than itself), terrain
             slope, TSA and fall-line tread from the slope's direction
             against the step's, wet ground, off existing tread, and cut
             and fill at 0 (its floor)
  a turn     part of a corner (turns of corner_min_turn_deg or more: the
             corner curve at the turn's radius, times the share of 180
             degrees turned, so a hairpin made of two turns is one corner),
             or a bend (the bend curve at its radius per right angle). The
             radius is the one the scoring will read at that vertex of the
             route, BEND_WINDOW_M over the turn (a 90 degree turn on the
             grid reads 0.64 m), so the route pays for a tight turn what the
             scoring will charge it (owner, 2026-10-19: when the route
             charged every turn as a 4 m one, it made hairpins the scoring
             charged 14 times as much, and the polish knotted them)

  excess     every metre a step descends on a net climb (climbs on a net
  climb      descent), at the excess climb curve for a 1 m loss (or the dead
             band, if deeper): a path search cannot see a whole dip, so it
             charges every one, drainage dips included; the scoring reads
             them through the dead band

What it leaves out, because a path search cannot add them up step by step:
the steepest-pitch charge, drainage, and the cut/fill tube. Those are the
scoring's job once the annealer has the line. A* with the straight-line
distance to the end as the estimate (every metre costs at least 1).
"""
import heapq
import math

import numpy as np

from .score import BEND_WINDOW_M

CELL_M = 2.0              # m between grid cells
MAX_TURN_DEG = 135.0      # a sharper turn at one cell is not allowed (a hairpin takes two)
SMOOTH_M = 1.0            # m: elevations are averaged over this either side (lidar noise)
READ_SHARE = 0.2          # the share of the route-finder's progress given to reading and pricing the terrain
SUB_M = 1.0               # m: a step's ground is read at least this often along it
SIMPLIFY_M = 0.5          # m: the route keeps vertices that move it more than this
LIMIT_MARGIN_M = 1.5      # m: the route keeps this far inside the limit, so the smooth curve fitted to it fits too
FLAT_PCT = 4.0            # %: flatter ground has no fall line (score.FALL_MIN_SLOPE_PCT)

# Steps to 32 neighbours: the 8 around, the 8 a knight's move away, and the
# 16 at (3, 1) and (3, 2). Headings then come every 8-18 degrees, so a
# traverse can hold a gentle grade across a steep face (12 % across 40 %
# ground is 17.5 degrees off the contour: the 18.4 degree step).
DIRS = sorted({(sx * a, sy * b) for a, b in ((1, 0), (1, 1), (2, 1), (3, 1), (3, 2))
               for a, b in ((a, b), (b, a)) for sx in (1, -1) for sy in (1, -1)},
              key=lambda d: math.atan2(d[1], d[0]))
ND = len(DIRS)


def _simplify(xy, tol):
    keep = np.zeros(len(xy), bool)
    keep[0] = keep[-1] = True
    stack = [(0, len(xy) - 1)]
    while stack:
        a, b = stack.pop()
        if b <= a + 1:
            continue
        p, q = xy[a], xy[b]
        seg = q - p
        L = math.hypot(*seg)
        pts = xy[a + 1:b]
        d = (np.hypot(*(pts - p).T) if L < 1e-9 else
             np.abs(seg[0] * (pts[:, 1] - p[1]) - seg[1] * (pts[:, 0] - p[0])) / L)
        i = int(np.argmax(d))
        if d[i] > tol:
            keep[a + 1 + i] = True
            stack += [(a, a + 1 + i), (a + 1 + i, b)]
    return xy[keep]


def _smoothed(g, m):
    """The grid with each elevation averaged with the four m away (the
    route's lidar-noise smoothing, done once rather than per sample)."""
    from .terrain import Grid
    o = max(1, int(round(m / g.res)))
    z = np.pad(g.z, o, mode="edge")
    n, w = g.z.shape
    zs = g.z.copy()                               # summed in place: one grid's worth of memory, not five
    for part in (z[o:o + n, 2 * o:], z[o:o + n, :w], z[2 * o:, o:o + w], z[:n, o:o + w]):
        zs += part
    del z
    zs /= 5
    return Grid(zs, g.x0, g.y0, g.res)


def route_line(prob, k=None, cell=CELL_M, progress=None):
    """The cheapest path from the line's start to its end within prob.limit,
    as an (m, 2) array in the line's metric CRS, or None if the ends cannot
    be joined inside the limit. `progress(fraction, what)` is called now and
    then: reading the terrain is the first READ_SHARE, and the search
    reports how far towards the end its frontier has got in a straight line
    (it widens behind the frontier, so it moves unevenly)."""
    say = progress or (lambda fr, what: None)
    k = prob.base_k if k is None else k
    g = prob.grid
    P0, P1 = prob.P[0], prob.P[-1]
    x0 = g.x0 + 0.5 * g.res
    y0 = g.y0 + 0.5 * g.res
    W = int((g.x0 + g.z.shape[1] * g.res - x0) // cell)
    H = int((g.y0 + g.z.shape[0] * g.res - y0) // cell)
    xs = x0 + np.arange(W) * cell
    ys = y0 + np.arange(H) * cell
    # Cells within the limit first, row by row, and the terrain read only
    # there: the bounding box of a long trail is mostly outside its corridor.
    cand = []
    for r_ in range(H):
        d_ = prob.dist_grid.sample(xs, np.full(W, ys[r_]))
        c_ = np.flatnonzero(np.isfinite(d_) & (d_ <= prob.limit - LIMIT_MARGIN_M))
        if len(c_):
            cand.append(r_ * W + c_)
    if not cand:
        return None
    cand = np.concatenate(cand)
    X, Y = xs[cand % W], ys[cand // W]
    gs = _smoothed(g, SMOOTH_M)
    z = gs.sample(X, Y)
    b2 = prob.bench / 2
    gx = (g.sample(X + b2, Y) - g.sample(X - b2, Y)) / prob.bench
    gy = (g.sample(X, Y + b2) - g.sample(X, Y - b2)) / prob.bench
    ok = np.isfinite(z) & np.isfinite(gx) & np.isfinite(gy)
    if not ok.any():
        return None
    nodes = cand[ok]
    idx = np.full(W * H, -1, np.int32)
    idx[nodes] = np.arange(len(nodes))
    N = len(nodes)
    nx, ny, nz = X[ok], Y[ok], z[ok]
    ngx, ngy = gx[ok], gy[ok]
    del X, Y, z, gx, gy, cand
    col, row = nodes % W, nodes // W
    f = prob.f
    wet = (np.nan_to_num(prob.twi_grid.sample(nx, ny), nan=0.0) if prob.twi_grid is not None else None)
    off = (np.nan_to_num(prob.existing_grid.sample(nx, ny), nan=0.0) if prob.existing_grid is not None else None)
    # The per-metre charges that do not depend on the step's direction.
    base = k.get("slope", 0) * f["slope"](100 * np.hypot(ngx, ngy)) + k.get("cutfill", 0) * f["cutfill"](0.0)
    if wet is not None:
        base = base + k.get("wet", 0) * f["wet"](wet)
    if off is not None:
        base = base + k.get("existing", 0) * f["existing"](off)
    # Excess climb: the stretch's net direction, from its ends on the ground.
    k_climb = k.get("climb", 0) * float(f["climb"](max(1.0, prob.climb_band)))
    climbs = not (gs.sample(*P1[:, None])[0] < gs.sample(*P0[:, None])[0])
    nb = np.full((ND, N), -1, np.int32)
    ec = np.full((ND, N), np.inf, np.float32)
    for d, (dc, dr) in enumerate(DIRS):
        say(READ_SHARE * (0.2 + 0.8 * d / ND), "pricing the steps")
        c2, r2 = col + dc, row + dr
        inside = (c2 >= 0) & (c2 < W) & (r2 >= 0) & (r2 < H)
        j = np.full(N, -1)
        j[inside] = idx[r2[inside] * W + c2[inside]]
        good = j >= 0
        i = np.flatnonzero(good)
        jj = j[good]
        L2 = cell * math.hypot(dc, dr)
        m = max(1, math.ceil(L2 / SUB_M - 1e-9))
        prev, up_down, slope_len = nz[i], np.zeros(len(i)), np.zeros(len(i))
        lost = np.zeros(len(i))
        for q in range(1, m + 1):
            zq = nz[jj] if q == m else gs.sample(nx[i] + q / m * dc * cell, ny[i] + q / m * dr * cell)
            up_down += np.abs(zq - prev)
            lost += np.maximum(0.0, (prev - zq) if climbs else (zq - prev))
            slope_len += np.hypot(L2 / m, zq - prev)
            prev = zq
        ok_step = np.isfinite(up_down)
        grade = 100 * np.nan_to_num(up_down, nan=0.0) / L2
        tx, ty = dc / math.hypot(dc, dr), dr / math.hypot(dc, dr)
        Gx, Gy = (ngx[i] + ngx[jj]) / 2, (ngy[i] + ngy[jj]) / 2
        along, cross = Gx * tx + Gy * ty, -Gx * ty + Gy * tx
        tsa = np.degrees(np.arctan2(np.abs(cross), np.abs(along)))
        tsa[100 * np.hypot(Gx, Gy) < FLAT_PCT] = 90.0
        per = (1 + (base[i] + base[jj]) / 2 + k.get("grade", 0) * f["grade"](grade) + k.get("tsa", 0) * f["tsa"](tsa)
               + k.get("falltread", 0) * f["falltread"](grade) * (tsa < prob.fall_tsa))
        nb[d, i] = np.where(ok_step, jj, -1)
        ec[d, i] = np.where(ok_step, slope_len * per + k_climb * np.nan_to_num(lost, nan=0.0), np.inf)
    # Turn costs between headings.
    ang = np.array([math.atan2(dr, dc) for dc, dr in DIRS])
    turn = np.abs((ang[None, :] - ang[:, None] + math.pi) % (2 * math.pi) - math.pi)
    r = BEND_WINDOW_M / np.maximum(turn, 1e-9)
    corner = k.get("corner", 0) * f["corner"](r)
    bend = k.get("bend", 0) * f["bend"](r)
    T = np.where(turn >= prob.min_turn, corner * turn / math.pi, bend * turn / (math.pi / 2))
    T[turn > math.radians(MAX_TURN_DEG)] = np.inf
    T = T.tolist()

    def nearest(p):
        return int(np.argmin(np.hypot(nx - p[0], ny - p[1])))
    s0, s1 = nearest(P0), nearest(P1)
    gxe, gye = nx[s1], ny[s1]
    # The search's state lives in flat typed arrays read through memoryviews
    # (owner, 2026-10-19: as Python dicts and lists a 7 km trail in a 100 m
    # corridor took 3 GB, and the 4 GB droplet swapped). Node u's neighbour
    # and step cost in direction d are at u * ND + d.
    hx = memoryview(np.hypot(nx - gxe, ny - gye))
    nbv = memoryview(np.ascontiguousarray(nb.T).ravel())
    ecv = memoryview(np.ascontiguousarray(ec.T).ravel())
    del nb, ec
    best_a = np.full(N * ND, np.inf)
    parent_a = np.full(N * ND, -1, np.int32)
    best, parent = memoryview(best_a), memoryview(parent_a)
    heap = []                     # (estimate, state); stale entries are skipped on popping
    for d in range(ND):
        st = s0 * ND + d
        best[st] = 0.0
        heapq.heappush(heap, (hx[s0], st))
    done = None
    h0, h_min, pops = max(hx[s0], 1e-9), hx[s0], 0
    say(READ_SHARE, "searching")
    while heap:
        fsc, st = heapq.heappop(heap)
        u, din = divmod(st, ND)
        gc = best[st]
        if fsc > gc + hx[u] + 1e-9:
            continue
        pops += 1
        if hx[u] < h_min:
            h_min = hx[u]
        if pops % 200_000 == 0:
            say(READ_SHARE + (1 - READ_SHARE) * (1 - h_min / h0), "searching")
        if u == s1:
            done = st
            break
        Tin = T[din]
        base_i = u * ND
        for dout in range(ND):
            v = nbv[base_i + dout]
            if v < 0:
                continue
            ng = gc + ecv[base_i + dout] + Tin[dout]
            sv = v * ND + dout
            if ng < best[sv]:
                best[sv] = ng
                parent[sv] = st
                heapq.heappush(heap, (ng + hx[v], sv))
    if done is None:
        return None
    path = []
    st = done
    while st >= 0:
        path.append(st // ND)
        st = parent[st]
    path.reverse()
    xy = np.column_stack([nx[path], ny[path]])
    xy = np.vstack([P0, xy[1:-1], P1]) if len(xy) > 2 else np.vstack([P0, P1])
    return _simplify(xy, SIMPLIFY_M)
