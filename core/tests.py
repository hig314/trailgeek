import json
import math
import tempfile

from django.contrib.auth.models import Group, User
from django.contrib.gis.geos import LineString, MultiLineString, Polygon
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, override_settings

from .gpx import GpxError, parse_gpx
from .management.commands.import_dem_catalog import ensure_context_rows, import_catalog
from .models import Alignment, DemSource, Leg, Project, Trail, Track, Visibility

# A short climb on Alpine Ridge above Grewingk Glacier, in the test lidar area.
GPX = b"""<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" creator="unit test" xmlns="http://www.topografix.com/GPX/1/1">
  <trk><name>Alpine Ridge test</name><trkseg>
    <trkpt lat="59.6200" lon="-151.1900"><ele>120.0</ele><time>2026-07-01T18:00:00Z</time></trkpt>
    <trkpt lat="59.6210" lon="-151.1890"><ele>140.5</ele><time>2026-07-01T18:05:00Z</time></trkpt>
    <trkpt lat="59.6220" lon="-151.1880"><ele>135.0</ele><time>2026-07-01T18:10:00Z</time></trkpt>
    <trkpt lat="59.6230" lon="-151.1870"><ele>160.0</ele><time>2026-07-01T18:15:00Z</time></trkpt>
  </trkseg></trk>
</gpx>"""

CATALOG = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "geometry": {"type": "Polygon", "coordinates": [[[-151.3, 59.55], [-151.0, 59.55], [-151.0, 59.7], [-151.3, 59.7], [-151.3, 59.55]]]},
            "properties": {
                "id": "grewingk_2021", "title": "Grewingk 2021 lidar", "year": 2021, "region": "Kenai Peninsula",
                "product": "DTM", "source": "Test", "source_url": None, "horizontal_crs": "EPSG:6334",
                "vertical_datum": None, "native_res_m": 0.5, "min_zoom": 8, "max_zoom": 17,
                "bounds": [-151.3, 59.55, -151.0, 59.7], "coverage_km2": 42.0, "z_min": 0, "z_max": 900,
                "cog_url": "https://lidar.landslidescience.org/cog/grewingk_2021.tif",
                "pmtiles_url": "https://lidar.landslidescience.org/pmtiles/grewingk_2021.pmtiles",
                "tiles_url": None, "slope_url": None, "slope_tiles_url": None, "slope_step": None,
                "fill_mode": None, "notes": "",
            },
        }
    ],
}


def tile_xy(lon, lat, z):
    n = 2 ** z
    x = int((lon + 180) / 360 * n)
    lat_r = math.radians(lat)
    y = int((1 - math.log(math.tan(lat_r) + 1 / math.cos(lat_r)) / math.pi) / 2 * n)
    return x, y


def make_trail(slug="alpine-ridge", visibility=Visibility.PUBLIC, owner=None, **kw):
    line = LineString((-151.19, 59.62), (-151.18, 59.63), srid=4326)
    return Trail.objects.create(
        slug=slug, name=slug.replace("-", " ").title(), geom=MultiLineString(line, srid=4326),
        visibility=visibility, owner=owner, **kw,
    )


class SmokeTests(TestCase):
    def test_home_renders_map(self):
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'id="map"')
        self.assertContains(r, "core/js/map.js")

    def test_healthz_reports_postgis(self):
        r = self.client.get("/healthz")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["postgis"])

    def test_sign_in_and_out_land_on_the_map(self):
        User.objects.create_user("u", password="pw")
        r = self.client.post("/accounts/login/", {"username": "u", "password": "pw"})
        self.assertEqual((r.status_code, r["Location"]), (302, "/"))
        r = self.client.post("/accounts/logout/")
        self.assertEqual((r.status_code, r["Location"]), (302, "/"))

    def test_init_groups_is_idempotent(self):
        call_command("init_groups")
        call_command("init_groups")
        self.assertEqual(
            set(Group.objects.values_list("name", flat=True)),
            {"data_users", "trail_editors", "site_admins"},
        )
        self.assertTrue(Group.objects.get(name="site_admins").permissions.filter(codename="change_page").exists())
        self.assertTrue(Group.objects.get(name="trail_editors").permissions.filter(codename="add_track").exists())


class ModelTests(TestCase):
    def test_trail_length_is_computed_in_metres(self):
        t = make_trail()
        # ~0.56 km east and ~1.1 km north at 59.6°N.
        self.assertGreater(t.length_m, 1200)
        self.assertLess(t.length_m, 1300)

    def test_alignment_length_and_str(self):
        p = Project.objects.create(slug="ram", name="Ram Valley")
        a = Alignment.objects.create(project=p, name="A")
        Leg.objects.create(alignment=a, order=0, geom=LineString((-149.5, 61.3), (-149.495, 61.3), srid=4326))
        Leg.objects.create(alignment=a, order=1, kind="existing", geom=LineString((-149.495, 61.3), (-149.49, 61.3), srid=4326))
        a.rebuild_from_legs()
        self.assertAlmostEqual(a.length_m, 536, delta=10)
        self.assertEqual(len(a.geom.coords), 3)          # the shared joint is written once
        self.assertEqual(str(a), "Ram Valley: A")
        self.assertEqual(p.effective_settings()["sample_spacing"], "5 ft")


class GpxTests(TestCase):
    def test_parse_points_elevation_and_time(self):
        g = parse_gpx(GPX)
        self.assertEqual(g["name"], "Alpine Ridge test")
        self.assertEqual(len(g["points"]), 4)
        lon, lat, ele, t = g["points"][1]
        self.assertEqual((lon, lat, ele), (-151.189, 59.621, 140.5))
        self.assertEqual(t.isoformat(), "2026-07-01T18:05:00+00:00")
        self.assertEqual(g["device"], "unit test")

    def test_route_without_namespace_is_accepted(self):
        g = parse_gpx(b'<gpx><rte><rtept lat="1" lon="2"/><rtept lat="1.1" lon="2"/></rte></gpx>')
        self.assertEqual(len(g["points"]), 2)
        self.assertIsNone(g["points"][0][2])

    def test_bad_input(self):
        with self.assertRaises(GpxError):
            parse_gpx(b"not xml")
        with self.assertRaises(GpxError):
            parse_gpx(b'<gpx><trk><trkseg><trkpt lat="1" lon="2"/></trkseg></trk></gpx>')


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class UploadTests(TestCase):
    def setUp(self):
        call_command("init_groups", verbosity=0)
        self.editor = User.objects.create_user("ed", password="pw")
        self.editor.groups.add(Group.objects.get(name="trail_editors"))
        self.viewer = User.objects.create_user("vi", password="pw")

    def upload(self, **extra):
        return self.client.post(
            "/tracks/upload/",
            {"file": SimpleUploadedFile("ridge.gpx", GPX), "visibility": "public", **extra},
        )

    def test_anonymous_is_sent_to_login(self):
        r = self.client.get("/tracks/upload/")
        self.assertEqual(r.status_code, 302)
        self.assertIn("/accounts/login/", r["Location"])

    def test_non_editor_is_forbidden(self):
        self.client.login(username="vi", password="pw")
        self.assertEqual(self.client.get("/tracks/upload/").status_code, 403)

    def test_editor_creates_track(self):
        self.client.login(username="ed", password="pw")
        r = self.upload()
        self.assertEqual(r.status_code, 302)
        t = Track.objects.get()
        self.assertEqual(r["Location"], f"/#track={t.pk}")
        self.assertEqual(t.name, "Alpine Ridge test")
        self.assertEqual(t.owner, self.editor)
        self.assertTrue(t.has_elevation)
        self.assertEqual(t.point_count, 4)
        self.assertEqual(t.geom.coords[0][2], 120.0)
        self.assertAlmostEqual(t.climb_m, 45.5)
        self.assertAlmostEqual(t.descent_m, 5.5)
        self.assertEqual(t.times, [0, 300, 600, 900])
        self.assertEqual(t.taken_at.isoformat(), "2026-07-01T18:00:00+00:00")
        self.assertTrue(t.original.name.endswith("ridge.gpx"))
        # The original comes back untouched.
        r = self.client.get(f"/tracks/{t.pk}/download/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(b"".join(r.streaming_content), GPX)

    def test_bad_file_reports_error(self):
        self.client.login(username="ed", password="pw")
        r = self.client.post("/tracks/upload/", {"file": SimpleUploadedFile("x.gpx", b"<gpx/>"), "visibility": "public"})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Could not read this GPX")
        self.assertEqual(Track.objects.count(), 0)


class ApiAndTileTests(TestCase):
    def setUp(self):
        call_command("init_groups", verbosity=0)
        self.owner = User.objects.create_user("own", password="pw")
        self.other = User.objects.create_user("oth", password="pw")
        self.data_user = User.objects.create_user("du", password="pw")
        self.data_user.groups.add(Group.objects.get(name="data_users"))
        self.public = make_trail("public-trail")
        self.private = make_trail("private-trail", Visibility.PRIVATE, owner=self.owner)
        self.gated = make_trail("gated-trail", Visibility.GATED)
        self.track = Track.objects.create(
            name="t", geom=LineString((-151.19, 59.62, 100), (-151.18, 59.63, 150), srid=4326),
            has_elevation=True, trail=self.public, visibility=Visibility.PUBLIC,
        )
        self.project = Project.objects.create(slug="p", name="P", visibility=Visibility.PRIVATE, owner=self.owner)
        self.alignment = Alignment.objects.create(project=self.project, name="A1")
        Leg.objects.create(alignment=self.alignment, order=0, kind="new",
                           geom=LineString((-151.19, 59.62), (-151.18, 59.63), srid=4326))
        self.alignment.rebuild_from_legs()

    def test_trail_api_respects_visibility(self):
        r = self.client.get("/api/trails/public-trail.geojson")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r["Content-Type"], "application/geo+json")
        f = r.json()
        self.assertEqual(f["geometry"]["type"], "MultiLineString")
        self.assertEqual(f["properties"]["tracks"][0]["id"], self.track.pk)
        self.assertEqual(self.client.get("/api/trails/private-trail.geojson").status_code, 404)
        self.assertEqual(self.client.get("/api/trails/gated-trail.geojson").status_code, 404)
        self.client.login(username="du", password="pw")
        self.assertEqual(self.client.get("/api/trails/gated-trail.geojson").status_code, 200)
        self.assertEqual(self.client.get("/api/trails/private-trail.geojson").status_code, 404)
        self.client.login(username="own", password="pw")
        self.assertEqual(self.client.get("/api/trails/private-trail.geojson").status_code, 200)

    def test_track_api_has_3d_coordinates(self):
        f = self.client.get(f"/api/tracks/{self.track.pk}.geojson").json()
        self.assertEqual(f["geometry"]["coordinates"][1][2], 150.0)
        self.assertTrue(f["properties"]["has_elevation"])
        self.assertEqual(f["properties"]["trail"]["slug"], "public-trail")

    def test_alignment_api_follows_project_visibility(self):
        url = f"/api/alignments/{self.alignment.pk}.geojson"
        self.assertEqual(self.client.get(url).status_code, 404)
        self.client.login(username="oth", password="pw")
        self.assertEqual(self.client.get(url).status_code, 404)
        self.project.members.add(self.other)
        self.assertEqual(self.client.get(url).status_code, 200)
        self.client.login(username="own", password="pw")
        self.assertEqual(self.client.get(url).json()["properties"]["project"]["slug"], "p")

    def test_tile_contains_public_but_not_private(self):
        z = 13
        x, y = tile_xy(-151.185, 59.625, z)
        r = self.client.get(f"/tiles/trails/{z}/{x}/{y}.mvt")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r["Content-Type"], "application/vnd.mapbox-vector-tile")
        self.assertIn(b"public-trail", r.content)
        self.assertNotIn(b"private-trail", r.content)
        self.assertNotIn(b"gated-trail", r.content)
        self.assertIn(b"tracks", r.content)
        self.assertNotIn(b"A1", r.content)          # alignment in a private project
        # An empty tile elsewhere is fine and small.
        x2, y2 = tile_xy(-149.9, 61.2, z)
        self.assertEqual(self.client.get(f"/tiles/trails/{z}/{x2}/{y2}.mvt").content, b"")
        # Out-of-range coordinates are rejected.
        self.assertEqual(self.client.get("/tiles/trails/3/0/0.mvt").status_code, 404)

    def test_tile_for_owner_includes_private(self):
        self.client.login(username="own", password="pw")
        z = 13
        x, y = tile_xy(-151.185, 59.625, z)
        r = self.client.get(f"/tiles/trails/{z}/{x}/{y}.mvt")
        self.assertIn(b"private-trail", r.content)
        self.assertIn(b"A1", r.content)


class DemCatalogTests(TestCase):
    def test_import_upserts_and_disables(self):
        ensure_context_rows()
        self.assertEqual(DemSource.objects.filter(kind="context").count(), 2)
        s = import_catalog(CATALOG)
        self.assertEqual((s["created"], s["updated"], s["disabled"]), (1, 0, 0))
        d = DemSource.objects.get(slug="grewingk_2021")
        self.assertEqual(d.kind, "lidar")
        self.assertEqual(d.max_zoom, 17)
        self.assertTrue(d.footprint.contains(Polygon.from_bbox((-151.2, 59.6, -151.1, 59.65))))
        self.assertEqual(d.pmtiles_url, CATALOG["features"][0]["properties"]["pmtiles_url"])
        # Second run updates in place; a survey missing from the catalogue is disabled, not deleted.
        stale = DemSource.objects.create(slug="old_survey", title="Old", kind="lidar")
        s = import_catalog(CATALOG)
        self.assertEqual((s["created"], s["updated"], s["disabled"]), (0, 1, 1))
        stale.refresh_from_db()
        self.assertFalse(stale.enabled)
        self.assertTrue(DemSource.objects.get(slug="terrarium").enabled)

    def test_command_reads_a_file_and_api_serves_it(self):
        with tempfile.NamedTemporaryFile("w", suffix=".geojson", delete=False) as fh:
            json.dump(CATALOG, fh)
        call_command("import_dem_catalog", file=fh.name, verbosity=0)
        r = self.client.get("/api/dems.geojson")
        self.assertEqual(r.status_code, 200)
        ids = {f["properties"]["id"] for f in r.json()["features"]}
        self.assertIn("grewingk_2021", ids)
        self.assertIn("terrarium", ids)
        self.assertNotIn("usgs3dep", ids)      # disabled by default
        # Gated surveys are hidden from anonymous visitors.
        DemSource.objects.filter(slug="grewingk_2021").update(gated=True)
        ids = {f["properties"]["id"] for f in self.client.get("/api/dems.geojson").json()["features"]}
        self.assertNotIn("grewingk_2021", ids)
