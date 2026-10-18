"""Project notes: points on the map, each with a note, for those who can
edit the project (core.access.can_edit_project). A public project's notes
stay private; nothing here is in the public exports.
"""
from django.contrib.auth.decorators import login_required
from django.contrib.gis.geos import Point
from django.core.exceptions import PermissionDenied
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_GET, require_POST

from . import access
from .design_views import _bad, _body
from .models import Project, ProjectNote

TEXT_MAX = 5000


def _editable_project(user, slug):
    p = get_object_or_404(Project.objects.filter(access.visible_projects_q(user)).distinct(), slug=slug)
    if not access.can_edit_project(user, p):
        raise PermissionDenied("Project notes are for those who can edit the project.")
    return p


def _editable_note(user, pk):
    n = get_object_or_404(ProjectNote.objects.select_related("project"), pk=pk)
    if not access.can_edit_project(user, n.project):
        raise PermissionDenied("Project notes are for those who can edit the project.")
    return n


def _who(u):
    return (u.get_full_name() or u.get_username()) if u else None


def note_json(n):
    return {"type": "Feature", "id": n.pk,
            "geometry": {"type": "Point", "coordinates": [round(n.geom.x, 7), round(n.geom.y, 7)]},
            "properties": {"id": n.pk, "text": n.text, "author": _who(n.author),
                           "updated_by": _who(n.updated_by),
                           "created": n.created.isoformat(), "updated": n.updated.isoformat()}}


def _clean(body, partial):
    """{"text", "lon", "lat"} -> (text or None, Point or None); ValueError."""
    if body is None:
        raise ValueError("body is not JSON")
    text = body.get("text")
    if text is not None or not partial:
        if not isinstance(text, str) or not text.strip():
            raise ValueError("a note needs some text")
        text = text.replace("\r\n", "\n").strip()
        if len(text) > TEXT_MAX:
            raise ValueError(f"a note is limited to {TEXT_MAX} characters")
    pt = None
    if "lon" in body or "lat" in body or not partial:
        try:
            lon, lat = float(body["lon"]), float(body["lat"])
        except (KeyError, TypeError, ValueError):
            raise ValueError("a note needs lon and lat")
        if not (-180 <= lon <= 180 and -90 <= lat <= 90):
            raise ValueError("lon or lat out of range")
        pt = Point(lon, lat, srid=4326)
    return text, pt


@login_required
@require_GET
def api_project_notes(request, slug):
    p = _editable_project(request.user, slug)
    notes = p.notes.select_related("author", "updated_by")
    return JsonResponse({"type": "FeatureCollection", "features": [note_json(n) for n in notes]})


@login_required
@require_POST
def api_project_note_create(request, slug):
    p = _editable_project(request.user, slug)
    try:
        text, pt = _clean(_body(request), partial=False)
    except ValueError as e:
        return _bad(str(e))
    n = ProjectNote.objects.create(project=p, geom=pt, text=text, author=request.user, updated_by=request.user)
    return JsonResponse(note_json(n), status=201)


@login_required
@require_POST
def api_note_update(request, pk):
    """Change a note's text, its place, or both."""
    n = _editable_note(request.user, pk)
    try:
        text, pt = _clean(_body(request), partial=True)
    except ValueError as e:
        return _bad(str(e))
    if text is not None:
        n.text = text
    if pt is not None:
        n.geom = pt
    n.updated_by = request.user
    n.save()
    return JsonResponse(note_json(n))


@login_required
@require_POST
def api_note_delete(request, pk):
    n = _editable_note(request.user, pk)
    n.delete()
    return JsonResponse({"deleted": pk})
