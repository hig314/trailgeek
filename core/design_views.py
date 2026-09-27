"""The trail design API: alignments made of legs, their live and saved
evaluations, routing along existing trails, projects, and the import page.

Nothing here reads rasters: evaluations are Huey jobs (core.tasks), and
these views only enqueue them or read their results. Access follows
core.access: anyone may read what they can see; editing a project's
alignments needs `can_edit_project`.
"""
import json
import math
import os
import re
import tempfile

from django.contrib.auth.decorators import login_required
from django.contrib.gis.db.models import Extent
from django.contrib.gis.geos import LineString
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.utils.text import slugify
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from . import access, network, tasks, tiles
from .evaluation import headline
from .forms import LineImportForm
from .importers import ImportError_, import_alignments, import_trails
from .models import Alignment, Leg, Project, Trail, Visibility

MAX_VERTICES = 50000
MAX_LEGS = 500
JOIN_TOLERANCE_M = 0.5
_TASK_ID = re.compile(r"^[0-9a-f-]{32,36}$")


def _bad(msg, status=400):
    return JsonResponse({"error": msg}, status=status)


def _body(request):
    try:
        return json.loads(request.body or b"{}")
    except ValueError:
        return None


def _metres(a, b):
    lat = math.radians((a[1] + b[1]) / 2)
    return 6371008.8 * math.hypot(math.radians(b[0] - a[0]) * math.cos(lat), math.radians(b[1] - a[1]))


# ---------------------------------------------------------------------------
# Serialisation
# ---------------------------------------------------------------------------

def _leg_json(leg):
    st = leg.stats or {}
    return {
        "id": leg.pk, "order": leg.order, "name": leg.name, "kind": leg.kind,
        "trail": {"id": leg.trail.pk, "slug": leg.trail.slug, "name": leg.trail.name} if leg.trail_id else None,
        "effort_factor": leg.effort_factor, "notes": leg.notes, "length_m": round(leg.length_m, 1),
        "coords": [[round(x, 7), round(y, 7)] for x, y in leg.geom.coords],
        "stats": {k: st.get(k) for k in ("length_m", "climb_m", "descent_m", "grade", "tsa", "slope",
                                        "grade_over", "tsa_under", "effort", "lidar_pct")} if st else None,
    }


def alignment_json(a, user):
    legs = list(a.legs.select_related("trail").order_by("order"))
    # Siblings' evaluations can be ~100 KB each: read only their headlines.
    siblings = a.project.alignments.exclude(pk=a.pk).defer("evaluation").order_by("priority", "name")
    return {
        "type": "Feature",
        "id": f"alignment:{a.pk}",
        "geometry": json.loads(a.geom.geojson) if a.geom else None,
        "properties": {
            "kind": "alignment", "id": a.pk, "name": a.name, "priority": a.priority,
            "trailhead": a.trailhead, "notes": a.notes, "source": a.source,
            "project": {"slug": a.project.slug, "name": a.project.name, "id": a.project.pk,
                        "visibility": a.project.visibility},
            "parent": {"id": a.parent_id, "name": a.parent.name} if a.parent_id else None,
            "length_m": a.length_m, "runs_uphill": a.runs_uphill, "has_elevation": False,
            "legs": [_leg_json(leg) for leg in legs],
            "evaluation_status": a.evaluation_status, "evaluation_error": a.evaluation_error,
            "evaluated_at": a.evaluated_at.isoformat() if a.evaluated_at else None,
            "evaluation": a.evaluation, "headline": a.headline or headline(a.evaluation),
            "can_edit": access.can_edit_project(user, a.project),
            "siblings": [{"id": s.pk, "name": s.name, "priority": s.priority, "parent_id": s.parent_id,
                          "length_m": round(s.length_m, 1), "headline": s.headline}
                         for s in siblings],
        },
    }


def _visible_alignment(user, pk):
    return get_object_or_404(
        Alignment.objects.filter(access.visible_q(user, prefix="project__")).distinct()
        .select_related("project", "parent"), pk=pk)


def _editable(user, a):
    if not access.can_edit_project(user, a.project):
        raise PermissionDenied("You cannot edit alignments in this project.")


# ---------------------------------------------------------------------------
# Legs in, validated
# ---------------------------------------------------------------------------

def clean_legs(user, raw):
    """Validate legs posted by the editor. Returns a list of dicts ready to
    save, or raises ValueError with a message for the user. Consecutive legs
    must meet (within JOIN_TOLERANCE_M; the joint is then made exact)."""
    if not isinstance(raw, list) or not raw:
        raise ValueError("an alignment needs at least one leg")
    if len(raw) > MAX_LEGS:
        raise ValueError(f"more than {MAX_LEGS} legs")
    out, nverts = [], 0
    kinds = {k for k, _ in Leg.Kind.choices}
    for i, leg in enumerate(raw):
        cs = leg.get("coords") if isinstance(leg, dict) else None
        if not isinstance(cs, list) or len(cs) < 2:
            raise ValueError(f"leg {i + 1} needs at least two points")
        try:
            cs = [[float(c[0]), float(c[1])] for c in cs]
        except (TypeError, ValueError, IndexError):
            raise ValueError(f"leg {i + 1} has a malformed coordinate")
        if not all(-180 <= x <= 180 and -90 <= y <= 90 for x, y in cs):
            raise ValueError(f"leg {i + 1} has a coordinate outside lon/lat range")
        nverts += len(cs)
        kind = leg.get("kind") or Leg.Kind.NEW
        if kind not in kinds:
            raise ValueError(f"leg {i + 1}: unknown kind {kind!r}")
        trail = None
        if leg.get("trail_id"):
            trail = Trail.objects.filter(access.visible_q(user)).filter(pk=leg["trail_id"]).first()
        try:
            factor = float(leg.get("effort_factor", 1.0))
        except (TypeError, ValueError):
            raise ValueError(f"leg {i + 1}: effort factor must be a number")
        if not 0 <= factor <= 100:
            raise ValueError(f"leg {i + 1}: effort factor must be between 0 and 100")
        if out:
            gap = _metres(out[-1]["coords"][-1], cs[0])
            if gap > JOIN_TOLERANCE_M:
                raise ValueError(f"legs {i} and {i + 1} do not meet (gap {gap:.1f} m)")
            cs[0] = list(out[-1]["coords"][-1])
        out.append({"coords": cs, "kind": kind, "trail": trail, "effort_factor": factor,
                    "name": str(leg.get("name") or "")[:200], "notes": str(leg.get("notes") or "")[:5000]})
    if nverts > MAX_VERTICES:
        raise ValueError(f"more than {MAX_VERTICES} vertices")
    return out


def _write_legs(a, legs):
    a.legs.all().delete()
    for i, leg in enumerate(legs):
        Leg.objects.create(alignment=a, order=i, kind=leg["kind"], trail=leg["trail"],
                           effort_factor=leg["effort_factor"], name=leg["name"], notes=leg["notes"],
                           geom=LineString(leg["coords"], srid=4326))
    a.rebuild_from_legs(save=False)
    a.evaluation_status = Alignment.EvalStatus.QUEUED
    a.evaluation_error = ""
    a.save()
    tiles.bump()
    pk = a.pk
    transaction.on_commit(lambda: tasks.evaluate_saved(pk))


def _apply_meta(a, body):
    for f, n in (("name", 200), ("trailhead", 200), ("notes", 20000)):
        if f in body and body[f] is not None:
            v = str(body[f])[:n]
            if f == "name" and not v.strip():
                raise ValueError("an alignment needs a name")
            setattr(a, f, v)
    if "priority" in body:
        try:
            a.priority = int(body["priority"])
        except (TypeError, ValueError):
            raise ValueError("priority must be a whole number")


# ---------------------------------------------------------------------------
# Alignment endpoints
# ---------------------------------------------------------------------------

@require_GET
def api_alignment(request, pk):
    a = _visible_alignment(request.user, pk)
    r = JsonResponse(alignment_json(a, request.user))
    r["Content-Type"] = "application/geo+json"
    r["Cache-Control"] = "private, no-cache"
    return r


@login_required
@require_POST
def api_alignment_save(request, pk):
    """Replace an alignment's legs (and optionally name, priority,
    trailhead, notes), then queue its evaluation."""
    a = _visible_alignment(request.user, pk)
    _editable(request.user, a)
    body = _body(request)
    if body is None:
        return _bad("body is not JSON")
    try:
        with transaction.atomic():
            _apply_meta(a, body)
            if "legs" in body:
                _write_legs(a, clean_legs(request.user, body["legs"]))
            else:
                a.save()
    except ValueError as e:
        return _bad(str(e))
    a.refresh_from_db()
    return JsonResponse(alignment_json(a, request.user))


@login_required
@require_POST
def api_alignment_create(request):
    """A new alignment in an existing project (project_id) or a new one
    (project_name, private by default). Legs are optional: an empty
    alignment is drawn in the editor."""
    body = _body(request)
    if body is None:
        return _bad("body is not JSON")
    if body.get("project_id"):
        project = Project.objects.filter(access.visible_q(request.user)).filter(pk=body["project_id"]).first()
        if project is None:
            return _bad("no such project", 404)
        if not access.can_edit_project(request.user, project):
            raise PermissionDenied("You cannot add alignments to this project.")
    else:
        if not access.is_trail_editor(request.user):
            raise PermissionDenied("You need the trail_editors role to start a design project.")
        pname = str(body.get("project_name") or "").strip()[:200]
        if not pname:
            return _bad("give a project, or a name for a new one")
        base = slugify(pname)[:45] or "project"
        slug, n = base, 2
        while Project.objects.filter(slug=slug).exists():
            slug, n = f"{base}-{n}", n + 1
        vis = body.get("visibility") if body.get("visibility") in dict(Visibility.choices) else Visibility.PRIVATE
        project = Project.objects.create(slug=slug, name=pname, owner=request.user, visibility=vis)
    a = Alignment(project=project, name="New alignment")
    try:
        with transaction.atomic():
            _apply_meta(a, body)
            a.save()
            if body.get("legs"):
                _write_legs(a, clean_legs(request.user, body["legs"]))
    except ValueError as e:
        return _bad(str(e))
    return JsonResponse(alignment_json(a, request.user), status=201)


@login_required
@require_POST
def api_alignment_duplicate(request, pk):
    """Copy an alignment as a variant (same legs and evaluation, `parent`
    pointing back), so a tweak can be compared against the original."""
    a = _visible_alignment(request.user, pk)
    _editable(request.user, a)
    body = _body(request) or {}
    n = a.project.alignments.filter(parent=a).count() + 2
    with transaction.atomic():
        b = Alignment.objects.create(
            project=a.project, name=str(body.get("name") or f"{a.name} (variant {n})")[:200],
            priority=a.priority, trailhead=a.trailhead, notes=a.notes, parent=a,
            evaluation=a.evaluation, headline=a.headline, evaluation_status=a.evaluation_status,
            evaluated_at=a.evaluated_at)
        for leg in a.legs.order_by("order"):
            Leg.objects.create(alignment=b, order=leg.order, name=leg.name, kind=leg.kind, trail=leg.trail,
                               effort_factor=leg.effort_factor, notes=leg.notes, geom=leg.geom, stats=leg.stats)
        b.rebuild_from_legs()
    tiles.bump()
    return JsonResponse(alignment_json(b, request.user), status=201)


@login_required
@require_POST
def api_alignment_delete(request, pk):
    a = _visible_alignment(request.user, pk)
    _editable(request.user, a)
    project = a.project
    a.delete()
    tiles.bump()
    return JsonResponse({"deleted": pk, "project": project.slug})


@login_required
@require_POST
def api_alignment_evaluate(request, pk):
    """Queue a fresh evaluation of the saved alignment (after a DEM or
    settings change, or for an imported alignment)."""
    a = _visible_alignment(request.user, pk)
    _editable(request.user, a)
    if not a.legs.exists():
        return _bad("the alignment has no legs yet")
    Alignment.objects.filter(pk=a.pk).update(evaluation_status=Alignment.EvalStatus.QUEUED, evaluation_error="")
    transaction.on_commit(lambda: tasks.evaluate_saved(a.pk))
    return JsonResponse({"queued": a.pk}, status=202)


# ---------------------------------------------------------------------------
# Evaluation: live (while editing) and results
# ---------------------------------------------------------------------------

@login_required
@require_POST
def api_evaluate(request):
    """Queue a live evaluation of unsaved legs. Returns the task id to poll,
    or the result straight away when the worker was quick (or in tests)."""
    body = _body(request)
    if body is None:
        return _bad("body is not JSON")
    # Evaluations cost worker time: only people who can edit may queue them.
    project = Project.objects.filter(pk=body.get("project_id")).first() if body.get("project_id") else None
    if not (access.is_trail_editor(request.user) or (project and access.can_edit_project(request.user, project))):
        raise PermissionDenied("Only editors can evaluate alignments.")
    try:
        legs = clean_legs(request.user, body.get("legs"))
    except ValueError as e:
        return _bad(str(e))
    payload = {"legs": [{"coords": l["coords"], "kind": l["kind"], "effort_factor": l["effort_factor"],
                         "name": l["name"]} for l in legs],
               "project_id": body.get("project_id"), "seq": body.get("seq")}
    res = tasks.evaluate_live(payload)
    value = res.get()                      # non-blocking: None until the worker finishes
    if value is None:
        return JsonResponse({"task": res.id, "status": "pending"}, status=202)
    return JsonResponse({"task": res.id, "status": "done", **value})


@login_required
@require_GET
def api_evaluate_result(request, task_id):
    if not _TASK_ID.match(task_id):
        return _bad("bad task id")
    from huey.contrib.djhuey import HUEY
    value = HUEY.result(task_id)
    if value is None:
        return JsonResponse({"task": task_id, "status": "pending"}, status=202)
    return JsonResponse({"task": task_id, "status": "done", **value})


# ---------------------------------------------------------------------------
# Following existing trails
# ---------------------------------------------------------------------------

@require_GET
def api_trail_route(request):
    """?from=lon,lat&to=lon,lat -> the shortest path along visible trails."""
    try:
        a = [float(v) for v in request.GET["from"].split(",")]
        b = [float(v) for v in request.GET["to"].split(",")]
        if len(a) != 2 or len(b) != 2:
            raise ValueError
    except (KeyError, ValueError):
        return _bad("give from=lon,lat and to=lon,lat")
    if _metres(a, b) > 100000:
        return _bad("those points are more than 100 km apart")
    try:
        r = network.route(request.user, a, b)
    except network.NoRoute as e:
        return _bad(str(e), 404)
    names = dict(Trail.objects.filter(pk__in=[t["id"] for t in r["trails"]]).values_list("pk", "name"))
    for t in r["trails"]:
        t["name"] = names.get(t["id"], "")
    return JsonResponse(r)


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------

@require_GET
def api_projects(request):
    """Projects the user may add alignments to (for the "new alignment" form)."""
    qs = Project.objects.filter(access.visible_q(request.user)).distinct().order_by("name")
    return JsonResponse({"projects": [{"id": p.pk, "slug": p.slug, "name": p.name, "visibility": p.visibility}
                                      for p in qs if access.can_edit_project(request.user, p)],
                         "can_create": access.is_trail_editor(request.user)})


@require_GET
def api_project(request, slug):
    p = get_object_or_404(Project.objects.filter(access.visible_q(request.user)).distinct(), slug=slug)
    als = list(p.alignments.defer("evaluation").order_by("priority", "name"))
    ext = p.alignments.exclude(geom=None).aggregate(e=Extent("geom"))["e"]
    return JsonResponse({
        "project": {"id": p.pk, "slug": p.slug, "name": p.name, "description": p.description,
                    "visibility": p.visibility, "dem": p.dem.slug if p.dem_id else None,
                    "can_edit": access.can_edit_project(request.user, p), "bbox": ext},
        "alignments": [{"id": a.pk, "name": a.name, "priority": a.priority, "parent_id": a.parent_id,
                        "length_m": round(a.length_m, 1), "evaluation_status": a.evaluation_status,
                        "legs": a.legs.count(), "headline": a.headline} for a in als],
    })


# ---------------------------------------------------------------------------
# Import page
# ---------------------------------------------------------------------------

@login_required
@require_http_methods(["GET", "POST"])
def import_lines(request):
    if not access.is_trail_editor(request.user):
        raise PermissionDenied("You need the trail_editors role to import data.")
    form = LineImportForm(request.POST or None, request.FILES or None, user=request.user)
    report = target = None
    if request.method == "POST" and form.is_valid():
        f = form.cleaned_data["file"]
        suffix = os.path.splitext(f.name)[1].lower()
        target = form.cleaned_data["target"]
        with tempfile.TemporaryDirectory() as tmp:
            # Keep the uploaded name: it becomes the import key (<stem>#<fid>).
            path = os.path.join(tmp, os.path.basename(f.name).replace(os.sep, "_") or f"upload{suffix}")
            with open(path, "wb") as out:
                for chunk in f.chunks():
                    out.write(chunk)
            try:
                if target == "trails":
                    report = import_trails(path, owner=request.user)
                else:
                    project = form.cleaned_data["project"]
                    if project is not None and not access.can_edit_project(request.user, project):
                        raise PermissionDenied("You cannot add alignments to that project.")
                    report = import_alignments(
                        path, project=project, project_name=form.cleaned_data["project_name"] or None,
                        owner=request.user, visibility=form.cleaned_data["visibility"])
            except ImportError_ as e:
                form.add_error("file", str(e))
    return render(request, "core/import.html", {"form": form, "report": report, "target": target})
