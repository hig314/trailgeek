"""The methods page's analytic sandbox (trailgeek_analysis/sandbox.py): the
formula is what it says, and a tracing step stays in its strip."""
import math
import unittest

import numpy as np

from trailgeek_analysis import sandbox as sb


def grades(surface, xy):
    z = surface.grid(0.5).sample(xy[:, 0], xy[:, 1])
    ds = np.hypot(*np.diff(xy, axis=0).T)
    return np.diff(z) / ds


class SettingsTests(unittest.TestCase):
    def test_every_factor_has_a_weight_and_two_vary(self):
        st = sb.settings_at(sb.weights_at(1, 2))
        self.assertEqual(st["factors"]["grade"]["k"], sb.VARY[0][1][1])
        self.assertEqual(st["factors"]["corner"]["k"], sb.VARY[1][1][2])
        self.assertEqual(st["factors"]["drainage"]["k"], 0.0)                 # fixed, and said why
        self.assertGreater(st["factors"]["tsa"]["k"], 0)                      # the defaults otherwise
        fj = sb.factors_json(st)
        self.assertEqual([f["key"] for f in fj if f["varies"]], ["grade", "corner"])
        self.assertTrue(all(f["note"] for f in fj if f["key"] in ("drainage", "wet", "existing")))

    def test_weights_between_nodes(self):
        self.assertAlmostEqual(sb.weights_at(3, 0, 0.5, 0)["grade"], math.sqrt(5 * 10))   # geometric
        self.assertAlmostEqual(sb.weights_at(0, 0, 0.5, 1)["corner"], 10.0)               # from 0: linear


class FormulaTests(unittest.TestCase):
    def test_plan_climbs_at_the_cheapest_grade_the_ground_allows(self):
        st = sb.settings_at(sb.weights_at(4, 0))
        s = np.full(50, 0.6)
        g, total = sb.climb_plan(s, np.full(50, 1.0), st)
        c = sb.climb_cost(sb.G_GRID[sb.G_GRID <= 0.6], 0.6, st)
        self.assertAlmostEqual(float(g[0]), float(sb.G_GRID[sb.G_GRID <= 0.6][np.argmin(c)]), delta=0.01)
        self.assertTrue(np.all(g == g[0]))

    def test_gentle_ground_is_climbed_straight_up(self):
        st = sb.settings_at(sb.weights_at(0, 0))
        g, _ = sb.climb_plan(np.array([0.02, 0.03]), np.array([1.0, 1.0]), st)
        self.assertTrue(np.allclose(g, [0.02, 0.03]))

    def test_heavier_grade_weight_means_gentler_grade(self):
        gs = [sb.SURFACES["cone"].best_grade(sb.settings_at(sb.weights_at(i, 0))) for i in range(len(sb.VARY[0][1]))]
        self.assertEqual(gs, sorted(gs, reverse=True))

    def test_cone_spiral_climbs_at_its_plan(self):
        S = sb.SURFACES["cone"]
        st = sb.settings_at(sb.weights_at(4, 0))
        xy = S.exact(st)
        g = grades(S, xy)
        r = np.hypot(*xy[1:].T)
        steady = r > 4 * S.A
        self.assertAlmostEqual(float(np.median(g[steady])), S.best_grade(st), delta=0.005)
        self.assertGreater(float(np.ptp(np.unwrap(np.arctan2(xy[:, 1], xy[:, 0])))), math.pi)
        self.assertTrue(np.allclose(xy[-1], [0, 0]))

    def test_plane_zigzag_fits_the_corridor_at_its_grade(self):
        S = sb.SURFACES["plane"]
        Z = S.zigzag(12, 0.15)
        self.assertLessEqual(float(np.abs(Z[:, 0]).max()), S.W - S.MARGIN + 1e-9)
        self.assertTrue(np.allclose(grades(S, Z), 0.15, atol=0.002))
        self.assertIsNone(S.zigzag(1, 0.05))


class TraceTests(unittest.TestCase):
    def test_a_step_stays_in_its_strip(self):
        # The strip leans toward the target: a step may move that way, not must.
        S = sb.SURFACES["dome"]
        grid = S.grid()
        a = sb.formula_line(S, sb.weights_at(2, 0), grid=grid)
        b = sb.formula_line(S, sb.weights_at(5, 0), grid=grid)
        a["_xy"], b["_xy"] = np.asarray(a["_xy"]), np.asarray(b["_xy"])
        step = sb.trace_step(S, sb.weights_at(2, 0, 0.3, 0), a, toward=b, iters=150, grid=grid)
        self.assertIsNotNone(step)
        from trailgeek_analysis.refine.score import polyline_distance
        d, _ = polyline_distance(step["_xy"][:, 0], step["_xy"][:, 1], a["_xy"])
        self.assertLessEqual(float(d.max()), sb.TRACE_W + sb.TRACE_LEAN + 0.5)


if __name__ == "__main__":
    unittest.main()
