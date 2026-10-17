"""Field exports of design projects: GPX for GaiaGPS and phones, GeoJSON of
the legs for building offline map products (MapApp's vector MBTiles via
`ogr2ogr -f MBTILES`, docs/SISTER_PROJECTS.md).

One source of truth: the alignments in the database. Nothing here is
hand-exported from QGIS, so a scout's file is always the saved plan.

Naming: GaiaGPS lists tracks by name, so every track gets a distinct,
sortable name that starts with the alignment, e.g. "Tutka Tusk 2 Link A
(new)". Waypoints mark where legs join and where each alignment starts and
ends, which is what a scout navigates to.
"""
import io
import zipfile

from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404
from django.utils.text import slugify
from django.views.decorators.http import require_GET

from . import access
from .gpx import write_gpx
from .models import Alignment, Leg, Project

# Priority -> Garmin DisplayColor (GaiaGPS honours it on import).
PRIORITY_COLORS = {"preferred": "Red", "alternate": "Blue", "future": "DarkGray"}


def _km(m):
    return f"{m / 1000:.2f} km" if m else ""


def _desc(*parts):
    return "; ".join(str(p) for p in parts if p)


def alignment_tracks(a, legs=None):
    """GPX tracks and waypoints for one alignment: one track per leg when
    there are several (so build efforts stay separate in the field), else one
    track for the alignment."""
    legs = list(legs if legs is not None else a.legs.select_related("trail").order_by("order"))
    label = a.priority_label
    color = PRIORITY_COLORS.get(label, "DarkGray")
    tracks, waypoints = [], []
    if not legs:
        return tracks, waypoints
    if len(legs) == 1:
        leg = legs[0]
        tracks.append({
            "name": a.name, "coords": leg.geom.coords, "color": color, "type": label,
            "desc": _desc(f"{label} alignment", Leg.Kind(leg.kind).label.lower(), _km(a.length_m), a.notes,
                          f"project: {a.project.name}"),
        })
    else:
        for i, leg in enumerate(legs, 1):
            kind = Leg.Kind(leg.kind).label.lower()
            name = f"{a.name} {i} " + (leg.name or (leg.trail.name if leg.trail_id else kind))
            tracks.append({
                "name": name[:100], "coords": leg.geom.coords, "color": color, "type": label,
                "desc": _desc(f"{label} alignment, leg {i} of {len(legs)}", kind, _km(leg.length_m), leg.notes,
                              f"project: {a.project.name}"),
            })
            if i < len(legs):
                x, y = leg.geom.coords[-1][:2]
                waypoints.append({"name": f"{a.name} jct {i}", "lon": x, "lat": y, "sym": "Flag, Blue",
                                  "desc": f"legs {i} and {i + 1} join"})
    start, end = legs[0].geom.coords[0], legs[-1].geom.coords[-1]
    waypoints.insert(0, {"name": f"{a.name} start", "lon": start[0], "lat": start[1], "sym": "Flag, Green",
                         "desc": _desc(a.trailhead and f"from {a.trailhead}", f"{label} alignment")})
    waypoints.append({"name": f"{a.name} end", "lon": end[0], "lat": end[1], "sym": "Flag, Red",
                      "desc": f"{label} alignment"})
    return tracks, waypoints


def _unique_names(alignments):
    """Two alignments with the same name (an imported file may repeat one)
    would be two identical tracks in GaiaGPS, so the repeats get (2), (3)."""
    seen, out = {}, []
    for a in alignments:
        n = seen.get(a.name, 0) + 1
        seen[a.name] = n
        out.append(a.name if n == 1 else f"{a.name} ({n})")
    return out


def _dedupe_waypoints(waypoints):
    """Alignments that share an end (an option A and B) would put two flags
    on one spot; keep one and join the names."""
    merged = {}
    for w in waypoints:
        key = (round(w["lon"], 4), round(w["lat"], 4))
        if key in merged:
            m = merged[key]
            if w["name"] not in m["name"]:
                m["name"] = f"{m['name']} / {w['name']}"
            m["desc"] = _desc(m.get("desc"), w.get("desc")) if w.get("desc") not in (m.get("desc") or "") else m.get("desc")
        else:
            merged[key] = dict(w)
    return list(merged.values())


def project_tracks(project):
    tracks, waypoints = [], []
    als = list(project.alignments.defer("evaluation").order_by("priority", "name"))
    for a, name in zip(als, _unique_names(als)):
        a.name = name  # export name only; not saved
        t, w = alignment_tracks(a)
        tracks += t
        waypoints += w
    return tracks, _dedupe_waypoints(waypoints)


def leg_features(project):
    """One GeoJSON feature per leg, with the attributes a field map shows on
    tap (name, kind, priority, length). Alignments are the user-facing unit,
    so `name` is the alignment name plus the leg position."""
    feats = []
    als = list(project.alignments.defer("evaluation").order_by("priority", "name").prefetch_related("legs__trail"))
    for a, aname in zip(als, _unique_names(als)):
        legs = list(a.legs.all())
        legs.sort(key=lambda l: l.order)
        for i, leg in enumerate(legs, 1):
            name = aname if len(legs) == 1 else f"{aname} {i} " + (leg.name or (leg.trail.name if leg.trail_id else Leg.Kind(leg.kind).label.lower()))
            feats.append({
                "type": "Feature", "id": f"leg:{leg.pk}",
                "geometry": {"type": "LineString", "coordinates": [[round(x, 7), round(y, 7)] for x, y in leg.geom.coords]},
                "properties": {
                    "name": name, "alignment": aname, "alignment_id": a.pk, "leg": i, "legs": len(legs),
                    "kind": leg.kind, "priority": a.priority, "class": a.priority_label,
                    "length_m": round(leg.length_m), "notes": leg.notes or a.notes,
                    "trail": leg.trail.name if leg.trail_id else None,
                    "project": project.name, "source": "trailgeek.org",
                },
            })
    return feats


# ---------------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------------

def _gpx_response(filename, name, tracks, waypoints, desc=None):
    body = write_gpx(name, tracks, waypoints, desc=desc)
    r = HttpResponse(body, content_type="application/gpx+xml")
    r["Content-Disposition"] = f'attachment; filename="{filename}"'
    r["Cache-Control"] = "private, no-cache"
    return r


def _visible_project(user, slug):
    return get_object_or_404(Project.objects.filter(access.visible_projects_q(user)).distinct(), slug=slug)


@require_GET
def api_alignment_gpx(request, pk):
    a = get_object_or_404(Alignment.objects.filter(access.visible_q(request.user, prefix="project__")).distinct()
                          .select_related("project"), pk=pk)
    tracks, waypoints = alignment_tracks(a)
    return _gpx_response(f"{slugify(a.project.name)}-{slugify(a.name) or a.pk}.gpx", a.name, tracks, waypoints,
                         desc=_desc(f"{a.priority_label} alignment", a.notes, f"project: {a.project.name}"))


@require_GET
def api_project_gpx(request, slug):
    p = _visible_project(request.user, slug)
    tracks, waypoints = project_tracks(p)
    return _gpx_response(f"{p.slug}.gpx", p.name, tracks, waypoints,
                         desc=_desc(p.description.splitlines()[0] if p.description else "",
                                    f"{len(tracks)} tracks: preferred alignments red, alternates blue, future grey"))


def project_zip(project):
    """A zip with one GPX per alignment (numbered in priority order, so they
    list in order on a phone) plus the combined file. Bytes."""
    als = list(project.alignments.defer("evaluation").order_by("priority", "name"))
    names = _unique_names(als)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for i, (a, name) in enumerate(zip(als, names), 1):
            a.name = name  # export name only; not saved
            tracks, waypoints = alignment_tracks(a)
            if not tracks:
                continue
            fn = f"{i:02d}_{a.priority_label}_{slugify(name) or a.pk}.gpx"
            z.writestr(fn, write_gpx(name, tracks, waypoints,
                                     desc=_desc(f"{a.priority_label} alignment", a.notes, f"project: {project.name}")))
        tracks, waypoints = project_tracks(project)
        z.writestr(f"{project.slug}_all.gpx", write_gpx(project.name, tracks, waypoints,
                                                        desc=f"all alignments of {project.name}"))
        z.writestr("README.txt", (f"{project.name}: GPX files exported from trailgeek.org.\n\n"
                                  "One file per alignment, numbered in priority order; "
                                  f"{project.slug}_all.gpx holds them all in one file.\n"
                                  "Tracks are coloured red (preferred), blue (alternate) or grey (future) where the app honours "
                                  "Garmin DisplayColor; waypoints mark where legs join, and each alignment's start and end.\n"))
    return buf.getvalue()


@require_GET
def api_project_zip(request, slug):
    p = _visible_project(request.user, slug)
    r = HttpResponse(project_zip(p), content_type="application/zip")
    r["Content-Disposition"] = f'attachment; filename="{p.slug}-gpx.zip"'
    r["Cache-Control"] = "private, no-cache"
    return r


@require_GET
def api_project_geojson(request, slug):
    p = _visible_project(request.user, slug)
    r = JsonResponse({"type": "FeatureCollection", "name": p.slug, "features": leg_features(p)})
    r["Content-Type"] = "application/geo+json"
    r["Content-Disposition"] = f'inline; filename="{p.slug}.geojson"'
    r["Cache-Control"] = "private, no-cache"
    return r
