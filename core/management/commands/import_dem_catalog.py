"""Seed DemSource from landslidescience's public lidar catalogue.

    manage.py import_dem_catalog                 # fetch the live catalogue
    manage.py import_dem_catalog --file cat.geojson
    manage.py import_dem_catalog --dry-run

trailgeek keeps no copy of the lidar itself: each row points at the same R2
archives landslidescience serves (docs/SISTER_PROJECTS.md, "Lidar data").
Rows are upserted by catalogue id, so re-running after a catalogue rebuild
updates URLs and footprints in place. Rows for surveys that have left the
catalogue are disabled, not deleted, because a Project may reference them.

The two regional context DEMs (AWS Terrarium, USGS 3DEP) are ensured on
every run; they cover the ground outside every survey footprint.
"""
import json
import urllib.request

from django.contrib.gis.geos import GEOSGeometry, MultiPolygon, Polygon
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from core.models import DemSource

CATALOG_URL = "https://landslidescience.org/lidar/catalog.geojson"

CONTEXT_ROWS = [
    {
        "slug": "terrarium",
        "title": "AWS Terrain Tiles (Terrarium)",
        "kind": DemSource.Kind.CONTEXT,
        "encoding": DemSource.Encoding.TERRARIUM,
        "tiles_url": "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png",
        "min_zoom": 0,
        "max_zoom": 15,
        "region": "Global",
        "product": "Regional DEM (USGS NED 2 arc-second in Alaska; includes bathymetry)",
        "source": "Mapzen / AWS Open Data Terrain Tiles",
        "source_url": "https://registry.opendata.aws/terrain-tiles/",
        "notes": "Public, no key, CORS open. The map's terrain outside lidar footprints.",
    },
    {
        "slug": "usgs3dep",
        "title": "USGS 3DEP elevation (ImageServer)",
        "kind": DemSource.Kind.CONTEXT,
        "encoding": DemSource.Encoding.IMAGESERVER,
        "tiles_url": "https://elevation.nationalmap.gov/arcgis/rest/services/3DEPElevation/ImageServer",
        "min_zoom": 0,
        "max_zoom": 14,
        "region": "United States",
        "product": "Regional DEM (5 m IfSAR in Alaska)",
        "source": "USGS 3D Elevation Program",
        "source_url": "https://www.usgs.gov/3d-elevation-program",
        "enabled": False,
        "notes": "Live float32 exports per tile; slower than Terrarium. Off by default.",
    },
]

# Catalogue property -> DemSource field, for the straight copies.
_COPY = {
    "title": "title", "year": "year", "region": "region", "product": "product",
    "source": "source", "source_url": "source_url", "horizontal_crs": "horizontal_crs",
    "vertical_datum": "vertical_datum", "native_res_m": "native_res_m",
    "min_zoom": "min_zoom", "max_zoom": "max_zoom", "bounds": "bounds",
    "coverage_km2": "coverage_km2", "z_min": "z_min", "z_max": "z_max",
    "cog_url": "cog_url", "pmtiles_url": "pmtiles_url", "tiles_url": "tiles_url",
    "slope_url": "slope_url", "slope_tiles_url": "slope_tiles_url",
    "slope_step": "slope_step", "fill_mode": "fill_mode", "notes": "notes",
}
_TEXT_FIELDS = {
    "title", "region", "product", "source", "source_url", "horizontal_crs", "vertical_datum",
    "cog_url", "pmtiles_url", "tiles_url", "slope_url", "slope_tiles_url", "fill_mode", "notes",
}


def footprint_from(geometry):
    if not geometry:
        return None
    g = GEOSGeometry(json.dumps(geometry), srid=4326)
    if isinstance(g, Polygon):
        g = MultiPolygon(g, srid=4326)
    return g if isinstance(g, MultiPolygon) else None


def upsert_feature(feature, now):
    """Create or update one DemSource from a catalogue feature. Returns
    (obj, created)."""
    p = feature.get("properties") or {}
    slug = p.get("id")
    if not slug:
        return None, False
    fields = {}
    for src, dst in _COPY.items():
        v = p.get(src)
        if dst in _TEXT_FIELDS:
            v = v or ""
        fields[dst] = v
    fields["kind"] = DemSource.Kind.LIDAR
    fields["encoding"] = DemSource.Encoding.MAPBOX
    fields["footprint"] = footprint_from(feature.get("geometry"))
    fields["imported_at"] = now
    obj, created = DemSource.objects.get_or_create(slug=slug, defaults={**fields, "enabled": True})
    if not created:
        for k, v in fields.items():
            setattr(obj, k, v)
        obj.save()
    return obj, created


def ensure_context_rows():
    made = 0
    for row in CONTEXT_ROWS:
        _, created = DemSource.objects.get_or_create(slug=row["slug"], defaults=row)
        made += created
    return made


def import_catalog(fc, disable_missing=True):
    """Upsert every feature; returns a summary dict."""
    now = timezone.now()
    seen, created = [], 0
    for f in fc.get("features", []):
        obj, was_created = upsert_feature(f, now)
        if obj is not None:
            seen.append(obj.slug)
            created += was_created
    disabled = 0
    if disable_missing and seen:
        disabled = DemSource.objects.filter(kind=DemSource.Kind.LIDAR, enabled=True).exclude(
            slug__in=seen
        ).update(enabled=False)
    return {"seen": len(seen), "created": created, "updated": len(seen) - created, "disabled": disabled}


class Command(BaseCommand):
    help = __doc__

    def add_arguments(self, parser):
        parser.add_argument("--url", default=CATALOG_URL)
        parser.add_argument("--file", help="Read the catalogue from a local GeoJSON file instead.")
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument(
            "--keep-missing", action="store_true",
            help="Do not disable lidar rows that are absent from this catalogue.",
        )

    def handle(self, *args, **opts):
        if opts["file"]:
            with open(opts["file"], "rb") as fh:
                fc = json.load(fh)
        else:
            try:
                with urllib.request.urlopen(opts["url"], timeout=60) as r:
                    fc = json.load(r)
            except OSError as e:
                raise CommandError(f"could not fetch {opts['url']}: {e}") from e
        feats = fc.get("features", [])
        if opts["dry_run"]:
            for f in feats:
                p = f.get("properties", {})
                self.stdout.write(f"{p.get('id'):24s} {p.get('year') or '':6} {p.get('title')}")
            self.stdout.write(f"{len(feats)} features (dry run, nothing written)")
            return
        n_ctx = ensure_context_rows()
        summary = import_catalog(fc, disable_missing=not opts["keep_missing"])
        self.stdout.write(
            f"lidar: {summary['created']} created, {summary['updated']} updated, "
            f"{summary['disabled']} disabled; context rows added: {n_ctx}"
        )
