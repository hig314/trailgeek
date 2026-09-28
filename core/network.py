"""Routing along the existing trail network, for "follow existing trail"
legs: the shortest path over visible trails between two points.

The trails are separate lines digitised at different times, so they rarely
share exact vertices where they meet. PostGIS does the topology: lines
near the two points are projected to the local UTM zone, snapped to each
other within SNAP_M (closing near-miss T-junctions), and noded at every
crossing. A plain Dijkstra over the noded edges finds the path, starting
and ending part-way along the edges nearest the two points. Each edge
remembers which trail it came from, so the leg can say what it follows.
"""
import heapq
import math

from django.contrib.gis.geos import GEOSGeometry
from django.db import connection

from . import access, geo
from .dem import transform_xy

SNAP_M = 5.0            # endpoints this close to another line join it (most join at 0 m; some digitising near-misses reach ~5 m)
MAX_SNAP_M = 250.0      # a point further than this from any trail is not "on" one
MARGIN_M = 1500.0       # search box around the two points


class NoRoute(ValueError):
    pass


def _edges(user, bbox_utm, srid):
    """Noded, snapped trail edges inside the box: [(trail_id, [(x, y), ...])]."""
    vis, params = access.visibility_sql(user, "t")
    sql = f"""
        WITH box AS (SELECT ST_MakeEnvelope(%s, %s, %s, %s, %s) AS g),
        src AS (
          SELECT t.id, (ST_Dump(ST_Transform(t.geom, %s))).geom AS g
          FROM core_trail t, box
          WHERE t.geom && ST_Transform(box.g, 4326) AND {vis}
        ),
        allg AS (SELECT ST_Collect(g) AS g FROM src),
        snapped AS (SELECT src.id, ST_Snap(src.g, allg.g, %s) AS g FROM src, allg),
        noded AS (SELECT (ST_Dump(ST_Node(ST_Collect(g)))).geom AS g FROM snapped)
        SELECT (SELECT s.id FROM snapped s
                 ORDER BY s.g <-> ST_LineInterpolatePoint(n.g, 0.5) LIMIT 1) AS trail_id,
               ST_AsBinary(n.g)
        FROM noded n WHERE ST_Length(n.g) > 0
    """
    x0, y0, x1, y1 = bbox_utm
    with connection.cursor() as cur:
        cur.execute(sql, [x0, y0, x1, y1, srid, srid] + params + [SNAP_M])
        rows = cur.fetchall()
    out = []
    for tid, wkb in rows:
        g = GEOSGeometry(memoryview(bytes(wkb)))
        out.append((tid, [tuple(c[:2]) for c in g.coords]))
    return out


def _nearest_on(edges, p):
    """(edge index, segment index, t along segment, distance, point)."""
    best = None
    px, py = p
    for ei, (_, cs) in enumerate(edges):
        for si in range(len(cs) - 1):
            (ax, ay), (bx, by) = cs[si], cs[si + 1]
            dx, dy = bx - ax, by - ay
            L2 = dx * dx + dy * dy
            t = 0.0 if L2 == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / L2))
            qx, qy = ax + t * dx, ay + t * dy
            d = math.hypot(px - qx, py - qy)
            if best is None or d < best[3]:
                best = (ei, si, t, d, (qx, qy))
    return best


def _key(pt):
    return (round(pt[0], 2), round(pt[1], 2))


def _length(cs):
    return sum(math.hypot(cs[i + 1][0] - cs[i][0], cs[i + 1][1] - cs[i][1]) for i in range(len(cs) - 1))


def route(user, start_lonlat, end_lonlat):
    """Shortest path over visible trails. Returns {"coords": [[lon, lat]...],
    "length_m", "trails": [{"id", "length_m"}...], "start_offset_m",
    "end_offset_m"} where the offsets say how far each input point was from
    the network (the path starts and ends on it)."""
    srid = geo.utm_srid((start_lonlat[0] + end_lonlat[0]) / 2, (start_lonlat[1] + end_lonlat[1]) / 2)
    xs, ys = transform_xy([start_lonlat[0], end_lonlat[0]], [start_lonlat[1], end_lonlat[1]], 4326, srid)
    a, b = (xs[0], ys[0]), (xs[1], ys[1])
    m = MARGIN_M + 0.25 * math.hypot(b[0] - a[0], b[1] - a[1])
    edges = _edges(user, (min(a[0], b[0]) - m, min(a[1], b[1]) - m, max(a[0], b[0]) + m, max(a[1], b[1]) + m), srid)
    if not edges:
        raise NoRoute("no trails near those points")
    sa, sb = _nearest_on(edges, a), _nearest_on(edges, b)
    if sa[3] > MAX_SNAP_M or sb[3] > MAX_SNAP_M:
        raise NoRoute(f"a point is more than {int(MAX_SNAP_M)} m from any trail")

    # Graph: nodes are edge endpoints; the start and end points split the
    # edges they lie on, so the path can begin and end part-way along them.
    graph = {}

    def link(cs, tid):
        L = _length(cs)
        if len(cs) < 2 or L <= 0:
            return
        graph.setdefault(_key(cs[0]), []).append((_key(cs[-1]), cs, tid, L))
        graph.setdefault(_key(cs[-1]), []).append((_key(cs[0]), cs[::-1], tid, L))

    cuts = {}
    for ei, si, t, _, pt in (sa, sb):
        cuts.setdefault(ei, []).append((si, t, pt))
    for ei, (tid, cs) in enumerate(edges):
        todo = sorted(cuts.get(ei, []), key=lambda c: (c[0], c[1]))
        cur, ci = [cs[0]], 0
        for i in range(len(cs) - 1):
            while ci < len(todo) and todo[ci][0] == i:
                pt = todo[ci][2]
                cur.append(pt)
                link(cur, tid)
                cur, ci = [pt], ci + 1
            cur.append(cs[i + 1])
        link(cur, tid)

    start, goal = _key(sa[4]), _key(sb[4])
    if start == goal:
        raise NoRoute("the two points are the same place on the trail")
    dist, prev = {start: 0.0}, {}
    heap = [(0.0, start)]
    while heap:
        dcur, u = heapq.heappop(heap)
        if u == goal:
            break
        if dcur > dist.get(u, math.inf):
            continue
        for v, cs, tid, L in graph.get(u, []):
            nd = dcur + L
            if nd < dist.get(v, math.inf):
                dist[v] = nd
                prev[v] = (u, cs, tid, L)
                heapq.heappush(heap, (nd, v))
    if goal not in dist:
        raise NoRoute("those points are on trails that do not connect")
    path, used, node = [], {}, goal
    while node != start:
        u, cs, tid, L = prev[node]
        path = list(cs) + path[1:] if path else list(cs)
        used[tid] = used.get(tid, 0.0) + L
        node = u
    lon, lat = transform_xy([p[0] for p in path], [p[1] for p in path], srid, 4326)
    coords = [[round(float(x), 7), round(float(y), 7)] for x, y in zip(lon, lat)]
    dedup = [coords[0]] + [c for i, c in enumerate(coords[1:], 1) if c != coords[i - 1]]
    return {
        "coords": dedup,
        "length_m": round(dist[goal], 1),
        "trails": [{"id": t, "length_m": round(L, 1)} for t, L in sorted(used.items(), key=lambda kv: -kv[1])],
        "start_offset_m": round(sa[3], 1),
        "end_offset_m": round(sb[3], 1),
    }
