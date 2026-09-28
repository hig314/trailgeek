"""Trail design: importers, the alignment / leg API, live and saved
evaluations, routing along trails, projects, and the legs tile layer.

Evaluations never touch the network here: `core.evaluation.choose_sources`
is patched to a synthetic surface (Huey runs tasks inline under test)."""
import json
import math
import os
import tempfile
import zipfile
from unittest import mock

import numpy as np
from django.contrib.auth.models import Group, User
from django.contrib.gis.geos import LineString, MultiLineString
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase

from core.dem import TerrariumSource, transform_xy
from core.importers import import_alignments, import_trails
from core.models import Alignment, Leg, Project, Trail, Visibility

ORIGIN = (-151.19, 59.62)          # Grewingk


class Tilted(TerrariumSource):
    """Terrarium without the network: a plane rising 20 % to the north."""
    def fetch_tile(self, z, x, y):
        n = 2 ** z
        rows = np.arange(256) + 0.5
        lat = np.degrees(np.arctan(np.sinh(math.pi * (1 - 2 * (y * 256 + rows) / (n * 256)))))
        north_m = (lat - 59.0) * 111320.0
        return np.repeat((north_m * 0.2)[:, None], 256, axis=1).astype(np.float32)


def fake_sources(line, project=None):
    return [Tilted()]


def utm_line(pts, srid=32605):
    """Metres east/north of ORIGIN -> lon/lat list."""
    ox, oy = transform_xy([ORIGIN[0]], [ORIGIN[1]], 4326, srid)
    lon, lat = transform_xy([ox[0] + p[0] for p in pts], [oy[0] + p[1] for p in pts], srid, 4326)
    return [[float(a), float(b)] for a, b in zip(lon, lat)]


def geojson_file(features, name="lines.geojson"):
    fc = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": props, "geometry": {"type": "LineString", "coordinates": coords}}
        for props, coords in features]}
    d = tempfile.mkdtemp()
    path = os.path.join(d, name)
    with open(path, "w") as fh:
        json.dump(fc, fh)
    return path


TRAIL_FEATURES = [
    ({"Name": "Alpine Ridge", "Symbol": "Major", "Area": "KBSP", "Builder": "KBSP", "Default": "Display",
      "descriptio": "The ridge."}, utm_line([(0, 0), (0, 200)])),
    ({"Name": None, "Symbol": "Route", "Area": "Seldovia", "Default": "Hidden"}, utm_line([(0, 200), (150, 200)])),
    ({"Name": "Old cut", "Symbol": "Abandoned", "Area": "KBSP", "Default": "Hidden"}, utm_line([(500, 0), (600, 0)])),
]


class ImportTests(TestCase):
    def test_trails_map_fields_and_reimport_updates_in_place(self):
        path = geojson_file(TRAIL_FEATURES, "200111_TRAILS.geojson")
        rep = import_trails(path)
        self.assertEqual((rep.created, rep.updated, rep.skipped), (3, 0, 0))
        ridge = Trail.objects.get(name="Alpine Ridge")
        self.assertEqual((ridge.trail_class, ridge.region, ridge.builder, ridge.visibility),
                         ("major", "KBSP", "KBSP", Visibility.PUBLIC))
        self.assertEqual(ridge.description, "The ridge.")
        route = Trail.objects.get(trail_class="route")
        self.assertEqual(route.visibility, Visibility.GATED)           # Hidden in the source map
        self.assertTrue(route.name.startswith("Unnamed route"))
        self.assertEqual(Trail.objects.get(name="Old cut").status, Trail.Status.HISTORIC)
        pk = ridge.pk
        rep = import_trails(path)
        self.assertEqual((rep.created, rep.updated), (0, 3))
        self.assertEqual(Trail.objects.get(name="Alpine Ridge").pk, pk)  # FKs survive a refresh

    def test_zip_with_macos_junk(self):
        path = geojson_file(TRAIL_FEATURES[:1], "trails.geojson")
        zpath = os.path.join(os.path.dirname(path), "t.zip")
        with zipfile.ZipFile(zpath, "w") as z:
            z.write(path, "folder/trails.geojson")
            z.writestr("__MACOSX/folder/._trails.geojson", b"junk")
        self.assertEqual(import_trails(zpath).created, 1)

    def test_alignments_go_to_a_private_project_with_one_leg_each(self):
        path = geojson_file([
            ({"Name": "Saddle_alternate", "Type": None}, utm_line([(0, 0), (100, 100)])),
            ({"Name": None, "Type": "Restored"}, utm_line([(0, 0), (0, 50)])),
        ], "scouting.geojson")
        rep = import_alignments(path, project_name="Scouting 2025", evaluate=False)
        p = rep.project
        self.assertEqual((p.slug, p.visibility), ("scouting-2025", Visibility.PRIVATE))
        self.assertEqual(p.alignments.count(), 2)
        self.assertEqual(Leg.objects.get(alignment__name="Saddle_alternate").kind, "new")
        self.assertEqual(Leg.objects.get(alignment__name="Restored line 1").kind, "restore")
        a = p.alignments.get(name="Saddle_alternate")
        self.assertAlmostEqual(a.length_m, math.hypot(100, 100), delta=1.0)
        self.assertEqual(import_alignments(path, project=p, evaluate=False).updated, 2)

    def test_import_page_is_for_editors(self):
        u = User.objects.create_user("u", password="pw")
        self.client.login(username="u", password="pw")
        self.assertEqual(self.client.get("/import/").status_code, 403)
        call_command("init_groups", verbosity=0)
        u.groups.add(Group.objects.get(name="trail_editors"))
        path = geojson_file(TRAIL_FEATURES, "200111_TRAILS.geojson")
        with open(path, "rb") as fh:
            r = self.client.post("/import/", {"file": SimpleUploadedFile("200111_TRAILS.geojson", fh.read()),
                                              "target": "trails", "visibility": "private"})
        self.assertContains(r, "3 created")
        self.assertEqual(Trail.objects.count(), 3)


@mock.patch("core.evaluation.choose_sources", fake_sources)
class AlignmentApiTests(TestCase):
    def setUp(self):
        call_command("init_groups", verbosity=0)
        self.owner = User.objects.create_user("own", password="pw")
        self.owner.groups.add(Group.objects.get(name="trail_editors"))
        self.editor = User.objects.create_user("ed", password="pw")
        self.editor.groups.add(Group.objects.get(name="trail_editors"))
        self.project = Project.objects.create(slug="p", name="P", owner=self.owner, visibility=Visibility.PRIVATE)
        self.a = Alignment.objects.create(project=self.project, name="A")
        Leg.objects.create(alignment=self.a, order=0, kind="new", geom=LineString(utm_line([(0, 0), (0, 100)]), srid=4326))
        self.a.rebuild_from_legs()
        self.client.login(username="own", password="pw")

    def post(self, url, body):
        return self.client.post(url, json.dumps(body), content_type="application/json")

    def two_legs(self):
        cs = utm_line([(0, 0), (0, 100), (100, 100)])
        return [{"coords": cs[:2], "kind": "existing"}, {"coords": cs[1:], "kind": "new", "effort_factor": 0.5, "name": "Traverse"}]

    def test_get_includes_legs_and_edit_rights(self):
        p = self.client.get(f"/api/alignments/{self.a.pk}.geojson").json()["properties"]
        self.assertEqual(len(p["legs"]), 1)
        self.assertTrue(p["can_edit"])
        self.client.logout()
        self.assertEqual(self.client.get(f"/api/alignments/{self.a.pk}.geojson").status_code, 404)

    def test_save_replaces_legs_and_evaluates(self):
        with self.captureOnCommitCallbacks(execute=True):
            r = self.post(f"/api/alignments/{self.a.pk}/save", {"legs": self.two_legs(), "name": "A2"})
        self.assertEqual(r.status_code, 200)
        self.a.refresh_from_db()
        self.assertEqual(self.a.name, "A2")
        self.assertEqual(self.a.evaluation_status, "done")
        self.assertAlmostEqual(self.a.length_m, 200, delta=1)
        up, across = self.a.legs.order_by("order")
        self.assertAlmostEqual(up.stats["grade"]["p50"], 20.0, delta=1.0)
        self.assertAlmostEqual(across.stats["tsa"]["p50"], 90.0, delta=2.0)
        self.assertEqual(up.stats["effort"]["construct_days"], 0.0)
        self.assertGreater(across.stats["effort"]["construct_days"], 0.0)
        h = self.client.get(f"/api/alignments/{self.a.pk}.geojson").json()["properties"]["headline"]
        self.assertEqual((h["build_m"], h["existing_m"]), (100.0, 100.0))

    def test_legs_must_meet(self):
        legs = self.two_legs()
        legs[1]["coords"][0] = utm_line([(5, 100)])[0]
        r = self.post(f"/api/alignments/{self.a.pk}/save", {"legs": legs})
        self.assertEqual(r.status_code, 400)
        self.assertIn("do not meet", r.json()["error"])
        legs[1]["coords"][0] = utm_line([(0.2, 100)])[0]              # within tolerance: joined exactly
        self.assertEqual(self.post(f"/api/alignments/{self.a.pk}/save", {"legs": legs}).status_code, 200)
        l0, l1 = self.a.legs.order_by("order")
        self.assertEqual(l0.geom.coords[-1], l1.geom.coords[0])

    def test_bad_kind_and_short_leg(self):
        self.assertEqual(self.post(f"/api/alignments/{self.a.pk}/save", {"legs": [{"coords": [[0, 0]]}]}).status_code, 400)
        r = self.post(f"/api/alignments/{self.a.pk}/save", {"legs": [{"coords": [[0, 0], [0, 0.001]], "kind": "teleport"}]})
        self.assertEqual(r.status_code, 400)

    def test_permissions(self):
        self.client.login(username="ed", password="pw")         # trail editor, but the project is private
        self.assertEqual(self.post(f"/api/alignments/{self.a.pk}/save", {"legs": self.two_legs()}).status_code, 404)
        self.project.visibility = Visibility.GATED
        self.project.save()
        self.editor.groups.add(Group.objects.get(name="data_users"))
        self.assertEqual(self.post(f"/api/alignments/{self.a.pk}/save", {"legs": self.two_legs()}).status_code, 200)
        viewer = User.objects.create_user("v", password="pw")
        viewer.groups.add(Group.objects.get(name="data_users"))
        self.client.login(username="v", password="pw")
        self.assertEqual(self.post(f"/api/alignments/{self.a.pk}/save", {"legs": self.two_legs()}).status_code, 403)

    def test_duplicate_and_delete(self):
        r = self.post(f"/api/alignments/{self.a.pk}/duplicate", {})
        self.assertEqual(r.status_code, 201)
        b = Alignment.objects.get(pk=r.json()["properties"]["id"])
        self.assertEqual((b.parent, b.legs.count(), b.name), (self.a, 1, "A (variant 2)"))
        r = self.post(f"/api/alignments/{b.pk}/delete", {})
        self.assertEqual(r.json()["project"], "p")
        self.assertFalse(Alignment.objects.filter(pk=b.pk).exists())

    def test_create_in_new_private_project(self):
        r = self.post("/api/alignments/", {"project_name": "Ram Valley", "name": "Low route", "legs": self.two_legs()})
        self.assertEqual(r.status_code, 201)
        a = Alignment.objects.get(name="Low route")
        self.assertEqual((a.project.slug, a.project.visibility, a.project.owner), ("ram-valley", "private", self.owner))
        self.assertEqual(a.legs.count(), 2)

    def test_live_evaluation(self):
        r = self.post("/api/evaluate/", {"legs": self.two_legs(), "project_id": self.project.pk, "seq": 3})
        self.assertEqual(r.status_code, 200)
        j = r.json()
        self.assertTrue(j["ok"])
        self.assertEqual(j["seq"], 3)
        res = j["result"]
        self.assertEqual(len(res["legs"]), 2)
        self.assertEqual(res["headline"]["existing_m"], 100.0)
        self.assertIn("lon", res["profile"])
        self.assertEqual(self.client.get("/api/evaluate/not-a-task/").status_code, 400)

    def test_project_api_and_list(self):
        j = self.client.get("/api/projects/p.json").json()
        self.assertEqual(len(j["alignments"]), 1)
        self.assertTrue(j["project"]["can_edit"])
        self.assertEqual([p["slug"] for p in self.client.get("/api/projects/").json()["projects"]], ["p"])

    def test_legs_tile_layer(self):
        lon, lat = ORIGIN
        z = 15
        x = int((lon + 180) / 360 * 2 ** z)
        y = int((1 - math.log(math.tan(math.radians(lat)) + 1 / math.cos(math.radians(lat))) / math.pi) / 2 * 2 ** z)
        body = self.client.get(f"/tiles/trails/{z}/{x}/{y}.mvt").content
        self.assertIn(b"legs", body)
        self.client.logout()
        self.assertNotIn(b"legs", self.client.get(f"/tiles/trails/{z}/{x}/{y}.mvt").content)   # private project


class RouteTests(TestCase):
    """Following trails: a network with a near-miss junction and an island."""

    def setUp(self):
        def trail(slug, pts):
            return Trail.objects.create(slug=slug, name=slug, geom=MultiLineString(LineString(utm_line(pts), srid=4326), srid=4326))
        self.a = trail("a", [(0, 0), (300, 0)])
        self.b = trail("b", [(302, 1), (302, 300)])         # starts 2 m off a's end: joined by snapping
        self.c = trail("c", [(150, 0), (150, -200)])        # T-junction onto the middle of a
        self.island = trail("island", [(1000, 1000), (1100, 1000)])

    def get(self, frm, to):
        f, t = utm_line([frm, to])
        return self.client.get(f"/api/route/trails?from={f[0]},{f[1]}&to={t[0]},{t[1]}")

    def test_path_across_a_near_miss(self):
        r = self.get((10, 5), (302, 250))
        self.assertEqual(r.status_code, 200)
        j = r.json()
        self.assertAlmostEqual(j["length_m"], 290 + 250, delta=8)
        self.assertEqual([t["name"] for t in j["trails"]], ["a", "b"])      # longest share first
        self.assertAlmostEqual(j["start_offset_m"], 5, delta=0.5)

    def test_t_junction(self):
        j = self.get((0, 0), (150, -200)).json()
        self.assertAlmostEqual(j["length_m"], 350, delta=8)
        self.assertEqual({t["name"] for t in j["trails"]}, {"a", "c"})

    def test_disconnected(self):
        r = self.get((0, 0), (1050, 1000))
        self.assertEqual(r.status_code, 404)
        self.assertIn("do not connect", r.json()["error"])

    def test_hidden_trails_are_not_routed_for_anonymous(self):
        Trail.objects.filter(pk=self.b.pk).update(visibility=Visibility.GATED)
        self.assertEqual(self.get((10, 0), (302, 250)).status_code, 404)
