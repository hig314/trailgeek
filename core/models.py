"""Data model (PLAN.md §4): the DEM catalogue, trails, GPS tracks, design
projects, their candidate alignments, and the legs each alignment is built
from.

Everything geographic is stored in EPSG:4326. Lengths are computed in a
local UTM zone at save time (core.geo) so list views never need PostGIS
maths just to show a distance.
"""
from django.conf import settings
from django.contrib.gis.db import models
from django.contrib.gis.geos import LineString
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

    class TrailClass(models.TextChoices):
        # The "Symbol" classes of the Kachemak trails map (200111_TRAILS).
        MAJOR = "major", "Major trail"
        REGULAR = "regular", "Trail"
        ROUTE = "route", "Route (unmaintained / cross-country)"
        SKI = "ski", "Ski trail"
        SIDEWALK = "sidewalk", "Sidewalk / path"
        ABANDONED = "abandoned", "Abandoned"
        OTHER = "other", "Other"

    slug = models.SlugField(unique=True, max_length=120)
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True, help_text="Markdown.")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.EXISTING)
    trail_class = models.CharField(max_length=10, choices=TrailClass.choices, default=TrailClass.REGULAR)
    region = models.CharField(max_length=100, blank=True)
    builder = models.CharField(max_length=100, blank=True, help_text="Who built or maintains it.")
    tags = models.JSONField(default=list, blank=True)
    source = models.CharField(max_length=300, blank=True, help_text="Where the line came from.")
    source_key = models.CharField(
        max_length=200, blank=True, db_index=True,
        help_text="Import provenance, e.g. '200111_TRAILS#17'. Re-importing a file replaces its rows.",
    )
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
    # The construction / maintenance rubric (TRAIL_ANALYSIS.md §1.7). Slope
    # bands use the smoothed side-slope, grade bands the smoothed |grade|.
    "effort_rubric": {
        "slope_effect": [
            {"min": "0 %", "max": "10 %", "construct": "10 m/day", "maintain": "1 mi/day", "note": "turnpike"},
            {"min": "10 %", "max": "20 %", "construct": "30 m/day", "maintain": "1 mi/day", "note": "drainage structures"},
            {"min": "20 %", "max": "50 %", "construct": "100 m/day", "maintain": "1 mi/day", "note": "ideal bench"},
            {"min": "50 %", "max": "90 %", "construct": "50 m/day", "maintain": "0.5 mi/day", "note": "heavy cut"},
            {"min": "90 %", "max": "10000 %", "construct": "10 m/day", "maintain": "0.5 mi/day", "note": "walls / ledging"},
        ],
        "grade_effect": [
            {"min": "0 %", "max": "15 %", "construct": "1 mi/day", "maintain": "3 mi/day"},
            {"min": "15 %", "max": "25 %", "construct": "1 mi/day", "maintain": "2 mi/day"},
            {"min": "25 %", "max": "45 %", "construct": "20 m/day", "maintain": "2 mi/day", "note": "stairs, hardening"},
            {"min": "45 %", "max": "77 %", "construct": "5 m/day", "maintain": "0.5 mi/day", "note": "build stairs"},
            {"min": "77 %", "max": "10000 %", "construct": "0.5 m/day", "maintain": "0.1 mi/day", "note": "effectively impossible"},
        ],
    },
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
    """One candidate route within a Project: a continuous line made of
    ordered Legs. Each leg is either existing trail to follow or a specific
    build effort (new construction, reroute, restoration), so the same route
    can be costed leg by leg.

    `geom` is derived: the legs joined end to end, rebuilt by
    `rebuild_from_legs()`. It is never reversed or rewritten by the
    evaluator; which way is uphill is recorded in `runs_uphill`
    (fixing the old fix_direction_of_paths, TRAIL_ANALYSIS.md §1.1).
    """

    class EvalStatus(models.TextChoices):
        NONE = "none", "Not evaluated"
        QUEUED = "queued", "Queued"
        DONE = "done", "Done"
        FAILED = "failed", "Failed"

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="alignments")
    name = models.CharField(max_length=200)
    priority = models.IntegerField(default=1, help_text="1 = primary candidate.")
    trailhead = models.CharField(max_length=200, blank=True)
    notes = models.TextField(blank=True)
    parent = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.SET_NULL, related_name="variants",
        help_text="The alignment this one was duplicated from, when it is a variant.",
    )
    source = models.CharField(max_length=300, blank=True, help_text="Import provenance, if imported.")
    geom = models.LineStringField(srid=4326, null=True, blank=True, editable=False)
    runs_uphill = models.BooleanField(null=True, blank=True, editable=False, help_text="Set by the evaluator.")
    length_m = models.FloatField(default=0, editable=False)
    evaluation = models.JSONField(null=True, blank=True, editable=False)
    headline = models.JSONField(
        null=True, blank=True, editable=False,
        help_text="The few numbers compare tables show, kept apart from the (large) evaluation.")
    evaluation_status = models.CharField(
        max_length=10, choices=EvalStatus.choices, default=EvalStatus.NONE, editable=False
    )
    evaluation_error = models.TextField(blank=True, editable=False)
    evaluated_at = models.DateTimeField(null=True, blank=True, editable=False)
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["project", "priority", "name"]

    def __str__(self):
        return f"{self.project.name}: {self.name}"

    def save(self, *args, **kwargs):
        self.length_m = geo.length_m(self.geom) if self.geom else 0
        super().save(*args, **kwargs)

    def get_absolute_url(self):
        return reverse("home") + f"#align={self.pk}"

    def rebuild_from_legs(self, save=True):
        """Join the legs (in order) into `geom`. Consecutive legs share their
        joining vertex, so it is written once."""
        coords = []
        for leg in self.legs.order_by("order"):
            cs = list(leg.geom.coords)
            if coords and cs and coords[-1] == cs[0]:
                cs = cs[1:]
            coords.extend(cs)
        self.geom = LineString(coords, srid=4326) if len(coords) >= 2 else None
        if save:
            self.save()


class Leg(models.Model):
    """One stretch of an Alignment. Consecutive legs share their joining
    vertex (the API refuses a gap), so an alignment is always continuous."""

    class Kind(models.TextChoices):
        EXISTING = "existing", "Existing trail"
        NEW = "new", "New construction"
        REROUTE = "reroute", "Reroute"
        RESTORE = "restore", "Restoration"

    # Kinds whose construction effort the rubric estimates. Existing trail
    # only costs maintenance.
    BUILD_KINDS = {Kind.NEW, Kind.REROUTE, Kind.RESTORE}

    alignment = models.ForeignKey(Alignment, on_delete=models.CASCADE, related_name="legs")
    order = models.PositiveIntegerField(default=0)
    name = models.CharField(max_length=200, blank=True)
    kind = models.CharField(max_length=10, choices=Kind.choices, default=Kind.NEW)
    trail = models.ForeignKey(
        Trail, null=True, blank=True, on_delete=models.SET_NULL, related_name="legs",
        help_text="For existing-trail legs: the trail followed (the main one, if several).",
    )
    effort_factor = models.FloatField(
        default=1.0,
        help_text="Multiplier on the rubric's construction days, e.g. 0.5 for a restoration "
                  "where half the tread survives. Ignored for existing trail.",
    )
    notes = models.TextField(blank=True)
    geom = models.LineStringField(srid=4326)
    length_m = models.FloatField(default=0, editable=False)
    stats = models.JSONField(null=True, blank=True, editable=False, help_text="From the last evaluation.")

    class Meta:
        ordering = ["alignment", "order"]

    def __str__(self):
        return self.name or f"{self.get_kind_display()} leg {self.order + 1}"

    def save(self, *args, **kwargs):
        self.length_m = geo.length_m(self.geom)
        super().save(*args, **kwargs)
