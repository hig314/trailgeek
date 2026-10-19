"""The refine tool's endpoints (docs/REFINE_TOOL.md). The work runs in the
Huey worker (core.refine.run_job); these start it, report on it, keep
people's defaults, and turn a chosen candidate into a variant.

    GET/POST /api/refine/settings                 site defaults, mine, effective; save or reset mine
    POST     /api/alignments/<pk>/refine          start a run (or a baseline: the input scored only)
    GET      /api/refine/jobs/<job>/              progress, then the result
    POST     /api/alignments/<pk>/refine/accept   save a candidate as a variant
"""
import copy
import re
import uuid

import numpy as np

from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from trailgeek_analysis.refine import (DEFAULT_SETTINGS, FACTOR_META, PARAM_META, clean_settings,
                                       merge_settings)
from trailgeek_analysis.refine.score import densify, polyline_distance
from trailgeek_analysis.refine.settings import FACTOR_ORDER

from . import geo, refine, tasks
from .dem import transform_xy
from .design_views import _bad, _body, _editable, _visible_alignment, _write_legs, alignment_json, clean_legs
from .models import Alignment, Leg, UserSetting

KEY = "refine"
_JOB = re.compile(r"^[0-9a-f]{32}$")


def _meta():
    return {"factors": [dict(FACTOR_META[f], name=f) for f in FACTOR_ORDER],
            "params": [{"name": k, "label": v[0], "min": v[1], "max": v[2], "integer": v[3]}
                       for k, v in PARAM_META.items()]}


@login_required
@require_http_methods(["GET", "POST"])
def api_refine_settings(request):
    row = UserSetting.objects.filter(user=request.user, key=KEY).first()
    if request.method == "POST":
        body = _body(request)
        if body is None:
            return _bad("body is not JSON")
        if body.get("reset"):
            UserSetting.objects.filter(user=request.user, key=KEY).delete()
            row = None
        else:
            try:
                value = clean_settings(body.get("settings"), partial=True)
            except ValueError as e:
                return _bad(str(e))
            value["params"].pop("seed", None)          # a seed is per run, never a default
            row, _ = UserSetting.objects.update_or_create(user=request.user, key=KEY, defaults={"value": value})
    mine = row.value if row else None
    return JsonResponse({"site": DEFAULT_SETTINGS, "mine": mine, "effective": merge_settings(mine), **_meta()})


def _leg_range(raw, n):
    if not isinstance(raw, list) or not raw:
        raise ValueError("select at least one leg")
    try:
        idx = sorted({int(i) for i in raw})
    except (TypeError, ValueError):
        raise ValueError("legs are given by their index")
    if idx[0] < 0 or idx[-1] >= n:
        raise ValueError("no such leg")
    if idx != list(range(idx[0], idx[-1] + 1)):
        raise ValueError("select legs that follow one another: the refined stretch must be continuous")
    return [idx[0], idx[-1]]


@login_required
@require_POST
def api_alignment_refine(request, pk):
    a = _visible_alignment(request.user, pk)
    _editable(request.user, a)
    body = _body(request)
    if body is None:
        return _bad("body is not JSON")
    mode = body.get("mode", "run")
    if mode not in ("run", "baseline"):
        return _bad("mode is run or baseline")
    try:
        legs = _leg_range(body.get("legs"), a.legs.count())
        settings = clean_settings(body.get("settings"))
    except ValueError as e:
        return _bad(str(e))
    job = uuid.uuid4().hex
    payload = {"alignment_id": a.pk, "legs": legs, "settings": settings, "mode": mode,
               "user_id": request.user.pk}
    refine.put_state(job, {"status": "queued", "user": request.user.pk, "alignment": a.pk, "mode": mode,
                           "legs": legs, "progress": 0.0})
    tasks.refine_job(job, payload)
    return JsonResponse({"job": job}, status=202)


FACTOR_KINDS = ("trail", "track", "align")
MAX_FACTOR_LINE_M = 20000.0
FACTOR_REACH_M = 5.0        # m: terrain read either side of a line scored without a search


@login_required
@require_POST
def api_factors(request):
    """Factors along the line for any line you can see (owner, 2026-10-18):
    a trail, a GPS track or an alignment, scored by the refine tool's
    factors with your refine settings, without a search. Body: {kind, key}.
    Returns a job, read like a refine job; its result's `input` is the line."""
    from .design_views import trail_line
    from .models import Trail, Track
    from . import access
    body = _body(request) or {}
    kind, key = body.get("kind"), str(body.get("key") or "")
    if kind not in FACTOR_KINDS:
        return _bad("kind is trail, track or align")
    mine = UserSetting.objects.filter(user=request.user, key=KEY).first()
    settings = copy.deepcopy(merge_settings(mine.value if mine else None))
    # No search, so read the terrain only just around the line.
    settings["params"]["corridor_m"] = FACTOR_REACH_M
    payload = {"settings": settings, "mode": "baseline", "user_id": request.user.pk}
    if kind == "align":
        try:
            a = _visible_alignment(request.user, int(key))
        except ValueError:
            return _bad("no such alignment", 404)
        n = a.legs.count()
        if not n:
            return _bad("this alignment has no legs yet")
        payload.update({"alignment_id": a.pk, "legs": [0, n - 1]})
        length = a.length_m
    elif kind == "trail":
        t = Trail.objects.filter(access.visible_q(request.user)).filter(slug=key).first()
        if t is None:
            return _bad("no such trail", 404)
        line, _ = trail_line(t)
        payload.update({"line": [list(c[:2]) for c in line.coords], "existing": True})
        length = t.length_m
    else:
        try:
            tr = Track.objects.filter(access.visible_q(request.user)).filter(pk=int(key)).first()
        except ValueError:
            tr = None
        if tr is None:
            return _bad("no such track", 404)
        payload.update({"line": [list(c[:2]) for c in tr.geom.coords], "existing": False})
        length = tr.length_m
    if length > MAX_FACTOR_LINE_M:
        return _bad(f"this line is {length / 1000:.1f} km; factors along the line read lidar for up to "
                    f"{MAX_FACTOR_LINE_M / 1000:.0f} km at a time")
    job = uuid.uuid4().hex
    refine.put_state(job, {"status": "queued", "user": request.user.pk, "alignment": None, "mode": "baseline",
                           "legs": None, "progress": 0.0})
    tasks.refine_job(job, payload)
    return JsonResponse({"job": job}, status=202)


def _job_state(request, job):
    if not _JOB.match(job):
        return None
    st = refine.get_state(job)
    if not st or st.get("user") != request.user.pk:
        return None
    return st


@login_required
@require_GET
def api_refine_job(request, job):
    st = _job_state(request, job)
    if st is None:
        return _bad("no such job (they expire when the worker restarts)", 404)
    out = {k: v for k, v in st.items() if k != "user"}
    return JsonResponse(out, status=200 if st["status"] in ("done", "failed") else 202)


LEAVES_M = 3.0      # a refined existing-trail leg further than this from the trail anywhere is a reroute


def _leaves(trail_geom, coords):
    """Does the line `coords` (lon/lat) go further than LEAVES_M from
    `trail_geom` anywhere? In a local metric frame, densified to 1 m."""
    srid = geo.utm_srid(*trail_geom.centroid.coords)
    tx, ty = transform_xy(*np.asarray(trail_geom.coords, float)[:, :2].T, 4326, srid)
    cx, cy = transform_xy(*np.asarray(coords, float)[:, :2].T, 4326, srid)
    pts = densify(np.column_stack([cx, cy]), 1.0)
    d, _ = polyline_distance(pts[:, 0], pts[:, 1], np.column_stack([tx, ty]))
    return float(d.max()) > LEAVES_M


@login_required
@require_POST
def api_alignment_refine_accept(request, pk):
    a = _visible_alignment(request.user, pk)
    _editable(request.user, a)
    body = _body(request) or {}
    st = _job_state(request, str(body.get("job") or ""))
    if st is None or st.get("alignment") != a.pk or st.get("status") != "done" or st.get("mode") != "run":
        return _bad("no finished run of this alignment to take a candidate from", 404)
    res = st["result"]
    try:
        cand = res["candidates"][int(body.get("candidate", 0))]
    except (IndexError, TypeError, ValueError):
        return _bad("no such candidate")
    if res["signature"] != refine.legs_signature(a):
        return _bad("the alignment has changed since this run: run it again", 409)
    i0, i1 = st["legs"]
    raw = []
    for i, leg in enumerate(a.legs.select_related("trail").order_by("order")):
        coords = [list(c) for c in leg.geom.coords]
        if i0 <= i <= i1:
            new = [list(c) for c in cand["legs"][i - i0]]
            # The stretch's ends are the input's own points, exactly.
            if i == i0:
                new[0] = coords[0]
            if i == i1:
                new[-1] = coords[-1]
            coords = new
        kind = leg.kind
        if i0 <= i <= i1 and kind == Leg.Kind.EXISTING and _leaves(leg.geom, coords):
            kind = Leg.Kind.REROUTE             # off the existing trail: it is a reroute of it now
        raw.append({"coords": coords, "kind": kind, "trail_id": leg.trail_id,
                    "effort_factor": leg.effort_factor, "name": leg.name, "notes": leg.notes})
    try:
        legs = clean_legs(request.user, raw)
    except ValueError as e:
        return _bad(str(e))
    today = timezone.localdate().isoformat()
    with transaction.atomic():
        b = Alignment.objects.create(
            project=a.project, parent=a, trail=a.trail, priority=a.priority, trailhead=a.trailhead,
            name=f"{a.name} refined {today} ({cand['label'].lower()})"[:200],
            notes=a.notes,
            refinement={"from": a.pk, "legs": [i0, i1], "label": cand["label"], "seed": res["seed"],
                        "settings": res["settings"], "input": {"total": res["input"]["total"],
                                                               "contrib": res["input"]["contrib"],
                                                               "stats": res["input"]["stats"]},
                        "candidate": {"total": cand["total"], "contrib": cand["contrib"], "stats": cand["stats"],
                                      "t": cand["t"]},
                        "terrain": res["terrain"], "made": timezone.now().isoformat()})
        _write_legs(b, legs)
    b.refresh_from_db()
    return JsonResponse(alignment_json(b, request.user), status=201)
