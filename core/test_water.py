"""The wetness overlay's endpoints: sign-in, the bbox and size checks, and a
job run in the (inline) worker on the synthetic plane of test_design."""
import base64
import json
from unittest import mock

import numpy as np
from django.contrib.auth.models import User
from django.test import TestCase

from core.test_design import ORIGIN, fake_sources
from core.water import colourise, png_bytes


@mock.patch("core.evaluation.choose_sources", fake_sources)
class TwiOverlayTests(TestCase):
    def setUp(self):
        User.objects.create_user("u", password="pw")
        self.client.login(username="u", password="pw")
        lon, lat = ORIGIN
        self.bbox = [lon - 0.004, lat - 0.002, lon + 0.004, lat + 0.002]     # ~0.45 x 0.45 km

    def post(self, body):
        return self.client.post("/api/twi/", json.dumps(body), content_type="application/json")

    def test_a_view_comes_back_as_an_image_with_corners(self):
        r = self.post({"bbox": self.bbox})
        self.assertEqual(r.status_code, 202, r.content)
        j = self.client.get(f"/api/twi/jobs/{r.json()['job']}/").json()
        self.assertEqual(j["status"], "done", j)
        res = j["result"]
        self.assertTrue(res["png"].startswith("data:image/png;base64,"))
        self.assertEqual(base64.b64decode(res["png"].split(",")[1])[:8], b"\x89PNG\r\n\x1a\n")
        lons = [c[0] for c in res["corners"]]
        self.assertAlmostEqual(min(lons), self.bbox[0], delta=0.001)
        self.assertGreater(res["corners"][0][1], res["corners"][3][1])         # top-left is north of bottom-left
        self.assertIn("p50", res["stats"])

    def test_refusals(self):
        self.assertEqual(self.post({"bbox": [1, 2, 3]}).status_code, 400)
        self.assertEqual(self.post({"bbox": [10, 50, 9, 51]}).status_code, 400)
        big = self.post({"bbox": [ORIGIN[0] - 0.1, ORIGIN[1] - 0.05, ORIGIN[0] + 0.1, ORIGIN[1] + 0.05]})
        self.assertEqual(big.status_code, 400)
        self.assertIn("zoom in", big.json()["error"])
        job = self.post({"bbox": self.bbox}).json()["job"]
        User.objects.create_user("other", password="pw")
        self.client.login(username="other", password="pw")
        self.assertEqual(self.client.get(f"/api/twi/jobs/{job}/").status_code, 404)  # not yours
        self.client.logout()
        self.assertEqual(self.post({"bbox": self.bbox}).status_code, 302)               # to sign-in

    def test_colours_are_clear_where_dry_and_blue_where_wet(self):
        rgba = colourise(np.array([[3.0, 14.0, np.nan]]))
        self.assertEqual(rgba[0, 0, 3], 0)
        self.assertGreater(rgba[0, 1, 3], 200)
        self.assertGreater(rgba[0, 1, 2], rgba[0, 1, 0])
        self.assertEqual(rgba[0, 2, 3], 0)
        self.assertEqual(png_bytes(rgba)[:8], b"\x89PNG\r\n\x1a\n")
