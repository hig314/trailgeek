# Refine route: design (draft, 2026-10-17; owner's answers folded in the same day)

**Status (2026-10-17): a first draft is built** (build steps 1–3 of §5) for
the owner to evaluate in dev: `trailgeek_analysis/refine/` (core, tested on
synthetic surfaces), `core/refine.py` + `core/refine_views.py` (worker job,
endpoints, per-user defaults in `UserSetting`, `Alignment.refinement`),
`core/static/core/js/tg_refine.js` (panel, curve editor, compare, save as
variant). Step 4, the design grade line in the evaluator for every
alignment, is not done: the refine panel compares like with like, but the
evaluator's numbers for a saved variant still use the raw ground. §7 lists
what changed from the design while building it.

A tool that takes a selected stretch of an alignment and proposes a modestly
tweaked version that fits the lidar surface better. Status: **design only**,
open questions at the end. Builds on the Alignment / Leg / variant model,
the evaluator (`trailgeek_analysis/`), terrain sampling (`core/dem.py`) and
the Huey worker; nothing here replaces them.

## 1. Workflow

1. Select an alignment, then a leg or a contiguous run of legs. **Existing-
   trail legs are locked by default** (owner, 2026-10-17); the ends of the
   refined stretch stay fixed. Finer control later: a reroute may want to
   use existing trail as much as possible (a "shared with existing trail"
   factor) and, once it leaves, be free to diverge a long way.
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
| Bend radius | radius of any other bend, m | per metre of bend | 0–5: 1, 10: 0.3, 20+: 0 | 1 |
| Terrain slope | side slope %, ~3 m scale | per metre | 0–60: 0, 80: 0.3, 100: 1 | 3 |
| Cut / fill | \|design − ground\|, m | per metre | 0–0.1: 0, 0.3: 0.3, 0.5: 1 | 5 |
| Distance from input | lateral offset, m | per metre | 0–5: 0, 10: 0.2, 20: 1 | 2 |
| Length | metres, every one alike (no curve; §10) | per 100 m, from the input's length | — | 3 |
| Drainage (stub) | metres since the last grade reversal | per metre | 0–60: 0, 120: 0.5, 200: 1 | 1 |
| Corner landing | side slope at a corner's apex, % | per corner | 0–15: 0, 30: 0.5, 45: 1 | 2 |

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
drift. Built as a **merge chain** of partial settings with one schema,
`site → user` now, so project-level defaults (owner, 2026-10-17: wanted,
not in the first pass) slot in later as `site → project → user`, and named
presets (hiking, adaptive bike) as one more link, without changing the
format.

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
- **Bend** (owner, 2026-10-17): any change of direction that is not a
  corner, e.g. a gully crossed while climbing. Its own curve, lighter by
  default: smoothing bends is "easier for a runner", while corners carry
  corner-cutting and more. Builders normally smooth bends with cut and
  fill, which the tube already allows: a straighter line across a gully
  costs fill, and the cut/fill curve prices it.
- **No pre-smoothing of the lidar** (owner, 2026-10-17). Elevation noise
  along the line is what the cut/fill tube is for, so the profile uses the
  raw surface. The exception is what the tube cannot see: quantities across
  the line. **Side slope** is measured at bench scale, as the plane fitted
  to the ground across the bench footprint (tread width plus the cut and
  fill slopes, ~2–3 m, perpendicular to the line), which is the slope the
  bench is actually cut into rather than a smoothing choice. **Outslope
  direction** for corner detection uses that same cross-section, plus
  hysteresis, so a boulder or a root wad cannot flip it and invent a corner.
- **Grade reversals: a deliberately primitive stub** (owner, 2026-10-17;
  good siting needs groundwater and the terrain below the trail, later).
  After the taut string, candidate reversal sites are (a) where the ground
  already dips below the design line, i.e. an easy opportunity, and (b)
  gully crossings, where water already converges. A reversal is a fixed
  template dip (length and depth as settings) placed at a candidate site;
  the drainage factor charges for long runs since the last one. Marked in
  code and UI as a stub. Next steps when it is revisited: upslope
  contributing area from the corridor DEM (flow accumulation) as the
  groundwater proxy, terrain grade below the trail for where water goes
  once shed, and reversal spacing that tightens with grade.

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

## 6. Decisions (owner, 2026-10-17)

- Proposed improvements accepted: input scored with the same grade line
  (an evaluator option for all alignments), corner landing slope, up to
  three candidates, only real improvements offered.
- Grade reversals: a primitive stub, flagged as such (§2).
- Bends: a separate, lighter factor from corners (§2).
- Existing-trail legs locked by default; reuse-existing-trail factor later.
- Length carries a cost.
- Defaults per user now, architecture ready for project defaults.
- No DEM pre-smoothing; the tube handles elevation, bench-scale cross
  sections handle side slope and outslope (§2).

## 7. What changed while building the first draft (2026-10-17)

Found by running it on the Tutka Tusk alignments (Kachemak Bay 2023 lidar,
0.5 m): a 757 m stretch takes ~15 s, the 1.9 km Lagoon Bypass ~45 s, at the
default 1500 iterations × 4 chains.

- **Grade has a peak term and a ceiling.** Per-metre cost alone let a
  candidate shorten long moderate stretches while making the worst pitch
  steeper. Now the curve's value at the steepest 10 m adds a quarter to the
  grade cost; going past the *input's* steepest 10 m costs a further 1 per
  10 %, without limit; and a candidate whose steepest 10 m is more than
  1 % worse than the input's is never offered while grade is weighted.
- **The default grade curve keeps rising to 55 %** (was full cost at 25 %).
  A curve that tops out gives no pressure beyond its last node, and the
  Tutka Tusk pieces run at 35 % for their steepest twentieth. General
  lesson for every curve: put the last node where you stop caring.
- **Drainage stub:** a design-line low point counts only when the line is
  at least the dip depth higher on both sides within 10 m (a millimetre
  wobble drained nothing, and the search learned to make them); the default
  ground dip is 0.3 m, not 0.15 m, which triggered almost everywhere on raw
  0.5 m lidar. It still finds ~1 site per 30 m on rough ground, so the
  drainage factor rarely bites yet: expected of a stub.
- **Chains** are weighted base, closer (offset and length ×2; since §14 the limit halved), steadier
  (grade ×2), base; candidates are the best under the base weights plus the
  best of the closer and steadier chains, deduplicated.
- **Corners on real lidar:** the inputs show more outslope flips than an eye
  would call corners (18 on Lagoon Bypass), and candidates often have fewer,
  by crossing a spur or gully floor straight where the flip then costs
  nothing. Worth the owner's eye: if the flips are noise, raise the
  minimum side slope (a run parameter) or widen the bench.

### Second pass, after the owner's first try (2026-10-17)

The owner saw tight, complex curvature inside some corners: one broad,
smooth corner read as several, because where a corner runs down the fall
line the cross slope is near zero and lidar roughness flips its sign; the
search then "fixed" each phantom corner and made kinks. Changes:

- **A corner is a turn with a net change of downhill side.** The side comes
  from the cross slope averaged over ~10 m (then hysteresis); flips within
  20 m are one event and events whose turning zones touch are one zone; a
  zone counts only if the side after it differs from the side before it and
  the line turns at least 60° across it (a side change on a straight line
  is crossing a crest or a valley floor). On the Tutka lines: Lagoon Bypass
  18 phantom corners → 0, Tutka Tusk 4 → 1.
- **A corner's radius is the tightest in its zone**, so a kink inside a
  broad corner costs as the kink it is.
- **Fewer corners win:** the default corner curve never drops below 0.2,
  so every corner costs at least that (owner: prefer fewer corners, even
  fewer than the hand-drawn line). Upper ascent A: 6 → 4.
- **Bends are charged per right angle turned** at a radius, not per metre:
  a kink is an event, and per metre a 3 m jog on a 750 m line cost nothing.
  Candidates' tightest bend went from ~2 m to 4–7 m.
- **A smoothing move** low-passes the offset field over a stretch, which
  irons out kinks and lets two turns merge or a spurious corner go, so the
  number of corners can change. Removing a real switchback pair is still out
  of reach: that needs a bigger change than a 20 m corridor allows.
- **A hand-drawn hairpin vertex is refined, not refused**: only folds a
  candidate adds are rejected (Link option A had one).
- The comparison shows the **tightest bend anywhere**, so kinks show in the
  numbers as well as on the map.
- **The "no factor notably worse" guard** now allows a factor to worsen by
  10 % of the input's total or of that factor's k, whichever is larger (was
  5 % of the total, which on a gentle line rejected a fair trade of a little
  more bending for much less grade).

## 8. The sketch as evidence (2026-10-17, owner's direction)

The first draft built a candidate as *the input plus a smooth sideways
offset*, so every sharp vertex of a hand-drawn line came through into the
result. The owner's point: a person lays out a coarse conceptual path, with
sharp corners they understand as sketches of much rounder ones, so the
refinement itself must do the rounding, and the input should be read as
*± some error around a smooth idea*. Now:

- **A candidate is its own smooth curve**: a clamped cubic B-spline through
  control points in the plane, ends pinned to the line's ends, controls
  every `knot_m` (8 m) on straight ground and up to three times closer
  where the line turns sharply (weighted arc length). No kink finer than the
  controls can exist.
- **The start is a smooth reading of the line**: its vertices filleted (at
  `sketch_radius_m` for a loose sketch, less for a precise line; limited by
  the neighbouring segments, so a hairpin becomes a U-turn where its arms are
  far enough apart instead of a cusp), then a least-squares fit with a
  curvature penalty that grows with `input_sigma_m`. Where the fit would
  still fold, the penalty is relaxed there and it refits. The stored line is
  never changed.
- **The line enters only through distance**: the nearest distance to it,
  less a corner allowance at each sketched vertex (rounding a turn D to the
  sketch radius R stands R (sec(D/2) − 1) off the vertex, tapering over the
  tangent length), less the line's uncertainty. Precomputed once on the
  terrain grid's cells, so it costs a lookup per step.
- **Moves act along the candidate's own sideways direction**, and every 250
  iterations the controls are re-spaced along the curve when a refit keeps
  the shape within 0.25 m.
- **"Your line, smoothed"** (the start) is scored and offered beside the
  search's candidates, and the input column is the line *as drawn*, so the
  gain from rounding alone and the gain from fitting the terrain show apart.
- **One input setting covers sketch and survey**: `input_sigma_m`, about 3
  to 5 m for a sketch, about 0.5 m for a GPS track or existing trail.

Two default-balance lessons came out of running it on the Tutka lines:

- **One scale for all factors.** Per-metre factors are now charged per 100 m
  of line (were averaged over the line), and length per 100 m of the input.
  Averaged, grade could never cost more than its k however long the route,
  while corners summed, so on a 2.5 km climb six corners outweighed any
  grade and the search removed switchbacks.
- **The grade curve must be convex.** Climbing a height costs
  (curve ÷ grade) per metre of climb; where the curve flattens,
  steeper-and-shorter becomes cheaper and the search heads up the fall line.
  The default now rises convexly to full cost at 60 %; keep y ÷ x rising up
  to the steepest grade you would accept when editing it.

On the four Tutka lines with the defaults: candidates' tightest bends rise
from ~2–3 m (sketch vertices) to 6–11 m, the steepest 10 m drops or holds,
the 95th-percentile grade holds within ~1 point (Upper ascent A: "Steadier
grade" 36.8 % against 37.4 % drawn, keeping 3 of 6 corners; "Best overall"
40.8 % with 2), and mean distance from the line is ~3 m. Runs take 25 s for
757 m to 100 s for 2.5 km at the default 1500 iterations × 4 chains.

## 9. Corners: fall-line tread and climbing turns versus switchbacks (2026-10-17)

The owner's reasoning, which replaced the "corner landing" factor:

- **Landing was mostly emergent, and partly wrong.** A broad corner on steep
  ground swings through the fall line, so its apex grade approaches the side
  slope and the (convex) grade factor already charges it; landing charged it
  twice. On gentle ground it pointed the wrong way: it preferred flat spots,
  where a fall-line arc pools. Removed.
- **Fall-line tread** (new, per 100 m): where the trail runs within
  `fall_tsa_deg` (30°) of the fall line, water cannot leave the tread
  sideways. The cost is a U-shaped curve of the design grade there: free at
  5–8 % (water runs off along the tread and it holds up), full below 3 %
  (pools), rising gently above 8 % (erodes; gently, since grade charges
  steep pitches too). Per metre, so a broad corner's long fall-line arc on
  gentle ground pays for its length: the counterweight that lets a sharper
  corner win when it keeps the trail across the slope. Ground flatter than
  4 % has no fall line to speak of and is not counted (pooling there is a
  grade question).
- **Climbing turn or switchback-like.** Every corner (the downhill side
  flips) crosses the fall line somewhere; what decides its kind is the
  ground there. At 15 % or gentler (a bench, a nose top, a mild slope) the
  trail can climb straight through the crossing: a climbing turn. At 25 % or
  steeper, crossing the fall line is a steep pitch unless a landing is cut:
  switchback-like. Between is "between". Reported per corner (profile labels,
  the comparison table, the baseline line); it does not change costs yet.
  (A first attempt classified by how far the fall line turns with the trail;
  but a turn that wraps a nose keeps the downhill side and is a bend, not a
  corner, so that measure could not separate the kinds.)
- **Next, only if the classification shows switchback-like corners the owner
  wants:** an explicit switchback corner type with its own fixed cost (work,
  appearance), exempt from the radius curve, allowed a deeper landing cut,
  and a search move that converts a corner to a switchback and back.

**What the classification found on the Tutka lines** (default settings,
2026-10-17): every drawn corner crosses the fall line on steep ground: Tutka
Tusk ~58 %, Link option A ~76 %, Upper ascent A's six at 31–70 %. Checked
against a 5 m bench (35–68 %), so it is the terrain, not lidar roughness.
The candidates move their corners but do not find gentler crossings within
the corridor (Upper ascent A keeps 2–3 corners at 27–69 %). That is the
evidence for or against the switchback corner type above.

**Speed:** the self-intersection check now tests only the stretch a move
changed against the rest, and a candidate is evaluated from the 4 controls
each sample depends on: Tutka Tusk 100 s → 21 s, Upper ascent A 268 s → 65 s,
same results for the same seed.

## 10. Length per metre (owner, 2026-10-17)

Length was a curve of length / input length (free to +2 %, then steep). It
is now a plain multiple: k per 100 m, every metre alike, counted from the
input's length so the line as drawn pays 0 and a shorter candidate earns a
credit. The panel shows only its k. Default k 3: 100 m more costs as much
as 30 m at full grade cost. At k 5 the test sketch's search cut its corner
by ~40 m for the credit and every chain was refused by the offset guard,
leaving only "Your line, smoothed".

## 11. Wet ground: a light TWI (owner, 2026-10-17)

The foundational water work (flow accumulation with culverts and closed
basins edited by hand, lidar intensity for surface water and snow, drought
spectral stability, calibration against field observations) belongs in
topobathy, the shared base map other projects build on; trailgeek will read
its products with light interpretation. Meanwhile the refine tool has a
quick topographic wetness index as one more factor, for mud: soil wetness
is the chronic problem (mud), surface flow the acute one (erosion), and
TWI speaks to the first.

- `trailgeek_analysis/refine/twi.py`: TWI = ln(a / tan b), with a from
  multiple-flow-direction routing (Freeman, p 1.1) over a pit-filled DEM
  (priority-flood + epsilon) and tan b floored at 0.01. Closed basins are
  filled, so they read as wet flats; nothing knows about culverts.
- `core/refine.py build_twi`: the DEM stack on a 5 m grid, each cell the
  mean of 3 x 3 samples (TWI on 1 m lidar maps ruts and logs), reaching
  150 m past the corridor. Upslope area beyond that is not counted, so
  cells near the window's edge read drier than they are. Cached in the
  worker, since the panel's baseline and the run read the same window.
- Factor **Wet ground**, per 100 m, curve on TWI: free to 8, 0.3 at 10,
  0.7 at 12, 1 at 14; k 3. The table's "Wet ground" row is the metres at
  TWI 10 or more.

**On the Tutka lines** (5 m, 2026-10-17): open hillside reads ~5 (line
medians 3.7-5.6), each window's wettest 1 % from 10.3 to 13.5; metres at
TWI >= 10: Lagoon Bypass 54, Link option A 12, Upper ascent A 4, Backdoor
loop connection 264, the rest 0. Reading the window took 6-35 s per line
(117 s for the 10 km loop).

**Map overlay** (2026-10-17): the Layers panel's **Wetness (TWI)** (signed
in) computes the same TWI for the current view in the worker
(`core/water.py`, `tg_twi.js`): 5 m cells of 2 x 2 samples, 150 m buffer
for upslope area, cropped to the view, coloured clear (TWI < 6) through
pale to deep blue (14) and drawn as an image over the shading, under the
trails, with its own opacity. At most 4 km² a view; 4.4 km² around Lagoon
Bypass took 91 s, nearly all of it reading the DEM. Filled flats (lakes,
the sea) read solid blue, striped by the fill's epsilon gradient.
Recomputed on request only; not in the URL.

## 12. Shares and log curves (owner, 2026-10-17)

- **Weights were shares** (replaced by metres on 2026-10-18, §13) adding up to 100 %, one slider per factor. Moving
  one scales the others to fit, keeping their ratios, and the ratios
  survive a trip through the extremes (one factor to 100 %, then 0 %,
  then 50 %): a mass and a level per factor, where a share set to 100 %
  lifted its factor a level above the rest instead of zeroing them (the
  module, `tg_weights.js`, went with the shares). Only the ratios reach the search: the
  defaults are the earlier k values (grade 10, corners 5, ...) as shares of
  100, and older saved settings score the same.
- **Curves live in log space**: drawn on a log y axis, 0.1 to 10 at first
  (+ and − zoom, "fit" frames the curve), and joined **log-linearly**
  between anchors, straight on that axis, in the search as on screen
  (`score.curve_fn`; halfway from 0.1 to 10 is 1). There is **no 0**:
  costs run 10^-4 to 10^4, "next to nothing" is 0.01, and a 0 in an older
  saved curve reads as 0.01. Since a log-linear rise off the floor hugs it
  most of the way, the default curves gained a midway anchor at each such
  rise, at the value the earlier linear curve had there (fall-line tread at
  4.5 % costs 0.25, as before, not 0.03). The floor is not quite free: the
  defaults' floors add about 0.65 per 100 m in all, some 8 % of what
  length itself costs per 100 m.

**Where this is heading** (owner's framing, 2026-10-17, for later): the
weights are an intuitive balance of what is really an economic question.
The true-to-reality model would put build and maintenance costs (with
discounting) against the benefit, at its core the value a user gets from
the trail, and the risks (wildfire damage to the statistical value of a
life lost to a fall). Calibrated against real settings, the ad hoc
weighting could converge on such a model, founded on explicit monetary
signals and on revealed value. The evaluator's construction and
maintenance rubric is the natural meeting point.

## 13. Equivalent effort in metres; signed cut and fill; corners by signed TSA (owner, 2026-10-18)

**Equivalent effort.** The fundamental cost of a trail is its length, and
every hard design trade-off adds to it. So a line's cost is now the length
of easy trail it is as much work as, in metres:

    effort = length along the slope
           + Σ per-metre factors  k × ∫ curve ds      (k: extra m per m at curve 1)
           + Σ corners            k × curve(radius)   (k: m per corner at curve 1)
           + bends                k × Σ curve(r) × right angles turned

- **Length along the slope** is the design grade line's own length, the
  climb included, and per-metre factors are integrated along it. Length has
  no weight: it is the unit. It is charged in full, not from the input's
  length, so totals compare across runs and between reaches of different
  lengths.
- **Weights are in metres** (log sliders with a box to type in; 0 is off).
  The defaults are the 2026-10-17 shares over length's 8.1: grade 3.3 m
  per m, corners 167 m each (the 0.2 floor is 33 m), bends 33 m per right
  angle, and so on. With the 0.01 floors, easy ground runs about 1.07 to
  1.09 × its length; the owner's target is nearer 1.05.
- The table leads with **Equivalent effort**, then length along the slope
  and each factor's extra metres, and **effort per metre** (1.00× is easy
  trail all the way). On the Tutka lines (2026-10-18, defaults): Lagoon
  Bypass 3175 m for 1942 m (1.63×), best candidate 2625 m (1.42×); Upper
  ascent A 5605 m for 2573 m (2.18×), of which corners are 1033 m, best
  4607 m (1.93×).
- **Older settings** (shares, or the earlier grade 10 / corners 5 / ...,
  always saved with a length weight) are converted on reading,
  `settings.upgrade`: per-metre k / length k, per corner or turn k / length k
  × 100; the old |cut or fill| curve is mirrored onto the cut side; the old
  fall-line default of 30 degrees gives way to 22.
- The search guard (a candidate may be worse on one factor by at most 10 %)
  is now 10 % of the input's effort beyond its length, or of the factor's
  unit (100 m at curve 1, one corner, one right angle), whichever is larger.

**Cut and fill is signed**: x is design − ground, cut below 0, fill above.
Cut reaches compacted ground, a sound tread, and its effort grows about as
depth squared (the walls lay back): the default is 2 d² (0.5 m is 0.5, 1 m
is 2). Fill is loose until compacted, and saturated it can turn to soup or
flow away, so past ~20 cm it climbs steeply (0.3 m is 1, 0.4 m is 5).
Full-bench construction would be fill at a very high cost. The tube the
design line runs through is asymmetric to match: cut down to the search's
depth t, fill up to the depth that costs the same on the curve (0.5 m of
cut matches about 0.26 m of fill), never past the curve's last node.

**Trail-slope alignment (TSA) is a factor** with its own curve over 0 to
90 degrees, flat ground read as 90. The **fall-line band** (TSA below
`fall_tsa_deg`, now 22, Marion's convention) is drawn on that curve as a
shaded band whose edge drags, so the cutoff is not a hidden parameter. The
band also decides where the tread counts as fall-line (the fall-line tread
factor) and where the downhill side is undefined (corners). TSA and
fall-line tread overlap (both charge the band), so TSA's default weight is
light, 0.5 m per m.

**Corners by signed TSA** (`Problem._corners`). The downhill side is right
(+) or left (−) of the trail, from the cross slope over ~10 m. It is
undefined in the fall-line band and where the cross slope is under
`min_outslope_pct`, and a side held for less than 10 m is noise. A corner is
a change of side across an undefined stretch (or none), where the line
turns at least `corner_min_turn_deg` (60, now a parameter: less is
crossing a crest or a gully). Its turn reaches out from the change for as
long as the line keeps curving the same way, at radii up to 500 m and at
most 100 m either side. Corners up to 45 m used to count and wider ones
did not (the turn had to be tighter than 50 m within 25 m of the change),
which is why the search widened corners into broad arcs: a 50 m corner
saved the whole 33 m floor.

**Digging a corner in** (option 2 of the 2026-10-18 discussion, computed on
the real ground rather than a uniform plane; `Problem._dig`). Turning
180 degrees at radius r on a slope S, the ground between the two arms
differs by 2rS but the trail climbs only πrG at grade G, so above about
S = πG/2 (16 % slope at 10 % grade) the turn either steepens through the
apex or is cut into the slope. Each corner tries cuts of 0.25 to 4 m (up
to `corner_cut_max_m`, 2 by default) over its turn and 20 m of ramp either
side, the design line pinned to its profile at the ends and fill matched
as above, and keeps the cheapest in grade (with the steepest 10 m) plus
cut and fill. So a wider corner on steep ground costs more digging (the
cut grows with r), and a turn hidden in gentle ground costs nothing extra.
With the default curves a 35 % hairpin of radius 8 m is about even
between 30 % grade through the apex and a 0.25 m cut; the grade curve's
leniency above 20 % decides that, and it is the owner's to tune.

**Still hidden** (candidates to expose): the second grade charge past the
input's steepest 10 m (`GRADE_OVER_PER`), the 10 m and 5 m smoothing
windows, the 4 % flat-ground cutoff for TSA, the corner reach (100 m, 1/500
per m) and ramp (20 m), and the climbing-turn / switchback labels (15 % and
25 %, labels only). `grade_peak_m` (25 m: the steepest 10 m charged again
as this many metres of its grade) is now a parameter.

**Later** (owner): a cost surface over two dimensions (TSA and terrain
slope, say) rather than one curve each; not yet.

## 14. Distance from your line is a limit, not a cost (owner, 2026-10-18)

A trail further from the sketch is not worse on the ground, so the
"distance from your line" factor is gone. The line's position enters only
through **Search within X m of your line** (`corridor_m`, 2 to 200 m,
default 25), at the top of the panel: a hard limit measured straight from
the line, corners included. There is **no allowance at sketched corners**
and the line's uncertainty no longer widens it: a narrow search around a
tight sketched corner gives a tight corner, as asked. The smooth start
rounds sketched corners and smooths less, in steps, until it fits inside
the limit (`START_SHRINK`).

- The "Closer to your line" candidate is now **Within half the distance**:
  the same search with the limit halved (skipped if the start does not fit).
- The table keeps the distance as statistics (max and mean) and adds
  **Along the search limit**, the length within 1 m of it: where a candidate
  presses against the limit, a wider search may do better.
- Older settings with a distance weight have it ignored.
- A wider search reads more terrain (and TWI) and has more room to cover.
  Lagoon Bypass at 25 m and at 60 m both took ~80 s and found candidates at
  most ~17 m from the line, none against the limit. The moves' size
  (bumps of 5 m falling to 0.3 m, `SIGMA0` / `SIGMA_END`) does not grow with
  the limit, so a wide search explores its far side slowly: scaling them
  with the limit is the obvious next step if wide searches matter.
