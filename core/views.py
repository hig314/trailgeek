import json

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import connection
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from . import access
from .forms import TrackUploadForm
from .models import Alignment, DemSource, Trail, Track
from .track_import import create_track_from_gpx
from .gpx import GpxError


def home(request):
    return render(
        request,
        "core/home.html",
        {"can_edit": access.is_trail_editor(request.user)},
    )


def healthz(request):
    """Liveness plus a database round-trip, for the uptime check and deploys."""
    with connection.cursor() as cur:
        cur.execute("SELECT PostGIS_Lib_Version()")
        postgis = cur.fetchone()[0]
    return JsonResponse({"ok": True, "postgis": postgis})


# ---------------------------------------------------------------------------
# JSON API read by the map. Everything here applies core.access.
# ---------------------------------------------------------------------------

def _geojson(resp):
    r = JsonResponse(resp)
    r["Content-Type"] = "application/geo+json"
    r["Cache-Control"] = "private, max-age=60"
    return r


def api_dems(request):
    """The DEM catalogue as GeoJSON, in the shape the demshade bridge reads."""
    qs = DemSource.objects.filter(enabled=True)
    if not access.is_data_user(request.user):
        qs = qs.filter(gated=False)
    feats = []
    for d in qs:
        feats.append({
            "type": "Feature",
            "geometry": json.loads(d.footprint.geojson) if d.footprint else None,
            "properties": d.client_props(),
        })
    return _geojson({"type": "FeatureCollection", "features": feats})


def _feature(obj, kind, props):
    return {
        "type": "Feature",
        "id": f"{kind}:{obj.pk}",
        "geometry": json.loads(obj.geom.geojson),
        "properties": {"kind": kind, "id": obj.pk, **props},
    }


def api_trail(request, slug):
    t = get_object_or_404(Trail.objects.filter(access.visible_q(request.user)).distinct(), slug=slug)
    tracks = Track.objects.filter(trail=t).filter(access.visible_q(request.user)).distinct()
    return _geojson(_feature(t, "trail", {
        "slug": t.slug, "name": t.name, "description": t.description, "status": t.status,
        "region": t.region, "tags": t.tags, "source": t.source, "visibility": t.visibility,
        "length_m": t.length_m, "has_elevation": False,
        "tracks": [{"id": x.pk, "name": x.name, "taken": x.taken_at.date().isoformat() if x.taken_at else None}
                   for x in tracks],
    }))


def api_track(request, pk):
    t = get_object_or_404(Track.objects.filter(access.visible_q(request.user)).distinct(), pk=pk)
    return _geojson(_feature(t, "track", {
        "name": t.name, "description": t.description, "device": t.device,
        "taken_at": t.taken_at.isoformat() if t.taken_at else None,
        "trail": {"slug": t.trail.slug, "name": t.trail.name} if t.trail else None,
        "visibility": t.visibility, "has_elevation": t.has_elevation, "times": t.times,
        "length_m": t.length_m, "climb_m": t.climb_m, "descent_m": t.descent_m,
        "point_count": t.point_count, "original": bool(t.original),
        "can_edit": bool(request.user.is_authenticated and (request.user == t.owner or request.user.is_superuser)),
    }))


def api_alignment(request, pk):
    a = get_object_or_404(
        Alignment.objects.filter(access.visible_q(request.user, prefix="project__")).distinct().select_related("project"),
        pk=pk,
    )
    return _geojson(_feature(a, "alignment", {
        "name": a.name, "priority": a.priority, "trailhead": a.trailhead, "notes": a.notes,
        "project": {"slug": a.project.slug, "name": a.project.name, "id": a.project.pk},
        "length_m": a.length_m, "runs_uphill": a.runs_uphill, "has_elevation": False,
        "siblings": [{"id": s.pk, "name": s.name, "priority": s.priority}
                     for s in a.project.alignments.exclude(pk=a.pk)],
    }))


# ---------------------------------------------------------------------------
# Uploads
# ---------------------------------------------------------------------------

@login_required
@require_http_methods(["GET", "POST"])
def track_upload(request):
    if not access.is_trail_editor(request.user):
        raise PermissionDenied("You need the trail_editors role to upload tracks.")
    form = TrackUploadForm(request.POST or None, request.FILES or None, user=request.user)
    if request.method == "POST" and form.is_valid():
        try:
            track = create_track_from_gpx(
                form.cleaned_data["file"],
                owner=request.user,
                name=form.cleaned_data["name"],
                trail=form.cleaned_data["trail"],
                visibility=form.cleaned_data["visibility"],
                description=form.cleaned_data["description"],
            )
        except GpxError as e:
            form.add_error("file", f"Could not read this GPX: {e}")
        else:
            return redirect(track.get_absolute_url())
    return render(request, "core/track_upload.html", {"form": form})


def track_download(request, pk):
    """The original file, untouched, for whoever may see the track."""
    t = get_object_or_404(Track.objects.filter(access.visible_q(request.user)).distinct(), pk=pk)
    if not t.original:
        raise Http404
    return FileResponse(t.original.open("rb"), as_attachment=True, filename=t.original.name.rsplit("/", 1)[-1])
