"""The evaluator against synthetic planes, where every answer is known."""
import math
import unittest

import numpy as np

from trailgeek_analysis import evaluate, length_m, rate_m_per_day, resample_legs
from trailgeek_analysis.evaluate import _runs, band_table, grades, running_mean

SLOPE = 0.30          # a plane rising 30 % to the north: z = 0.3 * y


def plane(x, y):
    return SLOPE * y, np.zeros_like(x), np.full_like(y, SLOPE)


def run(legs_xy, kinds=None, settings=None):
    r = resample_legs(legs_xy, 1.0)
    z, gx, gy = plane(r["x"], r["y"])
    legs = [{"kind": k} for k in (kinds or ["new"] * len(legs_xy))]
    return evaluate(r["d"], r["leg"], r["heading"], z, gx, gy, legs=legs, settings=settings)


class UnitTests(unittest.TestCase):
    def test_units(self):
        self.assertAlmostEqual(length_m("5 ft"), 1.524)
        self.assertAlmostEqual(rate_m_per_day("1 mi/day"), 1609.344)
        self.assertEqual(rate_m_per_day("30 m/day"), 30.0)
        with self.assertRaises(ValueError):
            length_m("5 furlongs")


class ResampleTests(unittest.TestCase):
    def test_leg_boundaries_are_samples_and_lengths_add_up(self):
        r = resample_legs([[(0, 0), (10.5, 0)], [(10.5, 0), (10.5, 7)]], 2.0)
        self.assertAlmostEqual(r["d"][-1], 17.5)
        # The boundary vertex is written once and closes leg 0.
        self.assertEqual(int((r["leg"] == 0).sum()), 7)      # 0 + 6 steps of 1.75 m
        i = int(np.flatnonzero(r["leg"] == 0)[-1])
        self.assertAlmostEqual(r["d"][i], 10.5)
        self.assertTrue(np.all(np.diff(r["d"]) <= 2.0 + 1e-9))


class GeometryTests(unittest.TestCase):
    def test_straight_up_the_fall_line(self):
        s = run([[(0, 0), (0, 100)]])["summary"]
        self.assertAlmostEqual(s["grade"]["p50"], 30.0, places=1)
        self.assertAlmostEqual(s["slope"]["p50"], 30.0, places=1)
        self.assertAlmostEqual(s["tsa"]["p50"], 0.0, places=1)
        self.assertAlmostEqual(s["climb_m"], 30.0, places=0)
        self.assertTrue(s["runs_uphill"])

    def test_along_the_contour(self):
        s = run([[(0, 50), (100, 50)]])["summary"]
        self.assertAlmostEqual(s["grade"]["p50"], 0.0, places=1)
        self.assertAlmostEqual(s["tsa"]["p50"], 90.0, places=1)

    def test_diagonal_matches_old_formula(self):
        # 60 degrees off the fall line: grade = slope * cos(60) = 15 %, and
        # TSA = acos(|grade| / slope) = 60 degrees.
        a = math.radians(60)
        s = run([[(0, 0), (100 * math.sin(a), 100 * math.cos(a))]])["summary"]
        self.assertAlmostEqual(s["grade"]["p50"], 15.0, places=1)
        self.assertAlmostEqual(s["tsa"]["p50"], 60.0, places=1)

    def test_descending_grades_are_binned_by_magnitude(self):
        s = run([[(0, 100), (0, 0)]])["summary"]
        self.assertFalse(s["runs_uphill"])
        self.assertEqual(s["grade_over"]["18"]["pct"], 100.0)

    def test_flat_ground_has_no_tsa(self):
        r = resample_legs([[(0, 0), (50, 0)]], 1.0)
        z = np.zeros_like(r["x"])
        out = evaluate(r["d"], r["leg"], r["heading"], z, z, z)
        self.assertIsNone(out["summary"]["tsa"])


class NullAndRunTests(unittest.TestCase):
    def test_running_mean_skips_nulls(self):
        v = np.array([10.0, np.nan, 20.0, np.nan, np.nan])
        out = running_mean(v, 3)
        self.assertEqual(out[0], 10.0)          # the old code gave 5
        self.assertEqual(out[1], 15.0)
        self.assertEqual(out[2], 20.0)
        self.assertTrue(np.isnan(out[4]))
        ramp = np.arange(10.0)
        self.assertTrue(np.allclose(running_mean(ramp, 9), ramp))   # linear trends survive, ends included

    def test_gap_is_bridged(self):
        g = grades(np.array([0.0, 1, 2, 3]), np.array([0.0, np.nan, np.nan, 3.0]))
        self.assertAlmostEqual(g[3], 100.0)

    def test_single_point_runs_count(self):
        w = np.ones(5)
        self.assertEqual(_runs(np.array([False, True, False, False, False]), w), 1.0)
        self.assertEqual(_runs(np.array([True, True, False, True, True]), w), 2.0)
        rows = band_table(np.array([1.0, 20.0, 1.0, 20.0, 20.0]), w, [5, 18])
        self.assertEqual(rows[2]["longest_m"], 2.0)
        self.assertEqual(rows[0]["pct"], 40.0)

    def test_no_data_is_reported_as_coverage(self):
        r = resample_legs([[(0, 0), (0, 100)]], 1.0)
        z, gx, gy = plane(r["x"], r["y"])
        z[r["d"] > 50] = np.nan
        gx[r["d"] > 50] = np.nan
        gy[r["d"] > 50] = np.nan
        s = evaluate(r["d"], r["leg"], r["heading"], z, gx, gy)["summary"]
        self.assertAlmostEqual(s["coverage_pct"], 50.0, delta=1.5)


RUBRIC = {
    "slope_effect": [{"min": "0 %", "max": "50 %", "construct": "100 m/day", "maintain": "1 mi/day"}],
    "grade_effect": [{"min": "0 %", "max": "100 %", "construct": "1 mi/day", "maintain": "3 mi/day"}],
}


class LegAndEffortTests(unittest.TestCase):
    def test_existing_legs_cost_maintenance_only(self):
        out = run([[(0, 0), (100, 0)], [(100, 0), (200, 0)]], kinds=["existing", "new"],
                  settings={"effort_rubric": RUBRIC})
        ex, new = out["legs"]
        self.assertAlmostEqual(ex["length_m"], 100.0)
        self.assertAlmostEqual(new["length_m"], 100.0)
        self.assertEqual(ex["effort"]["construct_days"], 0.0)
        # 100 m at 100 m/day for side-slope + 100 m at 1 mi/day for grade.
        self.assertAlmostEqual(new["effort"]["construct_days"], 1.0 + 100 / 1609.344, places=2)
        self.assertGreater(ex["effort"]["maintain_days"], 0)
        s = out["summary"]
        self.assertEqual((s["build_m"], s["existing_m"]), (100.0, 100.0))
        self.assertAlmostEqual(s["construct_days"], new["effort"]["construct_days"])
        self.assertEqual((new["d_start"], new["d_end"]), (100.0, 200.0))

    def test_effort_factor_scales_construction(self):
        r = resample_legs([[(0, 0), (100, 0)]], 1.0)
        z, gx, gy = plane(r["x"], r["y"])
        out = evaluate(r["d"], r["leg"], r["heading"], z, gx, gy,
                       legs=[{"kind": "restore", "effort_factor": 0.5}], settings={"effort_rubric": RUBRIC})
        self.assertAlmostEqual(out["legs"][0]["effort"]["construct_days"], (1.0 + 100 / 1609.344) / 2, places=2)

    def test_profile_is_downsampled_but_keeps_the_end(self):
        r = resample_legs([[(0, 0), (0, 5000)]], 1.0)
        z, gx, gy = plane(r["x"], r["y"])
        p = evaluate(r["d"], r["leg"], r["heading"], z, gx, gy, profile_points=500)["profile"]
        self.assertLessEqual(len(p["d"]), 502)
        self.assertEqual(p["d"][-1], 5000.0)


if __name__ == "__main__":
    unittest.main()
