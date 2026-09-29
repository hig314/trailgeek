"""Glue between the models and trailgeek_analysis: choose the DEMs, resample
an alignment's legs in its UTM zone, sample terrain (core.dem), evaluate,
and store the result. Called from the Huey tasks in core.tasks only.

The same function serves the live evaluation while an alignment is being
edited (legs sent from the browser, nothing saved) and the stored
evaluation of a saved alignment, so what the editor shows is what gets
saved.
"""
import numpy as np
from django.contrib.gis.geos import LineString, MultiLineString
from django.utils import timezone

from trailgeek_analysis import evaluate, resample_legs
from trailgeek_analysis.evaluate import spacing_m

from . import geo
from .dem import CogSource, TerrariumSource, TileSource, sample_stack, transform_xy
from .models import DEFAULT_PROJECT_SETTINGS, DemSource

MAX_SAMPLES_LIVE = 20000      # keeps a live evaluation of a long route to a few seconds
MAX_SAMPLES_SAVED = 80000     # ~120 km at the default 5 ft spacing


def choose_sources(line, project=None):
    """DEM stack for a line (a GEOS geometry in 4326): the project's DEM if
    it has one, then every enabled, public lidar survey whose footprint
    touches the line, finest first, newest first; then the baked 3DEP
    context when the catalogue serves it as tiles; then Terrarium."""
    out, seen = [], set()

    def add_lidar(d):
        if d.slug in seen or not d.cog_url:
            return
        seen.add(d.slug)
        out.append(CogSource(d.slug, d.cog_url, d.title, res=d.native_res_m, bounds=d.bounds))

    if project is not None and project.dem_id and project.dem.kind == DemSource.Kind.LIDAR:
        add_lidar(project.dem)
    qs = (DemSource.objects.filter(kind=DemSource.Kind.LIDAR, enabled=True, gated=False)
          .exclude(cog_url="").filter(footprint__intersects=line)
          .order_by("native_res_m", "-year"))
    for d in qs:
        add_lidar(d)
    # The baked context is a PMTiles archive; the worker reads it through
    # the tile Worker's per-tile URLs, so it is only used when those exist.
    for c in (DemSource.objects.filter(kind=DemSource.Kind.CONTEXT, enabled=True,
                                       encoding=DemSource.Encoding.MAPBOX)
              .exclude(tiles_url="").order_by("slug")):
        out.append(TileSource(c.slug, c.title, c.tiles_url, c.max_zoom, data_res=c.native_res_m,
                              encoding="mapbox"))
    ctx = DemSource.objects.filter(slug="terrarium", enabled=True).first()
    if ctx is None and not DemSource.objects.filter(slug="terrarium").exists():
        out.append(TerrariumSource())           # catalogue not imported: still evaluate
    elif ctx is not None:
        out.append(TerrariumSource(key=ctx.slug, title=ctx.title, url=ctx.tiles_url or TerrariumSource().url))
    return out


def run(legs, settings=None, project=None, live=False, sources=None):
    """Evaluate a list of legs, each {"coords": [[lon, lat], ...], "kind",
    "effort_factor", "name"}. Returns the trailgeek_analysis result plus
    `sources` and `eval_srid`."""
    settings = {**DEFAULT_PROJECT_SETTINGS, **(settings or {})}
    lines = [LineString(leg["coords"], srid=4326) for leg in legs if len(leg["coords"]) >= 2]
    if not lines:
        raise ValueError("nothing to evaluate: no leg has two points")
    whole = MultiLineString(*lines, srid=4326)
    c = whole.centroid
    srid = geo.utm_srid(c.x, c.y)
    legs_xy = []
    for leg in legs:
        cs = np.asarray(leg["coords"], float)
        if len(cs) < 2:
            legs_xy.append(np.zeros((0, 2)))
            continue
        x, y = transform_xy(cs[:, 0], cs[:, 1], 4326, srid)
        legs_xy.append(np.column_stack([x, y]))
    total = sum(float(np.hypot(*np.diff(xy, axis=0).T).sum()) for xy in legs_xy if len(xy) >= 2)
    cap = MAX_SAMPLES_LIVE if live else MAX_SAMPLES_SAVED
    spacing = max(spacing_m(settings), total / cap)
    r = resample_legs(legs_xy, spacing)
    if sources is None:
        sources = choose_sources(whole, project)
    z, gx, gy, src, warnings = sample_stack(r["x"], r["y"], srid, sources)
    lon, lat = transform_xy(r["x"], r["y"], srid, 4326)
    lidar_idx = [i for i, s in enumerate(sources) if s.kind == "lidar"]
    out = evaluate(r["d"], r["leg"], r["heading"], z, gx, gy, src=src,
                   legs=[{"kind": l.get("kind", "new"), "effort_factor": l.get("effort_factor", 1.0),
                          "name": l.get("name", "")} for l in legs],
                   settings=settings, lidar_sources=lidar_idx, lon=lon, lat=lat)
    used = set(int(s) for s in np.unique(src) if s >= 0)
    out["sources"] = [{"index": i, "key": s.key, "title": s.title, "kind": s.kind,
                       "res_m": round(s.res, 2), "used": i in used} for i, s in enumerate(sources)]
    out["eval_srid"] = srid
    out["spacing_m"] = round(spacing, 3)
    out["live"] = live
    out["warnings"] = warnings
    return out


def legs_payload(alignment):
    return [{"coords": list(leg.geom.coords), "kind": leg.kind, "effort_factor": leg.effort_factor,
             "name": leg.name, "id": leg.pk} for leg in alignment.legs.order_by("order")]


def evaluate_alignment(alignment):
    """Evaluate a saved alignment and store the result on it and its legs."""
    from .models import Alignment
    legs = list(alignment.legs.order_by("order"))
    try:
        result = run(legs_payload(alignment), alignment.project.effective_settings(), alignment.project)
    except Exception as e:                     # stored for the panel; the task logs the traceback
        Alignment.objects.filter(pk=alignment.pk).update(
            evaluation_status=Alignment.EvalStatus.FAILED, evaluation_error=str(e)[:2000])
        raise
    for leg, row in zip(legs, result["legs"]):
        leg.stats = {k: v for k, v in row.items() if k not in ("grade_bands", "slope_bands", "tsa_bands")}
        leg.save(update_fields=["stats"])
    Alignment.objects.filter(pk=alignment.pk).update(
        evaluation=result, headline=headline(result), evaluation_status=Alignment.EvalStatus.DONE,
        evaluation_error="", evaluated_at=timezone.now(), runs_uphill=result["summary"].get("runs_uphill"))
    return result


def headline(ev):
    """The handful of numbers the compare table and the editor's
    now-vs-saved strip show, from a full evaluation result."""
    if not ev:
        return None
    s = ev.get("summary") or {}
    go = s.get("grade_over") or {}
    tu = s.get("tsa_under") or {}
    so = s.get("slope_over") or {}
    thr = ev.get("thresholds") or {}
    g = thr.get("grade_%") or []
    t = thr.get("TSA") or []
    sl = thr.get("slope_%") or []
    steep = str(g[1]) if len(g) > 1 else (str(g[0]) if g else None)
    vsteep = str(g[2]) if len(g) > 2 else None
    fall = str(t[0]) if t else None
    heavy = str(sl[2]) if len(sl) > 2 else None
    return {
        "length_m": s.get("length_m"), "build_m": s.get("build_m"), "existing_m": s.get("existing_m"),
        "climb_m": s.get("climb_m"), "descent_m": s.get("descent_m"),
        "z_min": s.get("z_min"), "z_max": s.get("z_max"),
        "grade_p50": (s.get("grade") or {}).get("p50"), "grade_p95": (s.get("grade") or {}).get("p95"),
        "steep_threshold": float(steep) if steep else None,
        "steep_pct": (go.get(steep) or {}).get("pct") if steep else None,
        "steep_longest_m": (go.get(steep) or {}).get("longest_m") if steep else None,
        "very_steep_threshold": float(vsteep) if vsteep else None,
        "very_steep_pct": (go.get(vsteep) or {}).get("pct") if vsteep else None,
        "fall_line_threshold": float(fall) if fall else None,
        "fall_line_pct": (tu.get(fall) or {}).get("pct") if fall else None,
        "heavy_slope_threshold": float(heavy) if heavy else None,
        "heavy_slope_pct": (so.get(heavy) or {}).get("pct") if heavy else None,
        "construct_days": s.get("construct_days"), "maintain_days": s.get("maintain_days"),
        "lidar_pct": s.get("lidar_pct"), "coverage_pct": s.get("coverage_pct"),
    }
