"""Turn an uploaded GPX file into a Track row. Kept out of views.py so the
same path serves a management command or a Huey task later (KML, GeoJSON
and CSV readers plug in beside `parse_gpx`)."""
from django.contrib.gis.geos import LineString
from django.db import transaction

from .gpx import parse_gpx
from .models import Track


def create_track_from_gpx(uploaded, owner, name="", trail=None, visibility="public", description=""):
    """`uploaded` is a Django UploadedFile. Raises GpxError on bad input."""
    data = uploaded.read()
    parsed = parse_gpx(data)
    pts = parsed["points"]
    has_ele = any(p[2] is not None for p in pts)
    times = [p[3] for p in pts]
    first_time = next((t for t in times if t is not None), None)
    coords = [(lon, lat, (ele if ele is not None else 0.0)) for lon, lat, ele, _ in pts]
    if has_ele:
        # Fill the odd missing elevation from its neighbour so the Z column is
        # continuous; a whole file without <ele> stays flagged has_elevation=False.
        last = next(c[2] for c in coords if c[2] is not None)
        for i, (lon, lat, ele) in enumerate(coords):
            if pts[i][2] is None:
                coords[i] = (lon, lat, last)
            else:
                last = ele
    rel_times = None
    if first_time is not None:
        rel_times = [round((t - first_time).total_seconds()) if t is not None else None for t in times]

    track = Track(
        name=name or parsed["name"] or uploaded.name.rsplit(".", 1)[0],
        description=description,
        trail=trail,
        device=parsed["device"][:100],
        taken_at=first_time,
        geom=LineString(coords, srid=4326),
        has_elevation=has_ele,
        times=rel_times,
        owner=owner,
        visibility=visibility,
    )
    uploaded.seek(0)
    with transaction.atomic():
        track.original.save(uploaded.name, uploaded, save=False)
        track.save()
    return track
