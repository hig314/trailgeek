"""core.dem and core.evaluation against synthetic rasters (no network)."""
import math
import struct
import tempfile
import zlib

import numpy as np
from django.contrib.gis.gdal import GDALRaster
from django.contrib.gis.geos import LineString, MultiPolygon, Polygon
from django.test import SimpleTestCase, TestCase

from core import evaluation
from core.dem import CogSource, TerrariumSource, TileSource, decode_mapbox, sample_stack, transform_xy
from core.evaluation import choose_sources
from core.models import Alignment, DemSource, Leg, Project

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


def png_rgba(rgba):
    """A PNG file's bytes from an (h, w, 4) uint8 array."""
    h, w, _ = rgba.shape
    raw = b"".join(b"\x00" + rgba[r].tobytes() for r in range(h))

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


class TileSourceTests(SimpleTestCase):
    def test_decode_mapbox_with_nodata(self):
        rgba = np.zeros((2, 2, 4), np.uint8)
        v = int((123.4 + 10000) * 10)                     # 123.4 m
        rgba[0, 0] = [v >> 16, (v >> 8) & 255, v & 255, 255]
        rgba[0, 1] = [v >> 16, (v >> 8) & 255, v & 255, 0]  # transparent: no data
        rgba[1, 0] = [0, 0, 0, 255]                         # the -10000 m code: no data
        rgba[1, 1] = [v >> 16, (v >> 8) & 255, v & 255, 255]
        h = decode_mapbox(png_rgba(rgba))
        self.assertAlmostEqual(float(h[0, 0]), 123.4, places=3)
        self.assertTrue(np.isnan(h[0, 1]) and np.isnan(h[1, 0]))

    def test_tile_sources_do_not_share_cache_entries(self):
        class Const(TileSource):
            def __init__(self, key, value):
                super().__init__(key, key, "https://x/{z}/{x}/{y}.png", 13, data_res=10.0, encoding="mapbox")
                self.value = value

            def fetch_tile(self, z, x, y):
                return np.full((256, 256), self.value, np.float32)
        x, y = transform_xy([-151.2], [59.6], 4326, 32605)
        a, b = Const("ctx_a", 50.0), Const("ctx_b", 70.0)
        self.assertAlmostEqual(float(a.sample(x, y, 32605)[0]), 50.0)
        self.assertAlmostEqual(float(b.sample(x, y, 32605)[0]), 70.0)
        self.assertEqual(b.res, 10.0)


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


class ChooseSourcesTests(TestCase):
    def test_lidar_then_baked_context_then_terrarium(self):
        box = Polygon.from_bbox((-151.3, 59.55, -151.0, 59.7))
        box.srid = 4326
        DemSource.objects.create(slug="grewingk_2021", title="G", kind="lidar", native_res_m=0.5,
                                 cog_url="https://x/g.tif", footprint=MultiPolygon(box, srid=4326))
        DemSource.objects.create(slug="terrarium", title="T", kind="context", encoding="terrarium")
        ctx = DemSource.objects.create(slug="ctx_3dep", title="C", kind="context", encoding="mapbox",
                                       max_zoom=13, native_res_m=10.0, pmtiles_url="https://x/c.pmtiles")
        line = LineString((-151.19, 59.62), (-151.18, 59.63), srid=4326)
        # Without tile URLs the worker cannot read the bake (PMTiles), so it is skipped.
        self.assertEqual([s.key for s in choose_sources(line)], ["grewingk_2021", "terrarium"])
        ctx.tiles_url = "https://tiles.example/ctx_3dep/{z}/{x}/{y}.png?v=1"
        ctx.save()
        srcs = choose_sources(line)
        self.assertEqual([s.key for s in srcs], ["grewingk_2021", "ctx_3dep", "terrarium"])
        self.assertEqual((srcs[1].z, srcs[1].res, srcs[1].kind), (13, 10.0, "context"))
