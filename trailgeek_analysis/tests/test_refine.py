"""The refine tool's core against synthetic surfaces where the answers are
known (docs/REFINE_TOOL.md §5.1): a plane, a hairpin, a gully crossed while
climbing, and a kinked climb that the search must even out."""
import math
import unittest

import numpy as np

from trailgeek_analysis.refine import Grid, clean_settings, merge_settings, refine
from trailgeek_analysis.refine import spline
from trailgeek_analysis.refine.profile import taut_string
from trailgeek_analysis.refine.score import Problem, densify, intersects, polyline_distance, side_of_downhill, turning

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
        self.assertEqual(s["factors"]["corner"]["k"], 167)            # untouched default
        part = clean_settings({"factors": {"bend": {"k": 0}}}, partial=True)
        self.assertEqual(part, {"version": 2, "factors": {"bend": {"k": 0.0}}, "params": {}})

    def test_older_settings_are_read_in_metres(self):
        # Shares of 100 with a length weight (2026-10-17): per-metre weights
        # become k / length k, per-corner ones k / length k * 100 m; the old
        # |cut or fill| curve is mirrored to the cut side.
        s = clean_settings({"factors": {"grade": {"k": 27.1}, "corner": {"k": 13.5}, "length": {"k": 8.1},
                                        "cutfill": {"k": 13.5, "curve": [[0, 0.01], [0.2, 0.15], [0.5, 1]]}},
                            "params": {"fall_tsa_deg": 30}})
        self.assertAlmostEqual(s["factors"]["grade"]["k"], 3.3457, places=3)
        self.assertAlmostEqual(s["factors"]["corner"]["k"], 166.67, places=1)
        self.assertEqual(s["factors"]["cutfill"]["curve"], [[-0.5, 1], [-0.2, 0.15], [0, 0.01], [0.2, 0.15], [0.5, 1]])
        self.assertNotIn("length", s["factors"])
        self.assertEqual(s["params"]["fall_tsa_deg"], 22)               # the old default gives way to the new

    def test_rejects(self):
        bad = [
            {"factors": {"nope": {"k": 1}}},
            {"factors": {"grade": {"k": -1}}},
            {"factors": {"grade": {"curve": [[0, 0]]}}},
            {"factors": {"grade": {"curve": [[0, 0], [5, 2e4]]}}},
            {"factors": {"grade": {"curve": [[0, 0.01], [5, -0.5]]}}},
            {"factors": {"grade": {"curve": [[0, 1e-6], [5, 1]]}}},
            {"factors": {"grade": {"curve": [[5, 0], [1, 1]]}}},
            {"factors": {"grade": {"curve": [[i, 0] for i in range(10)]}}},
            {"params": {"iterations": 10 ** 9}},
            {"params": {"corridor_m": "wide"}},
        ]
        for raw in bad:
            with self.assertRaises(ValueError, msg=raw):
                clean_settings(raw)


class PieceTests(unittest.TestCase):
    def test_curves_are_log_linear_between_positive_anchors(self):
        from trailgeek_analysis.refine.score import curve_fn
        f = curve_fn([[0, 0.1], [10, 10]])
        np.testing.assert_allclose(f(np.array([-5.0, 5.0, 20.0])), [0.1, 1.0, 10.0])   # geometric, flat beyond
        g = curve_fn([[0, 0.01], [10, 1], [20, 4]])
        np.testing.assert_allclose(g(np.array([5.0, 15.0])), [0.1, 2.0])
        self.assertIsInstance(g(7.5), float)
        np.testing.assert_allclose(curve_fn([[0, 0], [10, 1]])(5.0), 0.1)             # an older curve's 0 reads as 0.01
        self.assertEqual(clean_settings({"factors": {"grade": {"curve": [[0, 0], [5, 1]]}}})["factors"]["grade"]["curve"][0][1], 0.01)

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

    def test_intersects_changed_agrees_with_the_full_check(self):
        from trailgeek_analysis.refine.score import intersects_changed
        base = np.column_stack([np.linspace(0, 100, 51), np.zeros(51)])
        bent = base.copy()
        bent[30:34] = [[60, 5], [62, 10], [40, -3], [42, -6]]          # loops back across itself
        self.assertTrue(intersects(bent, 1))
        self.assertTrue(intersects_changed(bent, base, 1))
        mild = base.copy(); mild[30:34, 1] += 2
        self.assertEqual(intersects_changed(mild, base, 1), intersects(mild, 1))

    def test_intersects(self):
        loop = np.array([[0, 0], [10, 0], [10, 10], [5, -5]], float)
        self.assertTrue(intersects(loop, 1))
        self.assertFalse(intersects(np.array([[0, 0], [10, 0], [20, 5], [30, 5]], float), 1))


class BendTests(unittest.TestCase):
    """Bends are measured finely (owner, 2026-10-18): 1 m and 3 m turns read apart."""

    def radius(self, line):
        _, r = turning(densify(line, 0.5))
        return float(r.min())

    def test_one_and_three_metre_turns_read_apart_from_a_kink(self):
        def turn(R):
            return np.vstack([np.column_stack([np.linspace(-20, 0, 11), np.zeros(11)]),
                              arc(0, R, R, -math.pi / 2, 0, n=30)[1:],
                              np.column_stack([np.full(10, R), R + np.linspace(2, 20, 10)])])
        kink = np.array([[-20.0, 0], [0, 0], [0, 20]])
        r1, r3, rk = self.radius(turn(1.0)), self.radius(turn(3.0)), self.radius(kink)
        self.assertTrue(0.8 < r1 < 1.2, r1)
        self.assertTrue(2.6 < r3 < 3.4, r3)
        self.assertLess(rk, 0.75)

    def test_a_drawn_arc_reads_its_radius_when_its_vertices_are_close(self):
        self.assertAlmostEqual(self.radius(arc(0, 0, 10.0, 0, math.pi / 2, n=40)), 10.0, delta=1.0)
        # Vertices 2.2 m apart: each is a kink at the 1 m scale, so tighter.
        self.assertLess(self.radius(arc(0, 0, 10.0, 0, math.pi / 2, n=8)), 6.0)


class ExistingTreadTests(unittest.TestCase):
    """Off existing tread (owner, 2026-10-18): a line on the existing trail
    pays the floor, one beside it pays for new tread, and with no existing
    trail selected the factor is off."""

    def test_new_tread_costs_and_the_existing_trail_does_not(self):
        g = Grid.from_function(lambda X, Y: 0.05 * Y, -60, -60, 60, 160)
        trail = np.array([[0, 0], [0, 100]], float)
        s = clean_settings({"params": {"spacing_m": 2.0}})
        prob = Problem([trail], g, s, existing_xy=[trail])
        on = prob.score_line(prob.P, 0.1)
        beside = prob.score_line(prob.P + np.array([8.0, 0.0]), 0.1)
        self.assertLess(on["costs"]["existing"], 2.0)             # the 0.01 floor over ~100 m
        self.assertGreater(beside["costs"]["existing"], 90.0)     # curve 1 for every metre
        none = Problem([trail], g, s).score_line(Problem([trail], g, s).P, 0.1)
        self.assertEqual(none["costs"]["existing"], 0.0)


class RouteTests(unittest.TestCase):
    """The route-finder (owner, 2026-10-18) winds up a slope the line goes
    straight up, when the grade curve makes steep ground dear."""

    def test_a_straight_line_up_a_steep_face_becomes_switchbacks(self):
        from trailgeek_analysis.refine.route import route_line
        g = Grid.from_function(lambda X, Y: 0.4 * Y, -80, -40, 80, 160)
        s = clean_settings({"params": {"corridor_m": 40}, "factors": {
            "grade": {"k": 4, "curve": [[0, 0.01], [12, 0.01], [16, 1], [25, 10], [60, 100]]}}})
        prob = Problem([np.array([[0, 0], [0, 100]], float)], g, s)
        R = route_line(prob)
        self.assertIsNotNone(R)
        seg = np.diff(R, axis=0)
        L = np.hypot(*seg.T)
        self.assertGreater(L.sum(), 250)                                  # 40 m up at <= ~16 %: at least 250 m of trail
        self.assertLess((40 * np.abs(seg[:, 1]) / np.maximum(L, 1e-9)).max(), 20)   # no step climbs steeper than ~20 %
        self.assertTrue((np.hypot(*(R[[0, -1]] - [[0, 0], [0, 100]]).T) < 1e-6).all())   # same ends
        self.assertLessEqual(prob.dist_grid.sample(*R.T).max(), 40)

    def test_on_gentle_ground_the_route_is_nearly_straight(self):
        from trailgeek_analysis.refine.route import route_line
        g = Grid.from_function(lambda X, Y: 0.05 * Y, -60, -40, 60, 160)
        prob = Problem([np.array([[0, 0], [0, 100]], float)], g, clean_settings({}))
        R = route_line(prob)
        self.assertLess(np.hypot(*np.diff(R, axis=0).T).sum(), 110)


    def test_routes_at_several_turn_weights_share_one_reading(self):
        # (2026-10-21) Cheaper turns, more switchbacks; dearer, fewer: layouts
        # the search's own moves cannot make. The first route is route_line's.
        from trailgeek_analysis.refine.route import route_line, route_lines
        g = Grid.from_function(lambda X, Y: 0.4 * Y, -80, -40, 80, 160)
        s = clean_settings({"params": {"corridor_m": 40}, "factors": {
            "grade": {"k": 4, "curve": [[0, 0.01], [12, 0.01], [16, 1], [25, 10], [60, 100]]}}})
        prob = Problem([np.array([[0, 0], [0, 100]], float)], g, s)
        R1, Rlo, Rhi = route_lines(prob, None, (1.0, 0.05, 20.0))
        np.testing.assert_allclose(R1, route_line(prob))

        def turns(R):
            seg = np.diff(R, axis=0)
            hd = np.degrees(np.arctan2(seg[:, 1], seg[:, 0]))
            return int((np.abs((np.diff(hd) + 180) % 360 - 180) >= 60).sum())
        self.assertGreaterEqual(turns(Rlo), turns(R1))
        self.assertLessEqual(turns(Rhi), turns(R1))
        self.assertGreater(turns(Rlo), turns(Rhi))

    def test_refine_runs_a_chain_from_each_route(self):
        g = Grid.from_function(lambda X, Y: 0.4 * Y, -80, -40, 80, 160)
        s = clean_settings({"params": {"corridor_m": 40, "chains": 1, "iterations": 60, "seed": 3}, "factors": {
            "grade": {"k": 4, "curve": [[0, 0.01], [12, 0.01], [16, 1], [25, 10], [60, 100]]}}})
        from trailgeek_analysis.refine.route import route_lines
        from trailgeek_analysis.refine.search import ROUTE_TURN_X
        line = np.array([[0, 0], [0, 100]], float)
        out = refine([line], g, s)
        starts = out["route"]["starts"]
        distinct = []
        for R in route_lines(Problem([line], g, s), None, ROUTE_TURN_X):
            if R is not None and not any(len(R) == len(D) and np.allclose(R, D) for D in distinct):
                distinct.append(R)
        self.assertEqual(len(starts), len(distinct))               # the same route twice is searched once
        self.assertTrue(all(st["polished"] is not None or st["as_found"] is not None for st in starts))


class GullyTests(unittest.TestCase):
    """A gully narrower than the 10 m grade window (owner, 2026-10-19, the
    Graduation Peak Trail): down one wall and up the other used to read as
    level, in the scoring and in the route-finder's long steps."""
    STEEP = {"grade": {"k": 4, "curve": [[0, 0.01], [12, 0.01], [25, 10], [60, 100]]}}

    def test_a_dip_through_a_gully_reads_as_both_its_walls(self):
        # Level ground with a V gully 3 m deep and 6 m wide along y = 50.
        g = Grid.from_function(lambda X, Y: -3 * np.clip(1 - np.abs(Y - 50) / 3, 0, None), -40, -20, 40, 120)
        prob = Problem([np.array([[0, 0], [0, 100]], float)], g, clean_settings({"params": {"spacing_m": 1.0}}))
        r = prob.score_line(prob.P, 0.02, detail=True)
        self.assertGreater(r["grade"].max(), 50)          # 6 m down and up within 10 m; read as ~0 before
        self.assertLess(r["grade"][r["d"] < 30].max(), 1)  # level ground stays level

    def test_the_route_does_not_leap_a_gully_it_can_go_round(self):
        from trailgeek_analysis.refine.route import route_line
        # A trench 3 m deep and 3 m wide along y = 50, with a causeway at
        # x 28-34; a 6 m step from rim to rim read it as level.
        g = Grid.from_function(lambda X, Y: np.where((np.abs(Y - 50) < 1.5) & ((X < 28) | (X > 34)), -3.0, 0.0),
                               -60, -20, 60, 120)
        only_grade = dict(self.STEEP, slope={"k": 0}, tsa={"k": 0}, falltread={"k": 0})
        prob = Problem([np.array([[0, 0], [0, 100]], float)], g,
                       clean_settings({"params": {"corridor_m": 40}, "factors": only_grade}))
        Q = densify(route_line(prob), 0.5)
        at = Q[np.abs(Q[:, 1] - 50) < 1.5, 0]
        self.assertTrue(((at > 26) & (at < 36)).all(), at)  # over the causeway (the leap was at x = 0)


class ExcessClimbTests(unittest.TestCase):
    """Excess climb (owner, 2026-10-19): a stretch must gain its net height;
    every metre lost on the way is won back, and charged."""

    def test_losses_are_read_through_the_dead_band(self):
        from trailgeek_analysis.refine.score import height_lost
        # A 0.2 m dip is free; a 2.5 m descent with a 0.2 m rise inside it is one loss.
        self.assertEqual(height_lost([0, 1, 2, 1.8, 3, 1, 1.2, 0.5, 4, 5], 0.5), [(4, 7, 2.5)])
        self.assertEqual(height_lost([5, 4, 4.5, 2, 0], 0.5), [(1, 2, 0.5)])     # a net descent: its climbs
        self.assertEqual(height_lost([0, 1, 0.7, 2], 0.5), [])
        self.assertEqual(height_lost([0, 2, 1], 0.5), [(1, 2, 1.0)])             # lost at the end, still lost

    def test_a_gully_on_a_climb_costs_its_depth(self):
        # A 10 % climb with a box trench 2 m deep and 4 m wide across it.
        g = Grid.from_function(lambda X, Y: 0.1 * Y - np.where(np.abs(Y - 50) < 2, 2.0, 0.0), -40, -20, 40, 120)
        line = [np.array([[0, 0], [0, 100]], float)]
        for band, lost in ((0.5, 2.0), (2.5, 0.0)):
            s = clean_settings({"params": {"spacing_m": 1.0, "climb_band_m": band},
                                "factors": {"climb": {"k": 5, "curve": [[0, 1], [10, 1]]}}})
            r = Problem(line, g, s).score_line(Problem(line, g, s).P, 0.02, detail=True)
            self.assertAlmostEqual(r["costs"]["climb"], lost, delta=0.15)
            self.assertAlmostEqual(r["contrib"]["climb"], 5 * lost, delta=0.75)

    def test_off_by_default(self):
        g = Grid.from_function(lambda X, Y: 0.1 * Y - np.where(np.abs(Y - 50) < 2, 2.0, 0.0), -40, -20, 40, 120)
        prob = Problem([np.array([[0, 0], [0, 100]], float)], g, clean_settings({}))
        self.assertEqual(prob.score_line(prob.P, 0.02, detail=True)["contrib"]["climb"], 0)

    def test_the_route_climbs_round_a_trench_rather_than_through_it(self):
        from trailgeek_analysis.refine.route import route_line
        # A 10 % climb with a trench 3 m deep across it and a causeway at x 28-34;
        # only excess climb is charged, so the straight line's only cost is its loss.
        g = Grid.from_function(lambda X, Y: 0.1 * Y - np.where((np.abs(Y - 50) < 1.5) & ((X < 28) | (X > 34)), 3.0, 0.0),
                               -60, -20, 60, 120)
        off = {f: {"k": 0} for f in ("grade", "slope", "tsa", "falltread", "corner", "bend")}
        prob = Problem([np.array([[0, 0], [0, 100]], float)], g,
                       clean_settings({"params": {"corridor_m": 40}, "factors": dict(off, climb={"k": 20})}))
        Q = densify(route_line(prob), 0.5)
        at = Q[np.abs(Q[:, 1] - 50) < 1.5, 0]
        self.assertTrue(((at > 26) & (at < 36)).all(), at)


class TurnChargeTests(unittest.TestCase):
    """The route-finder charges a turn at the radius the scoring will read
    for it (owner, 2026-10-19: hairpins charged as 4 m turns came out as
    0.6 m kinks the scoring charged far more, and the polish knotted them)."""

    def test_where_tight_corners_are_dear_the_route_makes_fewer(self):
        from trailgeek_analysis.refine.route import route_line
        g = Grid.from_function(lambda X, Y: 0.4 * Y, -80, -40, 80, 160)
        sharp = []
        for curve in ([[0, 0.01], [30, 0.01]], [[0, 10], [2.5, 10], [3, 0.01]]):
            s = clean_settings({"params": {"corridor_m": 40}, "factors": dict(GullyTests.STEEP, corner={
                "k": 50, "curve": curve})})
            R = route_line(Problem([np.array([[0, 0], [0, 100]], float)], g, s))
            self.assertGreater(np.hypot(*np.diff(R, axis=0).T).sum(), 200)    # it winds up the face either way
            turn, _ = turning(densify(R, 0.5))
            sharp.append(np.degrees(turn[turn > math.radians(60)]).sum())
        # Turns on the 2 m grid read as kinks under 1.3 m; charged as 4 m
        # turns, both curves gave the same route (1588 degrees of them).
        self.assertLess(sharp[1], 0.8 * sharp[0])


class AdaptiveSpacingTests(unittest.TestCase):
    """Curvature-dependent spacing (owner, 2026-10-18): samples close up
    where the line turns, so a tight corner is resolved."""

    def test_samples_close_up_in_a_hairpin_and_spread_on_the_straight(self):
        from trailgeek_analysis.refine.score import adaptive_resample
        line = np.vstack([np.column_stack([np.zeros(21), np.linspace(0, 40, 21)]),
                          arc(2, 40, 2.0, math.pi, 0, n=30)[1:],
                          np.column_stack([np.full(20, 4.0), np.linspace(38, 0, 20)])])
        Q = adaptive_resample(line, 2.0)
        ds = np.hypot(*np.diff(Q, axis=0).T)
        in_turn = Q[:-1, 1] > 40.5
        self.assertLess(ds[in_turn].max(), 0.5)            # 10 degrees of a 2 m radius is 0.35 m
        self.assertGreater(ds[Q[:-1, 1] < 30].min(), 1.5)  # straight: about the usual 2 m
        self.assertTrue(np.allclose(Q[[0, -1]], line[[0, -1]]))


class CornerTests(unittest.TestCase):
    def score(self, legs, grid, **params):
        s = clean_settings({"params": {"spacing_m": 1.0, **params}})
        prob = Problem(legs, grid, s)
        return prob.score_line(prob.P, 0.1, detail=True)            # the line as given

    def test_a_hairpin_of_any_radius_is_one_corner(self):
        # Corners up to 45 m used to count and wider ones did not (the turn
        # had to be tighter than 50 m), so the search widened corners past it.
        g = Grid.from_function(lambda X, Y: PLANE * Y, -60, -60, 400, 400, 1.0)
        for R in (5, 20, 45, 60, 120):
            r = self.score([self.hairpin(R, straight=80)], g)
            self.assertEqual(len(r["corners"]), 1, R)
            self.assertTrue(0.8 * R < r["corners"][0]["radius_m"] < 1.25 * R, (R, r["corners"]))

    def test_a_steep_switchback_is_dug_in_and_a_gentle_one_is_not(self):
        # 180 degrees at 8 m: on 35 % ground the turn climbs 2 r S = 5.6 m in
        # pi r = 25 m (22 %), so cutting in pays; on 12 % ground (8 % through
        # the turn) it does not. Grade weighted up, so the trade is clear-cut
        # (with the defaults a 35 % hairpin is about even: 0.25 m).
        digs = {}
        for slope in (0.12, 0.35):
            g = Grid.from_function(lambda X, Y: slope * Y, -60, -60, 260, 140, 1.0)
            s = clean_settings({"factors": {"grade": {"k": 10}}, "params": {"spacing_m": 1.0}})
            prob = Problem([self.hairpin(8.0)], g, s)
            r = prob.score_line(prob.P, 0.1, detail=True)
            self.assertEqual(len(r["corners"]), 1, slope)
            digs[slope] = r["corners"][0]["dig_m"]
        self.assertLess(digs[0.12], 0.2)
        self.assertGreater(digs[0.35], 0.4)

    def test_fill_costs_more_than_cut(self):
        s = clean_settings({"params": {"spacing_m": 1.0}})
        prob = Problem([self.hairpin(8.0)], plane_grid(), s)
        f = prob.f["cutfill"]
        self.assertGreater(f(0.3), 5 * f(-0.3))
        self.assertTrue(0.2 < prob.fill_for(0.5) < 0.3)               # the fill that costs as much as 0.5 m of cut

    def test_length_is_along_the_slope(self):
        g = Grid.from_function(lambda X, Y: 0.1 * Y, -60, -60, 60, 160)
        r = self.score([np.array([[0, 0], [0, 100]], float)], g)
        self.assertAlmostEqual(r["length_m"], 100 * math.hypot(1, 0.1), delta=0.2)
        self.assertAlmostEqual(r["costs"]["length"], r["length_m"])

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

    def test_corner_kind_follows_the_ground_where_it_crosses_the_fall_line(self):
        kinds = {}
        for slope in (0.12, 0.35):
            g = Grid.from_function(lambda X, Y: slope * Y, -60, -60, 160, 120)
            kinds[slope] = [c["kind"] for c in self.score([self.hairpin(10)], g)["corners"]]
        self.assertEqual(kinds, {0.12: ["climbing turn"], 0.35: ["switchback-like"]})

    def test_fall_line_tread_is_free_at_5_to_8_percent_and_costs_either_side(self):
        cost = {}
        for slope in (0.045, 0.065, 0.14):
            g = Grid.from_function(lambda X, Y: slope * Y, -60, -60, 60, 160)
            down_fall = self.score([np.array([[0, 0], [0, 100]], float)], g)
            cost[slope] = down_fall["costs"]["falltread"] / down_fall["length_m"]   # per metre
            contour = self.score([np.array([[-50, 50], [50, 50]], float)], g)
            self.assertEqual(contour["costs"]["falltread"], 0.0)      # across the slope: never fall-line
        self.assertLess(cost[0.065], 0.012)                             # the 0.01 floor: next to nothing
        self.assertGreater(cost[0.045], 0.1)                            # pools
        self.assertGreater(cost[0.14], 0.1)                             # erodes

    def test_gully_crossed_while_climbing_is_a_bend_not_a_corner(self):
        gully = Grid.from_function(lambda X, Y: PLANE * Y - 3 * np.exp(-((X - 50) ** 2) / 72), -60, -60, 160, 80)
        line = np.array([[0, 0], [50, 12], [100, 0]], float)          # a V in plan, into the gully
        r = self.score([line], gully)
        self.assertEqual(len(r["corners"]), 0)
        self.assertGreater(r["costs"]["bend"], 0)


class SketchTests(unittest.TestCase):
    """The person's line as evidence of a smooth intent (2026-10-17): the
    start is a smooth fit to it, rounded more for a loose sketch than for a
    precise line, inside the search's limit."""
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

    def test_a_narrow_search_keeps_a_sketched_corner_tight(self):
        # No allowance at sketched corners (owner, 2026-10-18): within 2 m of
        # a square corner, the start rounds it only as far as 2 m allows.
        prob = Problem(self.SQUARE, plane_grid(-60, -120, 260, 160),
                       clean_settings({"params": {"input_sigma_m": 3.0, "corridor_m": 2}}))
        r = prob.score(prob.start(), 0.1, detail=True)
        self.assertIsNotNone(r)
        self.assertLessEqual(r["dist"].max(), 2 + 1e-6)
        wide = Problem(self.SQUARE, plane_grid(-60, -120, 260, 160), clean_settings({"params": {"input_sigma_m": 3.0}}))
        self.assertLess(r["bend_r"].min(), wide.score(wide.start(), 0.1, detail=True)["bend_r"].min())

    def test_distance_from_the_line_is_a_limit_not_a_cost(self):
        s = clean_settings({"factors": {"offset": {"k": 5}}, "params": {"corridor_m": 6, "iterations": 300, "chains": 2}})
        self.assertNotIn("offset", s["factors"])                       # an older layer's weight is ignored
        r = refine(self.SQUARE, plane_grid(-60, -120, 260, 160), s, seed=3)
        for c in r["candidates"]:
            self.assertLessEqual(c["stats"]["offset_max"], 6 + 0.01)
            self.assertNotIn("offset", c["contrib"])

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
        self.assertGreater(r["candidates"][0]["stats"]["bend_min_r"], 2.5 * r["input"]["stats"]["bend_min_r"])


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

    def test_best_so_far_starts_at_the_input_and_only_goes_down(self):
        calls = []
        s = clean_settings({"factors": {"drainage": {"k": 0}}, "params": {"iterations": 300, "chains": 3}})
        r = refine(self.LEGS, plane_grid(), s, seed=3, progress=lambda f, b, stage: calls.append((f, b, stage)))
        seen = [b for _, b, _ in calls if b is not None]
        self.assertLessEqual(max(seen), r["input"]["total"] + 1e-6)
        self.assertTrue(all(b2 <= b1 + 1e-9 for b1, b2 in zip(seen, seen[1:])), seen)
        # Each step says what it is, and the bar only moves forward, to 1.
        fr = [f for f, _, _ in calls]
        self.assertTrue(all(f2 >= f1 - 1e-9 for f1, f2 in zip(fr, fr[1:])), fr)
        self.assertAlmostEqual(fr[-1], 1.0)
        stages = {st.split(":")[0] for _, _, st in calls}
        self.assertTrue({"Scoring your line", "Route-finder", "Search 1 of 3", "Search 3 of 3",
                         "Polishing the route-finder's line", "Comparing the candidates"} <= stages, stages)

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


class TwiTests(unittest.TestCase):
    """The light wetness index: a V valley draining south, with a pit."""

    def valley(self):
        x = np.arange(0, 400, 5.0)
        X, Y = np.meshgrid(x, x)
        z = 0.3 * np.abs(X - 200) + 0.1 * Y
        return z

    def test_valley_floor_is_wetter_than_its_sides_and_the_pit_is_filled(self):
        from trailgeek_analysis.refine.twi import fill_pits, twi
        z = self.valley()
        z[40, 10] -= 3
        f = fill_pits(z)
        self.assertGreater(f[40, 10], z[40, 10] + 0.9)                      # filled to its spill point
        self.assertGreater(f[40, 10], f[39, 11])                            # and drains
        t, a, _ = twi(z, 5.0)
        self.assertGreater(t[2, 40], 11)                                    # the floor, low end
        self.assertLess(t[40, 70], 7)                                       # a side slope
        self.assertGreater(a[2, 40], a[70, 40])                             # area grows downstream

    def test_no_data_is_left_out(self):
        from trailgeek_analysis.refine.twi import twi
        z = self.valley()
        z[:, :20] = np.nan
        t, _, _ = twi(z, 5.0)
        self.assertTrue(np.isnan(t[:, :20]).all())
        self.assertTrue(np.isfinite(t[:, 20:]).all())

    def test_refine_charges_wet_ground(self):
        from trailgeek_analysis.refine.twi import twi
        g = plane_grid()
        wet = Grid(np.full_like(g.z, 13.0), g.x0, g.y0, g.res)
        s = clean_settings({})
        s["params"]["chains"] = 0                                           # score the input only, as the baseline does
        line = [np.array([[0.0, 0.0], [200.0, 40.0]])]
        dry = refine(line, g, s)["input"]
        soaked = refine(line, g, s, twi_grid=wet)["input"]
        self.assertEqual(dry["contrib"]["wet"], 0)
        self.assertGreater(soaked["contrib"]["wet"], 0)
        self.assertGreater(soaked["stats"]["wet_m"], 190)
