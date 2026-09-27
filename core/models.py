"""Phase 1 data model (PLAN.md §4): the DEM catalogue, trails, GPS tracks,
design projects and their candidate alignments.

Everything geographic is stored in EPSG:4326. Lengths are computed in a
local UTM zone at save time (core.geo) so list views never need PostGIS
maths just to show a distance.
"""
from django.conf import settings
from django.contrib.gis.db import models
from django.urls import reverse

from . import geo


class Visibility(models.TextChoices):
    PUBLIC = "public", "Public"
    GATED = "gated", "Signed-in collaborators"     # members of data_users
    PRIVATE = "private", "Owner and project members"


class DemSource(models.Model):
    """One entry of the DEM catalogue.

    Lidar rows are seeded from landslidescience's public catalogue by
    `manage.py import_dem_catalog` and carry that site's R2 URLs: trailgeek
    reads the same archives rather than keeping a second copy of the lidar.
    Two `context` rows (AWS Terrarium, USGS 3DEP) cover the ground outside
    every survey footprint.
    """

    class Kind(models.TextChoices):
        LIDAR = "lidar", "Lidar survey"
        CONTEXT = "context", "Regional context DEM"

    class Encoding(models.TextChoices):
        MAPBOX = "mapbox", "Mapbox terrain-RGB (PMTiles or tiles)"
        TERRARIUM = "terrarium", "Terrarium tiles"
        IMAGESERVER = "imageserver", "ArcGIS ImageServer (float32 export)"

    slug = models.SlugField(unique=True, help_text="Catalogue id, e.g. grewingk_2021.")
    title = models.CharField(max_length=200)
    kind = models.CharField(max_length=10, choices=Kind.choices, default=Kind.LIDAR)
    encoding = models.CharField(max_length=12, choices=Encoding.choices, default=Encoding.MAPBOX)
    enabled = models.BooleanField(default=True)
    gated = models.BooleanField(
        default=False,
        help_text="Only data_users see it. Gated lidar also needs trailgeek's own ranged proxy (not built yet).",
    )
    year = models.IntegerField(null=True, blank=True)
    region = models.CharField(max_length=100, blank=True)
    product = models.CharField(max_length=100, blank=True)
    source = models.CharField(max_length=300, blank=True, help_text="Who flew / published it.")
    source_url = models.URLField(max_length=500, blank=True)
    horizontal_crs = models.CharField(max_length=40, blank=True, help_text="e.g. EPSG:6334")
    vertical_datum = models.CharField(
        max_length=200, blank=True,
        help_text="Free text from the catalogue, e.g. 'NAVD88 (GEOID12B, after vertical_shift_m)'.",
    )
    native_res_m = models.FloatField(null=True, blank=True)
    min_zoom = models.IntegerField(default=0)
    max_zoom = models.IntegerField(default=15)
    footprint = models.MultiPolygonField(srid=4326, null=True, blank=True)
    bounds = models.JSONField(null=True, blank=True, help_text="[w, s, e, n]")
    coverage_km2 = models.FloatField(null=True, blank=True)
    z_min = models.FloatField(null=True, blank=True)
    z_max = models.FloatField(null=True, blank=True)
    # Where the bytes are. For lidar these come straight from the catalogue.
    cog_url = models.URLField(max_length=500, blank=True, help_text="Archive COG (the worker samples this).")
    pmtiles_url = models.URLField(max_length=500, blank=True)
    tiles_url = models.CharField(max_length=500, blank=True, help_text="Per-tile URL template ({z}/{x}/{y}).")
    slope_url = models.URLField(max_length=500, blank=True)
    slope_tiles_url = models.CharField(max_length=500, blank=True)
    slope_step = models.FloatField(null=True, blank=True)
    fill_mode = models.CharField(max_length=20, blank=True)
    notes = models.TextField(blank=True)
    imported_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["kind", "region", "title"]
        verbose_name = "DEM source"

    def __str__(self):
        return self.title

    def client_props(self):
        """The properties the map needs, in the shape dem_shade_bridge.js's
        catalogOpts() reads (same keys as the landslidescience catalogue)."""
        return {
            "id": self.slug, "title": self.title, "kind": self.kind, "encoding": self.encoding,
            "year": self.year, "region": self.region, "product": self.product,
            "source": self.source, "source_url": self.source_url,
            "native_res_m": self.native_res_m, "min_zoom": self.min_zoom, "max_zoom": self.max_zoom,
            "bounds": self.bounds, "coverage_km2": self.coverage_km2,
            "z_min": self.z_min, "z_max": self.z_max,
            "cog_url": self.cog_url or None, "pmtiles_url": self.pmtiles_url or None,
            "tiles_url": self.tiles_url or None, "slope_url": self.slope_url or None,
            "slope_tiles_url": self.slope_tiles_url or None, "slope_step": self.slope_step,
            "fill_mode": self.fill_mode or None, "gated": self.gated,
        }


class OwnedMixin(models.Model):
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    visibility = models.CharField(max_length=10, choices=Visibility.choices, default=Visibility.PUBLIC)
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class Trail(OwnedMixin):
    class Status(models.TextChoices):
        EXISTING = "existing", "Existing"
        PROPOSED = "proposed", "Proposed"
        HISTORIC = "historic", "Historic"

    slug = models.SlugField(unique=True)
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True, help_text="Markdown.")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.EXISTING)
    region = models.CharField(max_length=100, blank=True)
    tags = models.JSONField(default=list, blank=True)
    source = models.CharField(max_length=300, blank=True, help_text="Where the line came from.")
    geom = models.MultiLineStringField(srid=4326)
    length_m = models.FloatField(default=0, editable=False)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        self.length_m = geo.length_m(self.geom)
        super().save(*args, **kwargs)

    def get_absolute_url(self):
        return reverse("home") + f"#trail={self.slug}"


class Track(OwnedMixin):
    """An uploaded GPS trackline. `geom` is 3D: Z holds the device elevation
    (0 where the file had none; see `has_elevation`). Timestamps live in
    `times` as seconds since `taken_at`, one per vertex, for replay later.
    The original file is kept untouched."""

    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    trail = models.ForeignKey(Trail, null=True, blank=True, on_delete=models.SET_NULL, related_name="tracks")
    device = models.CharField(max_length=100, blank=True, help_text="GPX creator string.")
    taken_at = models.DateTimeField(null=True, blank=True)
    geom = models.LineStringField(srid=4326, dim=3)
    has_elevation = models.BooleanField(default=False)
    times = models.JSONField(null=True, blank=True, editable=False)
    original = models.FileField(upload_to="tracks/%Y/", blank=True)
    point_count = models.IntegerField(default=0, editable=False)
    length_m = models.FloatField(default=0, editable=False)
    climb_m = models.FloatField(default=0, editable=False)
    descent_m = models.FloatField(default=0, editable=False)

    class Meta:
        ordering = ["-taken_at", "-created"]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        self.length_m = geo.length_m(self.geom)
        self.point_count = len(self.geom.coords)
        if self.has_elevation:
            self.climb_m, self.descent_m = geo.climb_descent(c[2] for c in self.geom.coords)
        else:
            self.climb_m = self.descent_m = 0
        super().save(*args, **kwargs)

    def get_absolute_url(self):
        return reverse("home") + f"#track={self.pk}"


DEFAULT_PROJECT_SETTINGS = {
    # docs/TRAIL_ANALYSIS.md §1: the defaults used for the Ram Valley and
    # Graduation Peak runs. Units stay as strings; the evaluator parses them.
    "sample_spacing": "5 ft",
    "averaging": {"grade_%": [9, 21], "TSA": [9], "slope_%": [5], "elev_m": [5, 9]},
    "thresholds": {"grade_%": [5, 18, 35], "slope_%": [10, 20, 73, 100], "TSA": [45, 60, 68]},
}


class Project(OwnedMixin):
    """A trail design study: an area, a DEM, and a set of candidate alignments."""

    slug = models.SlugField(unique=True)
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True, help_text="Markdown.")
    aoi = models.PolygonField(srid=4326, null=True, blank=True, help_text="Area of interest.")
    dem = models.ForeignKey(DemSource, null=True, blank=True, on_delete=models.SET_NULL, related_name="projects")
    members = models.ManyToManyField(settings.AUTH_USER_MODEL, blank=True, related_name="trail_projects")
    settings = models.JSONField(default=dict, blank=True, help_text="Evaluator settings; empty means defaults.")

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    def effective_settings(self):
        return {**DEFAULT_PROJECT_SETTINGS, **(self.settings or {})}


class Alignment(models.Model):
    """One candidate line within a Project. The stored geometry is never
    rewritten: the evaluator records which way is uphill in `runs_uphill`
    and works on a derived copy (fixing the old fix_direction_of_paths)."""

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="alignments")
    name = models.CharField(max_length=200)
    priority = models.IntegerField(default=1, help_text="1 = primary candidate.")
    trailhead = models.CharField(max_length=200, blank=True)
    notes = models.TextField(blank=True)
    geom = models.LineStringField(srid=4326)
    runs_uphill = models.BooleanField(null=True, blank=True, editable=False, help_text="Set by the evaluator.")
    length_m = models.FloatField(default=0, editable=False)
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["project", "priority", "name"]

    def __str__(self):
        return f"{self.project.name}: {self.name}"

    def save(self, *args, **kwargs):
        self.length_m = geo.length_m(self.geom)
        super().save(*args, **kwargs)

    def get_absolute_url(self):
        return reverse("home") + f"#align={self.pk}"
