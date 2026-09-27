"""Import line data (existing trails, proposed alignments) from any vector
file GDAL reads: a zipped or bare shapefile, GeoPackage, GeoJSON or KML.

Used by `manage.py import_lines` and by the /import/ page, so the rules live
in one place. Reading goes through GeoDjango's GDAL bindings (the system
libgdal the image already has for GeoDjango), not a second GDAL copy.

Re-importing the same file updates rows in place, keyed by `<file stem>#<fid>`,
so foreign keys to them (a leg following a trail) survive a refresh. Rows
from that file that are no longer in it are deleted (trails) or left alone
(alignments, which may have been edited since).

Field mapping is by name, case-insensitive, so the Kachemak trails map
(200111_TRAILS: Name, Symbol, descriptio, Area, Builder, Default) and the
scouting plans GeoPackage (Name, Type) both import without configuration.
"""
import os
import re
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from django.contrib.gis.gdal import DataSource, GDALException
from django.contrib.gis.geos import LineString, MultiLineString
from django.db import transaction
from django.utils.text import slugify

from .models import Alignment, Leg, Project, Trail, Visibility

READABLE = (".shp", ".gpkg", ".geojson", ".json", ".kml", ".gml", ".fgb")

# Trail map "Symbol" -> Trail.trail_class. Anything else becomes "other".
_CLASS = {
    "major": Trail.TrailClass.MAJOR, "regular": Trail.TrailClass.REGULAR,
    "route": Trail.TrailClass.ROUTE, "ski": Trail.TrailClass.SKI,
    "sidewalk": Trail.TrailClass.SIDEWALK, "abandoned": Trail.TrailClass.ABANDONED,
}
# Scouting "Type" -> Leg.kind. Everything proposed is new construction
# unless it says it restores an old line.
_LEG_KIND = {"restored": Leg.Kind.RESTORE, "restore": Leg.Kind.RESTORE, "reroute": Leg.Kind.REROUTE}


class ImportError_(ValueError):
    pass


@dataclass
class Report:
    created: int = 0
    updated: int = 0
    deleted: int = 0
    skipped: int = 0
    messages: list = field(default_factory=list)
    project: object = None

    def __str__(self):
        s = f"{self.created} created, {self.updated} updated, {self.deleted} removed, {self.skipped} skipped"
        if self.project is not None:
            s += f" (project '{self.project.name}')"
        return s


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------

def _unzip(path, into):
    """Extract a zip's vector files flat into `into`, skipping macOS resource
    forks. Flattening to basenames is also what keeps `../` out."""
    with zipfile.ZipFile(path) as z:
        for info in z.infolist():
            name = info.filename
            base = os.path.basename(name)
            if info.is_dir() or "__MACOSX" in name or base.startswith("._") or not base:
                continue
            with z.open(info) as src, open(os.path.join(into, base), "wb") as dst:
                dst.write(src.read())
    found = sorted(p for p in Path(into).iterdir() if p.suffix.lower() in READABLE)
    if not found:
        raise ImportError_("the zip holds no shapefile, GeoPackage, GeoJSON or KML")
    return found


def _field_map(layer):
    return {f.lower(): f for f in layer.fields}


def _get(feat, fmap, *names):
    for n in names:
        real = fmap.get(n.lower())
        if real is None:
            continue
        try:
            v = feat.get(real)
        except (GDALException, IndexError):
            continue
        if isinstance(v, str):
            v = v.strip()
        if v not in (None, ""):
            return v
    return None


def _lines(feat, layer):
    """The feature's geometry as a list of 2D LineStrings in EPSG:4326."""
    try:
        g = feat.geom
    except GDALException:                  # a feature with no geometry at all
        return []
    if g is None or g.empty:
        return []
    if layer.srs is not None:
        g.transform(4326)
    if hasattr(g, "set_3d"):
        g.set_3d(False)
    else:                                  # Django < 5.1
        g.coord_dim = 2
    geos = g.geos
    geos.srid = 4326
    if isinstance(geos, LineString):
        return [geos] if len(geos) >= 2 else []
    if isinstance(geos, MultiLineString):
        merged = geos.merged                 # join parts that touch
        if isinstance(merged, LineString):
            return [merged]
        return [ls for ls in merged if len(ls) >= 2]
    return []


def read_features(path):
    """Yield (stem, fid, feature, layer) for every line feature in the file
    (every layer of it). Zip files are unpacked to a temporary directory."""
    path = str(path)
    tmp = None
    try:
        if path.lower().endswith(".zip"):
            tmp = tempfile.TemporaryDirectory()
            files = _unzip(path, tmp.name)
        else:
            files = [Path(path)]
        for f in files:
            try:
                ds = DataSource(str(f))
            except GDALException as e:
                raise ImportError_(f"GDAL cannot read {f.name}: {e}") from e
            for layer in ds:
                for i, feat in enumerate(layer):
                    fid = feat.fid if feat.fid is not None and feat.fid >= 0 else i
                    yield f.stem, fid, feat, layer
    finally:
        if tmp is not None:
            tmp.cleanup()


# ---------------------------------------------------------------------------
# Trails
# ---------------------------------------------------------------------------

def _unique_slug(base, taken):
    base = (slugify(base) or "trail")[:100]
    slug, n = base, 2
    while slug in taken:
        slug = f"{base}-{n}"
        n += 1
    taken.add(slug)
    return slug


@transaction.atomic
def import_trails(path, owner=None, source_label=None, dry_run=False):
    """Upsert one Trail per feature. Symbol -> class; Default=Hidden ->
    visible to signed-in collaborators only (the source map kept those off
    its public view, e.g. private roads); Abandoned -> historic."""
    rep = Report()
    seen = set()
    taken = set(Trail.objects.values_list("slug", flat=True))
    stems = set()
    for stem, fid, feat, layer in read_features(path):
        stems.add(stem)
        fmap = _field_map(layer)
        lines = _lines(feat, layer)
        if not lines:
            rep.skipped += 1
            continue
        key = f"{stem}#{fid}"
        seen.add(key)
        symbol = (_get(feat, fmap, "symbol", "class", "type") or "").lower()
        tclass = _CLASS.get(symbol, Trail.TrailClass.OTHER if symbol else Trail.TrailClass.REGULAR)
        area = _get(feat, fmap, "area", "region") or ""
        name = _get(feat, fmap, "name", "trail_name", "trailname")
        hidden = (_get(feat, fmap, "default", "display") or "").lower() == "hidden"
        fields = {
            "name": (name or f"Unnamed {Trail.TrailClass(tclass).label.lower()}" + (f", {area}" if area else ""))[:200],
            "description": _get(feat, fmap, "descriptio", "description", "desc", "notes") or "",
            "trail_class": tclass,
            "status": Trail.Status.HISTORIC if tclass == Trail.TrailClass.ABANDONED else Trail.Status.EXISTING,
            "region": str(area)[:100],
            "builder": str(_get(feat, fmap, "builder", "maintainer") or "")[:100],
            "source": (source_label or stem)[:300],
            "visibility": Visibility.GATED if hidden else Visibility.PUBLIC,
            "geom": MultiLineString(*lines, srid=4326),
        }
        if dry_run:
            rep.created += 1
            continue
        t = Trail.objects.filter(source_key=key).first()
        if t is None:
            base = name or f"{area or 'trail'} {tclass} {fid}"
            t = Trail(source_key=key, slug=_unique_slug(base, taken), owner=owner)
            rep.created += 1
        else:
            rep.updated += 1
        for k, v in fields.items():
            setattr(t, k, v)
        t.save()
    if not dry_run and stems:
        gone = Trail.objects.filter(source_key__regex=r"^(%s)#" % "|".join(re.escape(s) for s in stems)).exclude(
            source_key__in=seen)
        rep.deleted = gone.count()
        gone.delete()
    if not dry_run:
        transaction.on_commit(_bump_tiles)
    return rep


# ---------------------------------------------------------------------------
# Proposed alignments
# ---------------------------------------------------------------------------

@transaction.atomic
def import_alignments(path, project=None, project_name=None, owner=None,
                      visibility=Visibility.PRIVATE, dry_run=False, evaluate=True):
    """One Alignment (with a single leg) per line, into `project` or a new
    project called `project_name`. A new project is private unless told
    otherwise: proposed routes can cross land whose owners have not been
    asked yet."""
    rep = Report()
    if project is None:
        if not project_name:
            project_name = f"Imported {Path(str(path)).stem}"
        slug = slugify(project_name)[:50] or "project"
        project = Project.objects.filter(slug=slug).first()
        if project is None and not dry_run:
            project = Project.objects.create(slug=slug, name=project_name, owner=owner, visibility=visibility)
    rep.project = project
    for stem, fid, feat, layer in read_features(path):
        fmap = _field_map(layer)
        lines = _lines(feat, layer)
        if not lines:
            rep.skipped += 1
            continue
        ftype = _get(feat, fmap, "type", "kind", "status") or ""
        kind = _LEG_KIND.get(str(ftype).lower(), Leg.Kind.NEW)
        name = _get(feat, fmap, "name", "title")
        base_name = name or (f"{ftype} line {fid}" if ftype else f"Line {fid}")
        src_len = _get(feat, fmap, "length (m)", "length_m", "length")
        notes = "; ".join(x for x in (f"Type: {ftype}" if ftype else "",
                                       f"source length {src_len} m" if src_len else "") if x)
        for part, ls in enumerate(lines, 1):
            src = f"{stem}#{fid}" + (f".{part}" if len(lines) > 1 else "")
            aname = (base_name + (f" (part {part})" if len(lines) > 1 else ""))[:200]
            if dry_run:
                rep.created += 1
                continue
            a = Alignment.objects.filter(project=project, source=src).first()
            if a is None:
                a = Alignment(project=project, source=src)
                rep.created += 1
            else:
                rep.updated += 1
            a.name = aname
            a.notes = notes
            a.save()
            a.legs.all().delete()
            Leg.objects.create(alignment=a, order=0, kind=kind, name="", geom=ls)
            a.rebuild_from_legs(save=False)
            if evaluate:
                a.evaluation_status = Alignment.EvalStatus.QUEUED
                transaction.on_commit(lambda pk=a.pk: _queue(pk))
            a.save()
    if not dry_run:
        transaction.on_commit(_bump_tiles)
    return rep


def _queue(pk):
    from .tasks import evaluate_saved
    evaluate_saved(pk)


def _bump_tiles():
    from .tiles import bump
    bump()
