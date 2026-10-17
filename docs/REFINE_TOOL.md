# Refine route: design (draft, 2026-10-17)

A tool that takes a selected stretch of an alignment and proposes a modestly
tweaked version that fits the lidar surface better. Status: **design only**,
open questions at the end. Builds on the Alignment / Leg / variant model,
the evaluator (`trailgeek_analysis/`), terrain sampling (`core/dem.py`) and
the Huey worker; nothing here replaces them.

## 1. Workflow

1. Select an alignment, then a leg or a contiguous run of legs (default:
   every build leg; existing-trail legs locked).
2. **Refine…** opens a panel: one row per factor, each with a cost curve, a
   weight `k` box (0 = ignore), and behind the curve a histogram of the
   *current* line's values for that factor, so the user sees where the line
   sits on the x-axis before deciding what to penalise.
3. **Run** queues a worker job; progress (best cost so far, iteration)
   polls into the panel. The corridor and the current best line draw live.
4. The result is a **variant** (`Alignment.parent` = the original), named
   "<name> refined <date>", with the settings and RNG seed stored on it.
   The existing compare view (Saved / Now / Change, profile with the saved
   line in grey) does the before/after; a per-factor cost breakdown is added.
5. Settings buttons: **Set as my default**, **Reset to my default**,
   **Reset to site default**.

## 2. Factors and their cost curves

Every factor is `k * f(x)`, with `f` a piecewise-linear curve of at most 8
segments, y in 0..1. Per-length factors are integrated along the line and
divided by length (cost per metre); per-event factors (corners) are summed.
The panel shows each factor's share of the total, before and after, which is
how a user learns what a `k` means.

| Factor | x | Kind | Default curve (x: y) | Default k |
|---|---|---|---|---|
| Grade | design \|grade\| %, 10 m mean | per metre | 0–8: 0, 12: 0.1, 18: 0.5, 25: 1 | 10 |
| Corner radius | radius at an outslope flip, m | per corner | 0–3: 1, 6: 0.4, 10: 0.1, 15+: 0 | 5 |
| Terrain slope | side slope %, ~3 m scale | per metre | 0–60: 0, 80: 0.3, 100: 1 | 3 |
| Cut / fill | \|design − ground\|, m | per metre | 0–0.1: 0, 0.3: 0.3, 0.5: 1 | 5 |
| Distance from input | lateral offset, m | per metre | 0–5: 0, 10: 0.2, 20: 1 | 2 |

Hard limits sit outside the curves, so the search never wastes time there:
the corridor (offset ≤ 25 m), the cut/fill tube (≤ 0.75 m), fixed ends.

**Curve editor** (D3): the curve is drawn as plateaus joined by ramps,
which is what these curves almost always are. Drag a plateau up or down;
drag a ramp's ends sideways; double-click a plateau to split it; drag a node
off the chart to delete it. Node count is capped at 9 (8 segments). Values
snap to round numbers; a node's exact x, y can be typed in.

**Defaults** live per user: a `UserSetting(user, key, value JSON)` row with
key `refine`, falling back to the site default in code. Every run stores the
full settings it used, so a variant can be reproduced after the defaults
drift.

### Definitions

- **Design grade line.** The tread is not the lidar surface: it is a grade
  line allowed to sit a little above or below it (small cut and fill, which
  also absorbs lidar error). For a given plan line, the design profile is
  the **taut string** through the tube `ground ± tolerance` in (distance,
  elevation): the shortest path through the tube, which is exactly the
  profile with the least maximum grade and the least grade variation that
  stays inside it. O(n), no tuning. The tolerance is not one number: the
  cut/fill curve is applied as a soft cost (free to 0.1 m, rising to the
  0.5 m that is rare), solved as a weighted smoothing with the hard tube at
  the outside.
- **Corner.** Where the outslope flips: the downhill side changes from left
  to right of the direction of travel (sign of heading × gradient), with
  hysteresis and a minimum side slope so flat ground does not flicker. A
  gully crossed while climbing keeps the downhill side and is not a corner.
  **Radius** = arc length through the flip zone / heading change across it,
  which is steadier than a peak curvature from three points.
- **Terrain slope** from a DEM smoothed to tread scale (~1–2 m), so 0.5 m
  lidar micro-relief does not read as cliffs.

## 3. The search

### Representation

The candidate line is the input line plus a **lateral offset field**
`o(s)` along the input's arc length, as a cubic B-spline with knots every
~10 m. A 2 km leg has ~200 parameters, not thousands of vertices; the
distance-from-input factor is just `|o|`; ends are fixed by pinning the end
knots. Where the input turns tightly the offset normals cross on the inside
of the bend; candidates that fold (self-intersect, or reverse direction
locally) are rejected outright. Sampling of the candidate is at 1 m, which
the old `add_curvature` warned tight corners need.

### Terrain once, in memory

The job reads the corridor (input buffered by 30 m) from the DEM stack once
into a numpy array at native resolution (lidar where present, the context
beyond), plus the smoothed copy and its gradient. Every candidate is then
scored in memory. Sampling through `sample_stack` per candidate would be far
too slow.

### Moves, and the lengthwise dependence

Moves are local bumps in `o(s)` at a random station, with a random width.
The difficulty you named: a wider corner lands the line a bit higher or lower,
so everything beyond it is now on the wrong grade. A plain bump would be
rejected because of the damage downstream even when it is the right idea.
So there are three move types:

1. **Bump**: a smooth local offset change (widths 10–60 m).
2. **Bump and slide**: a bump, then the stretch beyond it is shifted along
   the contour by the amount that restores its elevations (offset change =
   Δz / side slope, tapering over a run length). This keeps the rest of the
   climb on grade while the corner changes. The slide is what makes realistic
   improvements reachable in one step instead of several unlikely ones.
3. **Corner swing**: at a detected corner, move the apex along the fall
   line or along the contour and re-fit the two arms, so the radius and the
   landing change together.

### Schedule

Simulated annealing with the move width shrinking as the temperature
falls (coarse alignment first, polish last), several independent chains
from the input with different seeds, and a small elite set. Each chain is
cheap: one candidate score is a resample, a lookup in the corridor arrays,
a taut string, and the curve lookups, all O(n). Whole profile re-scored on
every move rather than incrementally; it is fast enough and avoids a class
of bookkeeping bugs.

### Not getting lost in outliers or min/max

- Max grade enters through the curve per metre, not as a hard `max()`, so
  one bad metre costs one metre's worth and the gradient of improvement
  stays smooth.
- Each factor is capped at y = 1 per metre, so a cliff cell cannot dominate
  everything else.
- Hard limits are rejections, not huge costs.
- The terrain is smoothed to tread scale before slope is taken.
- A candidate must beat the input on the total *and* not be worse than a set
  margin on any factor the user weighted, or it is not offered.

### Fairness of the comparison

The input line is scored with the same design grade line (same tube), so
the refinement does not "win" merely because its profile was smoothed and
the original's was not. Recommendation: make the design grade line an
evaluator option for every alignment, so the compare table means the same
thing everywhere.

## 4. Output

- A variant alignment with the original's leg boundaries carried over by
  arc length, the design profile (elevation per sample, cut/fill) stored
  with its evaluation, and the run's settings and seed.
- The profile shows ground, design grade line, and cut/fill shading.
- The map shows the corridor, the input in grey, the refined line.
- Optionally up to three candidates rather than one: the best total, the
  best with distance from input weighted double, and the best with grade
  weighted double, so a user sees the tradeoff instead of a single answer.

## 5. Build order

1. `trailgeek_analysis/refine/`: pure numpy, no Django: corridor arrays,
   offset field, taut string with soft tube, corner detection, factor
   curves, annealer. Tests on synthetic surfaces (a plane, a cone with a
   known optimal climbing turn, a gully crossing that must not count as a
   corner).
2. Worker task + `/api/alignments/<id>/refine` (start, progress, result) +
   `UserSetting`.
3. Panel and curve editor; compare view gains the cost breakdown.
4. Design grade line as an evaluator option for all alignments.
5. Later: a deterministic dynamic-programming seed over a corridor trellis,
   if annealing proves slow on long legs; the router can reuse the curves.

## 6. Open questions

See the PR or the conversation of 2026-10-17; answers get folded in here.
