"""Small geometry helpers shared by models, views and the GPX importer.

Lengths are measured in a local UTM zone rather than on the spheroid so
that the same number comes out of the ORM, the evaluator (Phase 2, which
works in the DEM's UTM CRS) and the client. At trail scale the difference
between UTM and geodesic length is well under 0.1 %.
"""
import math

from django.contrib.gis.geos import GEOSGeometry


def utm_srid(lon, lat):
    """EPSG code of the WGS84 UTM zone containing (lon, lat).

    32601..32660 north, 32701..32760 south. The NAD83(2011) zones used by the
    lidar archives (EPSG:6334/6335) differ from these by a datum shift of a
    metre or so, which does not matter for a length.
    """
    zone = int(math.floor((lon + 180) / 6)) % 60 + 1
    return (32600 if lat >= 0 else 32700) + zone


def local_utm(geom):
    """Return a copy of `geom` (SRID 4326) projected to its local UTM zone."""
    c = geom.centroid
    out = geom.clone()
    out.transform(utm_srid(c.x, c.y))
    return out


def length_m(geom):
    """Planar length in metres of a 4326 line geometry, via its UTM zone."""
    if geom is None or geom.empty:
        return 0.0
    return float(local_utm(geom).length)


def climb_descent(zs):
    """Sum of positive and negative elevation steps along a Z sequence.

    Raw lidar or GPS climb is inflated by noise; docs/TRAIL_ANALYSIS.md §1.6
    says the smoothed figure is the one to trust. This raw version is what
    the upload summary shows until the Phase 2 evaluator replaces it.
    """
    up = down = 0.0
    prev = None
    for z in zs:
        if z is None:
            continue
        if prev is not None:
            d = z - prev
            if d > 0:
                up += d
            else:
                down -= d
        prev = z
    return up, down


def as_geos(obj, srid=4326):
    if isinstance(obj, GEOSGeometry):
        return obj
    return GEOSGeometry(obj, srid=srid)
