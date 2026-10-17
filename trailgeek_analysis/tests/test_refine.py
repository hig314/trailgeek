"""The refine tool's core against synthetic surfaces where the answers are
known (docs/REFINE_TOOL.md §5.1): a plane, a hairpin, a gully crossed while
climbing, and a kinked climb that the search must even out."""
import math
import unittest

import numpy as np

from trailgeek_analysis.refine import Grid, clean_settings, merge_settings, refine
from trailgeek_analysis.refine import spline
from trailgeek_analysis.refine.profile import taut_string
from trailgeek_analysis.refine.score import Problem, intersects, polyline_distance, side_of_downhill

PLANE = 0.3          # ground rising 30 % to the north


def plane_grid(xmin=-60, ymin=-60, xmax=260, ymax=140):
    return Grid.from_function(lambda X, Y: PLANE * Y, xmin, ymin, xmax, ymax, 1.0)


def arc(cx, cy, r, a0, a1, n=40):
    a = np.linspace(a0, a1, n)
    return np.column_stack([cx + r * np.cos(a), cy + r * np.sin(a)])


class SettingsTests(unittest.TestCase):
    def test_merge_and_clean(self):
        s = merge_settings({"factors": {"grade": {"k": 3}}}, {"params": {"iterations": 99}})
        self.assertEqual(s["factors"]["grade"]["k"], 3)
        self.assertEqual(s["params"]["iterations"], 99)
        self.assertEqual(s["factors"]["corner"]["k"], 5)           # untouched default
        part = clean_settings({"factors": {"bend": {"k": 0}}}, partial=True)
        self.assertEqual(part, {"factors": {"bend": {"k": 0.0}}, "params": {}})

    def test_rejects(self):
        bad = [
            {"factors": {"nope": {"k": 1}}},
            {"factors": {"grade": {"k": -1}}},
            {"factors": {"grade": {"curve": [[0, 0]]}}},
            {"factors": {"grade": {"curve": [[0, 0], [5, 1.5]]}}},
            {"factors": {"grade": {"curve": [[5, 0], [1, 1]]}}},
            {"factors": {"grade": {"curve": [[i, 0] for i in range(10)]}}},
            {"params": {"iterations": 10 ** 9}},
            {"params": {"corridor_m": "wide"}},
        ]
        for raw in bad:
            with self.assertRaises(ValueError, msg=raw):
                clean_settings(raw)


class PieceTests(unittest.TestCase):
    def test_grid_bilinear_is_exact_on_a_plane(self):
        g = plane_grid()
        x, y = np.array([0.3, 10.7, -5.5]), np.array([1.25, 33.3, 7.9])
        np.testing.assert_allclose(g.sample(x, y), PLANE * y)
        self.assertTrue(np.isnan(g.sample(np.array([1e6]), np.array([0.0]))[0]))

    def test_spline_pins_its_ends(self):
        B = spline.basis(np.linspace(0, 1, 50), 8)
        np.testing.assert_allclose(B.sum(1), 1, atol=1e-9)             # partition of unity
        self.assertAlmostEqual(B[0, 0], 1)
        self.assertAlmostEqual(B[-1, -1], 1, places=6)

    def test_taut_string(self):
        d = np.arange(0, 101, 1.0)
        g = 0.1 * d + np.where((d > 40) & (d < 50), 1.0, 0.0)          # a 1 m step up and back
        z = taut_string(d, g - 0.3, g + 0.3)
        self.assertTrue((np.abs(z - g) <= 0.3 + 1e-9).all())          # inside the tube
        self.assertAlmostEqual(z[0], g[0]); self.assertAlmostEqual(z[-1], g[-1])   # ends pinned
        self.assertLess(np.abs(np.diff(z)).max(), np.abs(np.diff(g)).max())
        # A straight climb inside a wide tube stays straight.
        z2 = taut_string(d, 0.1 * d - 0.5, 0.1 * d + 0.5)
        np.testing.assert_allclose(np.diff(z2), 0.1, atol=1e-9)

    def test_side_of_downhill_holds_through_flat(self):
        s = side_of_downhill(np.array([0.0, 0.3, 0.01, -0.01, 0.2, -0.3]), 0.1)
        self.assertEqual(s.tolist(), [1, 1, 1, 1, 1, -1])

    def test_intersects(self):
        loop = np.array([[0, 0], [10, 0], [10, 10], [5, -5]], float)
        self.assertTrue(intersects(loop, 1))
        self.assertFalse(intersects(np.array([[0, 0], [10, 0], [20, 5], [30, 5]], float), 1))


class CornerTests(unittest.TestCase):
    def score(self, legs, grid, **params):
        s = clean_settings({"params": {"spacing_m": 1.0, **params}})
        prob = Problem(legs, grid, s)
        return prob.score_line(prob.P, 0.1, detail=True)            # the line as given

    def test_hairpin_is_one_corner_of_about_its_radius(self):
        R = 8.0
        line = np.vstack([np.column_stack([np.linspace(0, 50, 26), np.zeros(26)]),
                          arc(50, R, R, -math.pi / 2, math.pi / 2)[1:],
                          np.column_stack([np.linspace(50, 0, 26), np.full(26, 2 * R)])[1:]])
        r = self.score([line], plane_grid())
        self.assertEqual(len(r["corners"]), 1)
        self.assertTrue(0.7 * R < r["corners"][0]["radius_m"] < 1.5 * R, r["corners"])
        self.assertGreater(r["costs"]["corner"], 0)

    def hairpin(self, R, straight=60):
        m = max(2, int(straight / 2))
        return np.vstack([np.column_stack([np.linspace(0, straight, m), np.zeros(m)]),
                          arc(straight, R, R, -math.pi / 2, math.pi / 2, n=int(math.pi * R) + 4)[1:],
                          np.column_stack([np.linspace(straight, 0, m), np.full(m, 2 * R)])[1:]])

    def test_broad_corner_through_noisy_fall_line_is_one_corner(self):
        # A 25 m hairpin on a 30 % slope whose ground has ±0.25 m of lidar-like
        # roughness: at the apex the trail runs down the fall line, where the
        # roughness flips the cross slope back and forth.
        rng = np.random.default_rng(3)
        noise = rng.normal(0, 0.25, (241, 241))
        rough = Grid(PLANE * np.arange(-60, 181)[:, None] + noise, -60, -60, 1.0)
        r = self.score([self.hairpin(25)], rough)
        self.assertEqual(len(r["corners"]), 1, r["corners"])
        self.assertGreater(r["corners"][0]["arc_radius_m"], 15)

    def test_side_change_on_a_straight_line_is_not_a_corner(self):
        # The fall line turns from south to north under a straight traverse.
        g = Grid.from_function(lambda X, Y: PLANE * Y * np.tanh((X - 50) / 5), -60, -60, 160, 60)
        r = self.score([np.array([[0, 0], [100, 0]], float)], g)
        self.assertEqual(r["corners"], [])

    def test_a_kink_inside_a_broad_corner_sets_its_radius(self):
        line = self.hairpin(20)
        i = len(line) // 2 - 1                                          # the apex, heading north
        kinked = line.copy()
        kinked[i:i + 3] += [[1.0, 0], [1.8, 0], [1.0, 0]]               # a sharp outward jog across the track
        smooth, rough = (self.score([L], plane_grid())["corners"] for L in (line, kinked))
        self.assertEqual((len(smooth), len(rough)), (1, 1))
        self.assertLess(rough[0]["radius_m"], 0.5 * smooth[0]["radius_m"])

    def test_gully_crossed_while_climbing_is_a_bend_not_a_corner(self):
        gully = Grid.from_function(lambda X, Y: PLANE * Y - 3 * np.exp(-((X - 50) ** 2) / 72), -60, -60, 160, 80)
        line = np.array([[0, 0], [50, 12], [100, 0]], float)          # a V in plan, into the gully
        r = self.score([line], gully)
        self.assertEqual(len(r["corners"]), 0)
        self.assertGreater(r["costs"]["bend"], 0)


class SketchTests(unittest.TestCase):
    """The person's line as evidence of a smooth intent (2026-10-17): the
    start is a smooth fit to it, rounded more for a loose sketch than for a
    precise line, and rounding a sketched corner costs no distance."""
    SQUARE = [np.array([[0, 0], [100, 0], [100, 80]], float)]

    def start(self, legs, sigma, grid=None):
        prob = Problem(legs, grid or plane_grid(-60, -120, 260, 160), clean_settings({"params": {"input_sigma_m": sigma}}))
        return prob, prob.score(prob.start(), 0.1, detail=True)

    def test_a_sketched_corner_is_rounded_by_how_loose_the_sketch_is(self):
        _, tight = self.start(self.SQUARE, 0.5)
        _, loose = self.start(self.SQUARE, 3.0)
        self.assertIsNotNone(tight); self.assertIsNotNone(loose)
        self.assertLess(tight["bend_r"].min(), 6)
        self.assertTrue(7 < loose["bend_r"].min() < 20, loose["bend_r"].min())
        self.assertLess(tight["dist"].max(), 1.5)                       # a precise line stays put
        np.testing.assert_allclose(loose["Q"][0], [0, 0], atol=1e-6)  # ends pinned
        np.testing.assert_allclose(loose["Q"][-1], [100, 80], atol=1e-6)

    def test_a_sketched_hairpin_gives_a_clean_start(self):
        for legs in ([np.array([[0, 0], [100, 0], [20, 30]], float)], [np.array([[0, 0], [100, 0], [0, 6]], float)]):
            prob, r = self.start(legs, 3.0)
            self.assertIsNotNone(r, legs)
            self.assertFalse(prob.folds(prob.start()))

    def test_rounding_a_sketched_corner_to_the_sketch_radius_is_free(self):
        prob, _ = self.start(self.SQUARE, 0.0)
        R = prob.R_sketch
        a = np.linspace(-np.pi / 2, 0, 30)
        arc = np.column_stack([100 - R + R * np.cos(a), R + R * np.sin(a)])
        self.assertLess(prob.eff_grid.sample(arc[:, 0], arc[:, 1]).max(), 0.6)
        far = prob.eff_grid.sample(np.array([50.0]), np.array([-10.0]))[0]
        self.assertGreater(far, 9)                                    # off a straight stretch it costs

    def test_respacing_keeps_the_shape(self):
        prob, _ = self.start(self.SQUARE, 3.0)
        c = prob.start()
        # Bunch the controls on the first straight by sliding them along it.
        first = np.flatnonzero(c[:, 1] < 0.5)[2:8]
        c[first, 0] = np.linspace(c[first[0], 0], c[first[0], 0] + 3, len(first))
        c2 = prob.respace(c)
        self.assertIsNotNone(c2)
        gap = lambda cc: np.diff(cc[first[0] - 1:first[-1] + 2, 0])
        self.assertLess(gap(c2).std(), gap(c).std())                    # evened out
        before, after = prob.curve(c), prob.curve(c2)
        dev, _ = polyline_distance(after[:, 0], after[:, 1], before)
        self.assertLess(dev.max(), 0.3)                                 # same shape

    def test_refining_a_sketch_smooths_its_corner(self):
        s = clean_settings({"factors": {"drainage": {"k": 0}}, "params": {"iterations": 400, "chains": 2}})
        r = refine(self.SQUARE, plane_grid(-60, -120, 260, 160), s, seed=6)
        self.assertTrue(r["candidates"])
        self.assertGreater(r["candidates"][0]["stats"]["bend_min_r"], 3 * r["input"]["stats"]["bend_min_r"])


class RefineTests(unittest.TestCase):
    LEGS = [np.array([[0, 0], [100, 60]], float), np.array([[100, 60], [200, 60]], float)]
    # 15 % up to a kink, then level: the climb should be spread out.

    def run_refine(self, seed=11, **params):
        s = clean_settings({"factors": {"drainage": {"k": 0}},
                            "params": {"iterations": 600, "chains": 2, **params}})
        return refine(self.LEGS, plane_grid(), s, seed=seed)

    def test_evens_out_a_kinked_climb(self):
        r = self.run_refine()
        self.assertTrue(r["candidates"], "no candidate offered")
        c = r["candidates"][0]
        self.assertLess(c["total"], r["input"]["total"])
        self.assertLess(c["stats"]["grade_max"], r["input"]["stats"]["grade_max"])
        self.assertLessEqual(c["stats"]["offset_max"], 25 + 1e-6)
        legs = [np.array(L) for L in c["legs_xy"]]
        self.assertEqual(len(legs), 2)
        np.testing.assert_allclose(legs[0][0], [0, 0], atol=1e-6)      # ends fixed
        np.testing.assert_allclose(legs[1][-1], [200, 60], atol=1e-6)
        np.testing.assert_allclose(legs[0][-1], legs[1][0])            # legs still meet
        self.assertIn("grade", r["histograms"])

    def test_seeded_runs_repeat(self):
        a, b = self.run_refine(seed=5, iterations=150), self.run_refine(seed=5, iterations=150)
        self.assertEqual([c["total"] for c in a["candidates"]], [c["total"] for c in b["candidates"]])

    def test_a_hand_drawn_hairpin_vertex_is_refined_not_refused(self):
        legs = [np.array([[0, 0], [100, 30], [0, 31]], float)]          # turns back within a metre
        s = clean_settings({"params": {"iterations": 200, "chains": 1}})
        r = refine(legs, plane_grid(), s, seed=2)
        self.assertEqual(r["input"]["stats"]["corners"], 1)

    def test_input_off_the_terrain_is_refused(self):
        small = Grid.from_function(lambda X, Y: PLANE * Y, 0, 0, 20, 20)
        s = clean_settings({"params": {"iterations": 50, "chains": 1}})
        with self.assertRaises(ValueError):
            refine(self.LEGS, small, s, seed=1)
