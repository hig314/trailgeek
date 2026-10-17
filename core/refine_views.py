"""The refine tool's endpoints (docs/REFINE_TOOL.md). The work runs in the
Huey worker (core.refine.run_job); these start it, report on it, keep
people's defaults, and turn a chosen candidate into a variant.

    GET/POST /api/refine/settings                 site defaults, mine, effective; save or reset mine
    POST     /api/alignments/<pk>/refine          start a run (or a baseline: the input scored only)
    GET      /api/refine/jobs/<job>/              progress, then the result
    POST     /api/alignments/<pk>/refine/accept   save a candidate as a variant
"""
import re
import uuid

from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from trailgeek_analysis.refine import (DEFAULT_SETTINGS, FACTOR_META, PARAM_META, clean_settings,
                                       merge_settings)
from trailgeek_analysis.refine.settings import FACTOR_ORDER

from . import refine, tasks
from .design_views import _bad, _body, _editable, _visible_alignment, _write_legs, alignment_json, clean_legs
from .models import Alignment, UserSetting

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
        raw.append({"coords": coords, "kind": leg.kind, "trail_id": leg.trail_id,
                    "effort_factor": leg.effort_factor, "name": leg.name, "notes": leg.notes})
    try:
        legs = clean_legs(request.user, raw)
    except ValueError as e:
        return _bad(str(e))
    today = timezone.localdate().isoformat()
    with transaction.atomic():
        b = Alignment.objects.create(
            project=a.project, parent=a, priority=a.priority, trailhead=a.trailhead,
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
