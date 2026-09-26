"""Live vector tiles of trails, tracks and alignments straight from PostGIS
with ST_AsMVT, so an edit in /admin/ or an upload shows on the map on the
next tile fetch, with no rebuild.

One tile carries three layers: `trails`, `tracks`, `alignments`. Each
query applies the same visibility rule as the GeoJSON API (core.access),
and the result is cached per (tile, viewer class) for a short time.
"""
import hashlib

from django.core.cache import cache
from django.db import connection
from django.http import HttpResponse

from . import access

TILE_TTL = 60          # seconds; edits appear within this
MIN_ZOOM = 5
MAX_ZOOM = 18
EXTENT = 4096
BUFFER = 64

_LAYERS = {
    "trails": {
        "table": "core_trail",
        "cols": "t.id, t.slug, t.name, t.status, t.region, t.visibility, t.length_m",
        "order": "t.length_m DESC",
    },
    "tracks": {
        "table": "core_track",
        "cols": "t.id, t.name, t.trail_id, t.visibility, t.length_m, t.has_elevation, "
                "to_char(t.taken_at, 'YYYY-MM-DD') AS taken",
        "order": "t.taken_at DESC NULLS LAST",
    },
    "alignments": {
        "table": "core_alignment",
        "cols": "t.id, t.name, t.priority, t.trailhead, t.project_id, t.length_m",
        "order": "t.priority, t.name",
    },
}


def _viewer_key(user):
    if not user.is_authenticated:
        return "anon"
    return f"u{user.pk}"


def _layer_sql(name, z, x, y, user):
    spec = _LAYERS[name]
    if name == "alignments":
        # Visibility lives on the project; members of the project also see it.
        vis, params = access.visibility_sql(user, "p")
        if user.is_authenticated:
            vis = (f"({vis[1:-1]} OR EXISTS (SELECT 1 FROM core_project_members m "
                   f"WHERE m.project_id = p.id AND m.user_id = %s))")
            params = params + [user.pk]
        join = "JOIN core_project p ON p.id = t.project_id"
    else:
        vis, params = access.visibility_sql(user, "t")
        join = ""
    sql = f"""
        SELECT ST_AsMVT(q, %s, {EXTENT}, 'geom') FROM (
          SELECT {spec['cols']},
                 ST_AsMVTGeom(ST_Transform(ST_Force2D(t.geom), 3857),
                              ST_TileEnvelope(%s, %s, %s), {EXTENT}, {BUFFER}, true) AS geom
          FROM {spec['table']} t {join}
          WHERE t.geom && ST_Transform(ST_TileEnvelope(%s, %s, %s, margin => 0.02), 4326)
            AND {vis}
          ORDER BY {spec['order']}
        ) q
    """
    return sql, [name, z, x, y, z, x, y] + params


def render_tile(z, x, y, user):
    parts = []
    with connection.cursor() as cur:
        for name in _LAYERS:
            sql, params = _layer_sql(name, z, x, y, user)
            cur.execute(sql, params)
            row = cur.fetchone()
            if row and row[0]:
                parts.append(bytes(row[0]))
    return b"".join(parts)


def trails_mvt(request, z, x, y):
    if not (MIN_ZOOM <= z <= MAX_ZOOM) or not (0 <= x < 2**z and 0 <= y < 2**z):
        return HttpResponse(status=404)
    key = "mvt:" + hashlib.sha1(f"{_viewer_key(request.user)}/{z}/{x}/{y}".encode()).hexdigest()
    data = cache.get(key)
    if data is None:
        data = render_tile(z, x, y, request.user)
        cache.set(key, data, TILE_TTL)
    resp = HttpResponse(data, content_type="application/vnd.mapbox-vector-tile")
    resp["Cache-Control"] = f"private, max-age={TILE_TTL}"
    return resp
