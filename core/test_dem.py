"""core.dem and core.evaluation against synthetic rasters (no network)."""
import math
import tempfile

import numpy as np
from django.contrib.gis.gdal import GDALRaster
from django.contrib.gis.geos import LineString
from django.test import SimpleTestCase, TestCase

from core import evaluation
from core.dem import CogSource, TerrariumSource, sample_stack, transform_xy
from core.models import Alignment, Leg, Project

SLOPE = 0.25


def plane_tif(srid, x0, y0, size=400, res=2.0, nodata_right=False):
    """A GeoTIFF plane rising SLOPE to grid-north of `srid`, origin top-left."""
    ys = y0 - (np.arange(size) + 0.5) * res
    z = np.repeat(((ys - (y0 - size * res)) * SLOPE)[:, None], size, axis=1).astype("float32")
    if nodata_right:
        z[:, size // 2:] = -9999
    fh = tempfile.NamedTemporaryFile(suffix=".tif", delete=False)
    fh.close()
    GDALRaster({"driver": "GTiff", "name": fh.name, "srid": srid, "width": size, "height": size,
                "origin": [x0, y0], "scale": [res, -res],
                "bands": [{"data": z.ravel().tolist(), "nodata_value": -9999}]})
    return fh.name


class SampleTests(SimpleTestCase):
    def test_transform_roundtrip(self):
        x, y = transform_xy([-151.19, -151.18], [59.62, 59.63], 4326, 32605)
        lon, lat = transform_xy(x, y, 32605, 4326)
        self.assertTrue(np.allclose(lon, [-151.19, -151.18]) and np.allclose(lat, [59.62, 59.63]))

    def test_cog_bilinear_and_gradient_in_eval_axes(self):
        # Raster in UTM zone 6, route evaluated in zone 5: grid north differs by
        # the convergence between zones, and the probes must absorb it.
        cx, cy = transform_xy([-150.0], [60.0], 4326, 32606)
        path = plane_tif(32606, cx[0] - 400, cy[0] + 400)
        src = CogSource("plane", path, res=2.0)
        ex, ey = transform_xy([-150.0], [60.0], 4326, 32605)
        z, gx, gy, idx, warn = sample_stack(ex, ey, 32605, [src])
        self.assertEqual((idx[0], warn), (0, []))
        self.assertAlmostEqual(math.hypot(gx[0], gy[0]), SLOPE, places=3)
        # In zone 5 at 150 W the zone-6 grid north is rotated ~2.6 degrees.
        self.assertGreater(abs(math.degrees(math.atan2(gx[0], gy[0]))), 1.0)

    def test_nodata_falls_through_to_next_source(self):
        cx, cy = transform_xy([-151.2], [59.6], 4326, 32605)
        half = plane_tif(32605, cx[0] - 400, cy[0] + 400, nodata_right=True)
        full = plane_tif(32605, cx[0] - 400, cy[0] + 400)
        x = np.array([cx[0] - 100, cx[0] + 100])
        y = np.array([cy[0], cy[0]])
        _, _, _, idx, _ = sample_stack(x, y, 32605, [CogSource("half", half, res=2), CogSource("full", full, res=2)])
        self.assertEqual(list(idx), [0, 1])

    def test_unreadable_source_is_a_warning(self):
        x, y = transform_xy([-151.2], [59.6], 4326, 32605)
        _, _, _, idx, warn = sample_stack(x, y, 32605, [CogSource("gone", "/nonexistent.tif")])
        self.assertEqual(idx[0], -1)
        self.assertIn("could not be read", warn[0])


class FlatTerrarium(TerrariumSource):
    """Terrarium without the network: every tile is a 100 m plateau."""
    def fetch_tile(self, z, x, y):
        return np.full((256, 256), 100.0, np.float32)


class EvaluateAlignmentTests(TestCase):
    def test_saved_evaluation_is_stored_per_leg(self):
        cx, cy = transform_xy([-151.2], [59.6], 4326, 32605)
        path = plane_tif(32605, cx[0] - 400, cy[0] + 400)
        p = Project.objects.create(slug="p", name="P")
        a = Alignment.objects.create(project=p, name="A")
        lon, lat = transform_xy([cx[0], cx[0], cx[0] + 200], [cy[0] - 100, cy[0] + 100, cy[0] + 100], 32605, 4326)
        Leg.objects.create(alignment=a, order=0, kind="existing",
                           geom=LineString(list(zip(lon[:2], lat[:2])), srid=4326))
        Leg.objects.create(alignment=a, order=1, kind="new",
                           geom=LineString(list(zip(lon[1:], lat[1:])), srid=4326))
        a.rebuild_from_legs()
        sources = [CogSource("plane", path, res=2.0), FlatTerrarium()]
        orig = evaluation.choose_sources
        evaluation.choose_sources = lambda line, project=None: sources
        try:
            evaluation.evaluate_alignment(a)
        finally:
            evaluation.choose_sources = orig
        a.refresh_from_db()
        self.assertEqual(a.evaluation_status, "done")
        self.assertTrue(a.runs_uphill)
        up, across = a.legs.order_by("order")
        self.assertAlmostEqual(up.stats["grade"]["p50"], 25.0, delta=0.5)      # straight up the plane
        self.assertAlmostEqual(across.stats["tsa"]["p50"], 90.0, delta=1.0)   # along the contour
        self.assertEqual(up.stats["effort"]["construct_days"], 0.0)           # existing trail
        self.assertGreater(across.stats["effort"]["construct_days"], 0.0)
        self.assertEqual(a.evaluation["summary"]["lidar_pct"], 100.0)
