"""The refine tool's endpoints: defaults per user, a run and a baseline in
the (inline) worker, reading a job, saving a candidate as a variant, and the
refusals. Terrain is the synthetic tilted plane of test_design (no network)."""
import json
from unittest import mock

from django.contrib.auth.models import Group, User
from django.contrib.gis.geos import LineString
from django.core.management import call_command
from django.test import TestCase

from core.models import Alignment, Leg, Project, UserSetting, Visibility
from core.test_design import fake_sources, utm_line

FAST = {"params": {"iterations": 300, "chains": 2, "seed": 4}, "factors": {"drainage": {"k": 0}}}


@mock.patch("core.evaluation.choose_sources", fake_sources)
class RefineApiTests(TestCase):
    def setUp(self):
        call_command("init_groups", verbosity=0)
        self.owner = User.objects.create_user("own", password="pw")
        self.owner.groups.add(Group.objects.get(name="trail_editors"))
        self.project = Project.objects.create(slug="p", name="P", owner=self.owner, visibility=Visibility.PUBLIC)
        self.a = Alignment.objects.create(project=self.project, name="Kinked")
        cs = utm_line([(0, 0), (100, 60), (200, 60)])          # steep to a kink, then level
        Leg.objects.create(alignment=self.a, order=0, kind="new", geom=LineString(cs[:2], srid=4326))
        Leg.objects.create(alignment=self.a, order=1, kind="new", geom=LineString(cs[1:], srid=4326))
        self.a.rebuild_from_legs()
        self.client.login(username="own", password="pw")

    def post(self, url, body):
        return self.client.post(url, json.dumps(body), content_type="application/json")

    def start(self, mode="run", legs=(0, 1), settings=FAST):
        r = self.post(f"/api/alignments/{self.a.pk}/refine", {"mode": mode, "legs": list(legs), "settings": settings})
        self.assertEqual(r.status_code, 202, r.content)
        return r.json()["job"]

    def test_defaults_are_per_user(self):
        r = self.client.get("/api/refine/settings").json()
        self.assertIsNone(r["mine"])
        self.assertEqual(r["effective"]["factors"]["grade"]["k"], 3.3)
        self.assertEqual(len(r["factors"]), 9)
        r = self.post("/api/refine/settings", {"settings": {"factors": {"grade": {"k": 4}}, "params": {"seed": 9}}})
        self.assertEqual(r.json()["effective"]["factors"]["grade"]["k"], 4)
        self.assertNotIn("seed", UserSetting.objects.get(user=self.owner, key="refine").value["params"])
        self.assertEqual(self.post("/api/refine/settings", {"settings": {"factors": {"grade": {"k": -2}}}}).status_code, 400)
        r = self.post("/api/refine/settings", {"reset": True}).json()
        self.assertIsNone(r["mine"])
        self.assertFalse(UserSetting.objects.exists())

    def test_baseline_scores_the_input_only(self):
        st = self.client.get(f"/api/refine/jobs/{self.start('baseline')}/").json()
        self.assertEqual(st["status"], "done", st)
        res = st["result"]
        self.assertEqual(res["candidates"], [])
        self.assertIn("grade", res["histograms"])
        self.assertEqual(len(res["input"]["legs"]), 2)

    def test_run_then_save_a_candidate_as_a_variant(self):
        job = self.start()
        st = self.client.get(f"/api/refine/jobs/{job}/").json()
        self.assertEqual(st["status"], "done", st)
        res = st["result"]
        self.assertTrue(res["candidates"], "no candidate offered")
        c = res["candidates"][0]
        self.assertLess(c["total"], res["input"]["total"])
        self.assertLessEqual(c["stats"]["grade_max"], res["input"]["stats"]["grade_max"])
        with self.captureOnCommitCallbacks(execute=True):
            r = self.post(f"/api/alignments/{self.a.pk}/refine/accept", {"job": job, "candidate": 0})
        self.assertEqual(r.status_code, 201, r.content)
        b = Alignment.objects.get(pk=r.json()["properties"]["id"])
        self.assertEqual(b.parent_id, self.a.pk)
        self.assertEqual(b.legs.count(), 2)
        self.assertEqual(b.refinement["legs"], [0, 1])
        self.assertEqual(b.evaluation_status, "done")
        old, new = list(self.a.geom.coords), list(b.geom.coords)
        self.assertEqual(new[0], old[0])                       # the stretch's ends are kept exactly
        self.assertEqual(new[-1], old[-1])
        self.assertNotEqual(len(new), len(old))

    def test_refusals(self):
        bad = self.post(f"/api/alignments/{self.a.pk}/refine", {"legs": [], "settings": FAST})
        self.assertEqual(bad.status_code, 400)
        Leg.objects.create(alignment=self.a, order=2, kind="new",
                           geom=LineString(utm_line([(200, 60), (300, 60)]), srid=4326))
        gap = self.post(f"/api/alignments/{self.a.pk}/refine", {"legs": [0, 2], "settings": FAST})
        self.assertEqual(gap.status_code, 400)
        self.assertIn("continuous", gap.json()["error"])
        job = self.start(legs=(0, 1))
        # Someone else cannot read the job or start one.
        other = User.objects.create_user("other", password="pw")
        self.client.login(username="other", password="pw")
        self.assertEqual(self.client.get(f"/api/refine/jobs/{job}/").status_code, 404)
        self.assertEqual(self.post(f"/api/alignments/{self.a.pk}/refine", {"legs": [0], "settings": FAST}).status_code, 403)
        # An alignment edited after the run refuses the old result.
        self.client.login(username="own", password="pw")
        leg = self.a.legs.get(order=2)
        leg.geom = LineString(utm_line([(200, 60), (300, 70)]), srid=4326)
        leg.save()
        r = self.post(f"/api/alignments/{self.a.pk}/refine/accept", {"job": job, "candidate": 0})
        self.assertEqual(r.status_code, 409)
