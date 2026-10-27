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
from django.db.models import Count
from django.contrib.gis.geos import LineString
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.utils.text import slugify
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from . import access, network, tasks, tiles
from .assemble import AssembleError, assemble
from .evaluation import headline
from .forms import LineImportForm
from .importers import ImportError_, import_alignments, import_trails
from .models import Alignment, Leg, Project, ProjectSection, Trail, Visibility

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
            "section": {"id": a.section_id, "name": a.section.name} if a.section_id else None,
            "assembly": a.assembly,
            "trail": {"slug": a.trail.slug, "name": a.trail.name} if a.trail_id else None,
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


def _project_for(user, body):
    """The project a new alignment goes in: an editable existing one
    (project_id), or a new private one (project_name) for a trail editor."""
    if body.get("project_id"):
        project = Project.objects.filter(access.visible_projects_q(user)).filter(pk=body["project_id"]).first()
        if project is None:
            raise LookupError("no such project")
        if not access.can_edit_project(user, project):
            raise PermissionDenied("You cannot add alignments to this project.")
        return project
    if not access.is_trail_editor(user):
        raise PermissionDenied("You need the trail_editors role to start a design project.")
    pname = str(body.get("project_name") or "").strip()[:200]
    if not pname:
        raise ValueError("give a project, or a name for a new one")
    base = slugify(pname)[:45] or "project"
    slug, n = base, 2
    while Project.objects.filter(slug=slug).exists():
        slug, n = f"{base}-{n}", n + 1
    vis = body.get("visibility") if body.get("visibility") in dict(Visibility.choices) else Visibility.PRIVATE
    return Project.objects.create(slug=slug, name=pname, owner=user, visibility=vis)


def trail_line(trail):
    """A trail's line as one LineString: its parts joined where they meet
    (GEOS line merge). Returns (line, pieces): with several pieces that do
    not meet, the longest, and how many there were."""
    merged = trail.geom.merged
    parts = [merged] if merged.geom_type == "LineString" else list(merged)
    best = max(parts, key=lambda g: g.length)
    return LineString(list(best.coords), srid=4326), len(parts)


def trail_studies(user, trail):
    """The alignments `user` may see that study `trail` (Alignment.trail), as a
    tree: each with its variants under it, so the trail panel lists them
    below the trail (owner, 2026-10-18)."""
    qs = list(Alignment.objects.filter(trail=trail).filter(access.visible_q(user, prefix="project__")).distinct()
              .select_related("project").defer("evaluation").order_by("created"))
    ids = {a.pk for a in qs}
    kids = {}
    for a in qs:
        kids.setdefault(a.parent_id if a.parent_id in ids else None, []).append(a)

    def node(a):
        return {"id": a.pk, "name": a.name, "project": a.project.name, "length_m": round(a.length_m, 1),
                "refined": bool(a.refinement), "variants": [node(b) for b in kids.get(a.pk, [])]}
    return [node(a) for a in kids.get(None, [])]


def study_projects(user, trail):
    """Projects `user` can edit that already have an alignment following
    `trail`: where a refine study of it most likely belongs."""
    if not user.is_authenticated:
        return []
    qs = (Project.objects.filter(access.visible_projects_q(user), alignments__legs__trail=trail).distinct()
          .order_by("name"))
    return [{"id": p.pk, "name": p.name} for p in qs if access.can_edit_project(user, p)]


@login_required
@require_POST
def api_trail_study(request, slug):
    """A refine study of an existing trail (owner, 2026-10-18): a new
    alignment of one existing-trail leg along it, in an editable project or a
    new one. The panel then opens the refine tool on it; a kept candidate is
    a reroute variant, and "Off existing tread" weighs leaving the trail."""
    t = get_object_or_404(Trail.objects.filter(access.visible_q(request.user)).distinct(), slug=slug)
    body = _body(request)
    if body is None:
        return _bad("body is not JSON")
    try:
        project = _project_for(request.user, body)
    except LookupError as e:
        return _bad(str(e), 404)
    except ValueError as e:
        return _bad(str(e))
    section = None
    if body.get("section_id"):
        section = ProjectSection.objects.filter(project=project, pk=body["section_id"]).first()
        if section is None:
            return _bad("no such section in this project", 404)
    line, pieces = trail_line(t)
    name = str(body.get("name") or f"{t.name}: refine study").strip()[:200] or f"{t.name}: refine study"
    a = Alignment(project=project, name=name, trail=t, section=section)
    try:
        with transaction.atomic():
            a.save()
            _write_legs(a, clean_legs(request.user, [{"coords": [list(c[:2]) for c in line.coords], "kind": Leg.Kind.EXISTING,
                                                      "trail_id": t.pk, "name": t.name}]))
    except ValueError as e:
        return _bad(str(e))
    out = alignment_json(a, request.user)
    if pieces > 1:
        out["properties"]["note"] = (f"{t.name} is in {pieces} pieces that do not meet; the study follows the longest. "
                                     "Join the pieces in /admin/ to study the whole trail.")
    return JsonResponse(out, status=201)


@login_required
@require_POST
def api_alignment_create(request):
    """A new alignment in an existing project (project_id) or a new one
    (project_name, private by default). Legs are optional: an empty
    alignment is drawn in the editor."""
    body = _body(request)
    if body is None:
        return _bad("body is not JSON")
    try:
        project = _project_for(request.user, body)
    except LookupError as e:
        return _bad(str(e), 404)
    except ValueError as e:
        return _bad(str(e))
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
            priority=a.priority, trailhead=a.trailhead, notes=a.notes, parent=a, trail=a.trail,
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
    """Every project the user may see, with `can_edit` per project: the
    project picker in the Layers panel lists them all (a public project is
    shareable by its #project= link and findable here without signing in),
    and the "new alignment" form keeps the editable ones."""
    qs = (Project.objects.filter(access.visible_projects_q(request.user)).distinct()
          .select_related("parent").annotate(n_alignments=Count("alignments")).order_by("name"))
    visible = {p.pk for p in qs}   # a public child never names a private parent
    return JsonResponse({"projects": [{"id": p.pk, "slug": p.slug, "name": p.name, "visibility": p.visibility,
                                       "parent": p.parent.slug if p.parent_id in visible else None,
                                       "alignments": p.n_alignments,
                                       "can_edit": access.can_edit_project(request.user, p)} for p in qs],
                         "can_create": access.is_trail_editor(request.user)})


@require_GET
def api_project(request, slug):
    p = get_object_or_404(Project.objects.filter(access.visible_projects_q(request.user)).distinct(), slug=slug)
    als = list(p.alignments.defer("evaluation").select_related("trail").order_by("priority", "name"))
    ext = p.alignments.exclude(geom=None).aggregate(e=Extent("geom"))["e"]
    children = p.children.filter(access.visible_projects_q(request.user)).distinct().order_by("name")
    tracks = p.tracks.filter(access.visible_q(request.user)).distinct().order_by("-taken_at", "name")
    return JsonResponse({
        "project": {"id": p.pk, "slug": p.slug, "name": p.name, "description": p.description,
                    "visibility": p.visibility, "dem": p.dem.slug if p.dem_id else None,
                    "can_edit": access.can_edit_project(request.user, p), "bbox": ext,
                    "parents": [{"slug": q.slug, "name": q.name} for q in p.ancestors()
                                if Project.objects.filter(access.visible_projects_q(request.user), pk=q.pk).exists()],
                    "children": [{"slug": q.slug, "name": q.name} for q in children],
                    "gpx_url": f"/api/projects/{p.slug}.gpx", "zip_url": f"/api/projects/{p.slug}.zip",
                    "geojson_url": f"/api/projects/{p.slug}.geojson"},
        "sections": [{"id": q.pk, "name": q.name, "order": q.order, "notes": q.notes} for q in p.sections.all()],
        "alignments": [{"id": a.pk, "name": a.name, "priority": a.priority, "parent_id": a.parent_id,
                        "section_id": a.section_id, "trail": a.trail.name if a.trail_id else None,
                        "assembled": a.assembly is not None,
                        "length_m": round(a.length_m, 1), "evaluation_status": a.evaluation_status,
                        "legs": a.legs.count(), "headline": a.headline} for a in als],
        "tracks": [{"id": t.pk, "name": t.name, "taken_at": t.taken_at.isoformat() if t.taken_at else None,
                    "length_m": round(t.length_m, 1), "climb_m": round(t.climb_m)} for t in tracks],
    })


# ---------------------------------------------------------------------------
# Sections (owner, 2026-10-26): the parts of a project's route, in order; its
# alignments are each one's options, variants under them.
# ---------------------------------------------------------------------------

def _editable_project(user, slug):
    p = get_object_or_404(Project.objects.filter(access.visible_projects_q(user)).distinct(), slug=slug)
    if not access.can_edit_project(user, p):
        raise PermissionDenied("You cannot edit this project.")
    return p


def _editable_section(user, pk):
    q = get_object_or_404(ProjectSection.objects.select_related("project")
                          .filter(project__in=Project.objects.filter(access.visible_projects_q(user))), pk=pk)
    if not access.can_edit_project(user, q.project):
        raise PermissionDenied("You cannot edit this project.")
    return q


def _renumber(project):
    for n, q in enumerate(project.sections.all()):
        if q.order != n:
            ProjectSection.objects.filter(pk=q.pk).update(order=n)


def _section_json(q):
    return {"id": q.pk, "name": q.name, "order": q.order, "notes": q.notes}


@login_required
@require_POST
def api_section_create(request, slug):
    """A new section at the end of the project's route."""
    p = _editable_project(request.user, slug)
    body = _body(request)
    if body is None:
        return _bad("body is not JSON")
    name = str(body.get("name") or "").strip()[:200]
    if not name:
        return _bad("give the section a name")
    q = ProjectSection.objects.create(project=p, name=name, order=p.sections.count(),
                                      notes=str(body.get("notes") or "")[:5000])
    return JsonResponse(_section_json(q), status=201)


@login_required
@require_POST
def api_section_save(request, pk):
    """Rename a section, change its notes, or move it one place along the
    route (`move`: -1 earlier, +1 later)."""
    q = _editable_section(request.user, pk)
    body = _body(request)
    if body is None:
        return _bad("body is not JSON")
    if "name" in body:
        name = str(body["name"] or "").strip()[:200]
        if not name:
            return _bad("a section needs a name")
        q.name = name
    if "notes" in body:
        q.notes = str(body["notes"] or "")[:5000]
    q.save()
    move = body.get("move")
    if move in (-1, 1):
        with transaction.atomic():
            _renumber(q.project)
            secs = list(q.project.sections.all())
            i = next(n for n, x in enumerate(secs) if x.pk == q.pk)
            j = i + move
            if 0 <= j < len(secs):
                ProjectSection.objects.filter(pk=secs[i].pk).update(order=j)
                ProjectSection.objects.filter(pk=secs[j].pk).update(order=i)
        q.refresh_from_db()
    return JsonResponse(_section_json(q))


@login_required
@require_POST
def api_section_delete(request, pk):
    """Delete a section; its alignments stay in the project, unsorted."""
    q = _editable_section(request.user, pk)
    project = q.project
    q.delete()
    _renumber(project)
    return JsonResponse({"deleted": pk, "project": project.slug})


@login_required
@require_POST
def api_alignment_section(request, pk):
    """Put an alignment in a section (`section_id`, or null for unsorted).
    Its variants that were in its old section go with it."""
    a = _visible_alignment(request.user, pk)
    _editable(request.user, a)
    body = _body(request)
    if body is None:
        return _bad("body is not JSON")
    sid = body.get("section_id")
    section = None
    if sid is not None:
        section = ProjectSection.objects.filter(project=a.project, pk=sid).first()
        if section is None:
            return _bad("no such section in this project", 404)
    old = a.section_id
    kids = {}
    for b in a.project.alignments.only("pk", "parent_id", "section_id"):
        kids.setdefault(b.parent_id, []).append(b)
    move, todo = [a.pk], [a.pk]
    while todo:
        for b in kids.get(todo.pop(), []):
            if b.section_id == old and b.pk not in move:
                move.append(b.pk)
                todo.append(b.pk)
    Alignment.objects.filter(pk__in=move).update(section=section)
    return JsonResponse({"moved": move, "section_id": sid})


# ---------------------------------------------------------------------------
# Assembling one route from one option per section (owner, 2026-10-26)
# ---------------------------------------------------------------------------

def _choices(project, body):
    """[(section, alignment)] in section order, and the forced directions,
    from {"choices": [{"section_id", "alignment_id", "reverse": null|bool}]}."""
    raw = body.get("choices")
    if not isinstance(raw, list) or not raw:
        raise AssembleError("choose an option for at least one section")
    secs = {q.pk: q for q in project.sections.all()}
    out = []
    for c in raw:
        if not isinstance(c, dict):
            raise AssembleError("a choice is malformed")
        q = secs.get(c.get("section_id"))
        if q is None:
            raise AssembleError("no such section in this project")
        a = project.alignments.filter(pk=c.get("alignment_id"), section=q).first()
        if a is None:
            raise AssembleError(f"that alignment is not an option for {q.name}")
        rev = c.get("reverse")
        out.append((q, a, rev if rev in (True, False) else None))
    if len({q.pk for q, _, _ in out}) != len(out):
        raise AssembleError("one option per section")
    out.sort(key=lambda t: (t[0].order, t[0].pk))
    return [(q, a) for q, a, _ in out], [r for _, _, r in out]


@login_required
@require_POST
def api_assemble_preview(request, slug):
    """The legs and the join report for a set of choices, unsaved: the panel
    draws them and evaluates them live."""
    p = _editable_project(request.user, slug)
    body = _body(request)
    if body is None:
        return _bad("body is not JSON")
    try:
        choices, rev = _choices(p, body)
        legs, report = assemble(choices, rev)
    except AssembleError as e:
        return _bad(str(e))
    return JsonResponse({"legs": legs, "report": report})


@login_required
@require_POST
def api_assemble(request, slug):
    """Save an assembled route as a new alignment in the project (`name`;
    `parent_id`: the assembled route this one re-assembles, as a variant)."""
    p = _editable_project(request.user, slug)
    body = _body(request)
    if body is None:
        return _bad("body is not JSON")
    try:
        choices, rev = _choices(p, body)
        legs, report = assemble(choices, rev)
    except AssembleError as e:
        return _bad(str(e))
    parent = None
    if body.get("parent_id"):
        parent = p.alignments.filter(pk=body["parent_id"]).exclude(assembly=None).first()
        if parent is None:
            return _bad("no such assembled route in this project", 404)
    name = str(body.get("name") or "").strip()[:200] or f"{p.name}: whole route"
    a = Alignment(project=p, name=name, parent=parent,
                  assembly={"choices": [{"section_id": q.pk, "alignment_id": al.pk, "reverse": r}
                                        for (q, al), r in zip(choices, rev)], **report})
    try:
        with transaction.atomic():
            a.save()
            _write_legs(a, clean_legs(request.user, legs))
    except ValueError as e:
        return _bad(str(e))
    return JsonResponse(alignment_json(a, request.user), status=201)


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
