# The methods page's route sandbox

`/methods/sandbox/` (owner, 2026-10-19/20): a small patch of terrain, the
weights of the refine engine, and the line it draws at each setting, so a
reader can see how the weights move the answer. Every line is computed
ahead of time by the real engine (`refine.search.refine` and its annealer)
and stored as static JSON; the page only draws. No second copy of the
engine in JavaScript: the page would then explain an engine the site does
not run.

## The interface (owner, 2026-10-20)

- **Every factor has a weight; some vary.** The sandbox is a set of weights
  and curves for every factor (the defaults), with a few factors flagged as
  varied (their curves fixed, their weights on sliders). No factor is left
  out; the reader just cannot move the fixed ones. The page lists them all
  ("All factor weights"). The test case varies two: grade and corners
  (`sandbox.VARY`). Fixed away from the defaults, and said why on the page:
  drainage at 0 (a stub, and on a climb that never reverses it charges every
  metre past 60 m). Off because the surface has nothing for them: wetness,
  existing tread.
- **A weight pad and two sliders.** The grid of settings is drawn as rails;
  the point rides a rail (moving one slider puts the other on its nearest
  grid value). Between two settings the line slides from step to step of a
  tracing (below), and where the route changes it jumps, marked in red on
  the pad.
- **Dig-in ticks.** Short lines across the alignment where the design line
  is below the ground (cut, brown) or above it (fill, teal), 8 m long per
  metre of depth. The profile chart draws the ground under the engine's line
  too, so a dip hidden by a dug-in corner shows.

## How the lines are found (`manage.py build_methods analytic`)

1. **The grid's settings, thoroughly.** `refine` as the panel runs it
   (route-finder and chains), with 6 chains of 2500 iterations, at two
   seeds; the cheapest line kept (`offer_all`, so a winding answer much
   longer than the straight input is not dropped by the panel's MARGIN rule).
2. **Polished from each neighbour.** Each setting is searched again from each
   neighbouring setting's line (8 m either side of it), so a better route
   found next door is not missed.
3. **Traced along every edge, from both ends** (owner's design): ten steps;
   each step searches only a strip 3 m either side of the step before's line,
   or of that line moved up to 4 m toward the other end's line (whichever is
   nearer: `Problem(limit_xy=...)`), 1500 iterations.
   - **One switch per edge.** The page shows the tracing from the lower end
     up to a switch and the one from the upper end after it, the switch where
     the total along the edge is least. (First tried: the cheaper of the two
     at each step. Where two routes cost within the searches' noise of each
     other, about 0.5 %, that flickered between them, up to six jumps on an
     edge.)
   - **Re-tracing.** A tracing that reaches the far setting more cheaply than
     that setting's own line replaces it; the edges at that setting are then
     traced again from its new line (else they start from a line it no
     longer shows: a jump at the setting), until none is replaced, at most 3
     rounds. Tracing is a good optimiser: in the trials a third to a half of
     the settings were first improved this way.
   - Consecutive lines more than 10 m apart anywhere are a jump (in the
     dome's trial, a step moved a line 3 m or less; a change of route,
     30-80 m).
4. **The formula's line** at every setting and step.

Run it with numpy on one thread per worker, or the workers fight over the
cores (an hour for the cone's tracings at 1155 % CPU, 2026-10-20):

    docker compose exec -e OMP_NUM_THREADS=1 -e OPENBLAS_NUM_THREADS=1 web python manage.py build_methods analytic --jobs 8

The engine's runs (stages 1-3) are cached in `.methods_cache/` (not
committed); `--recombine` rebuilds the page's file from them after a change
to how they are combined. `--quick` (short searches, one seed) tries the
pipeline; the page says so. `--formula-only` redoes stage 4 in an existing
file. Rebuild after any
change to the scoring or the search, or the page shows lines the engine no
longer draws.

## The formula

On these smooth surfaces every per-metre factor depends only on the line's
grade g and the ground's slope s (grade; trail-slope alignment, acos(g/s);
fall-line tread in the band; side slope; cut and fill at its floor), so a
metre of height costs c(g, s) = m(g, s) sqrt(1 + g^2) / g. With turns free,
the cheapest climb takes each metre at the g <= s minimising c, under a cap
searched for the steepest-pitch charge (`climb_plan`). Exact only with turns
free: the formula's line pays the bend weight for its curvature and the
corner weight for any corner, and the engine may beat it by turning less.

- With trail-slope alignment and fall-line tread charged, the turn-free
  line keeps about 33 degrees off the fall line nearly to the top, so on the
  cone and the dome it spirals in tightly near the summit, and pays bend
  charges there that the engine avoids. Near summits it is a weak reference.
- The grade curve is the default carried on past 60 % (2.5 at 100 %): the
  default goes flat at 60 %, past which steeper climbing is cheaper per metre
  of height.
- Grades within one step of the grid of grades of the slope are the slope
  (the fall line); without that a grade a hair under the slope spiralled
  into the summit forever (2026-10-20).

## What it showed about the engine

**Review of 2026-10-21** (before the real-terrain sandbox):

- **The plane's lines of 2026-10-20 were invalid.** Tracing and polishing
  steps measured their limit from the step before's line alone, and drifted
  out of the plane's 30 m corridor (469 of 563 lines, to 100 m). Its claims
  ("within 1 % of the reference", "beats the reference zigzag") are
  withdrawn. Fixed: a step must stay inside its strip and the surface's own
  corridor (`sandbox.trace_step`). The cone's and dome's 120 m corridors held.
- **Inside the corridor, single thorough runs match the plane's reference
  zigzag** (within 1 %, or better, at four settings). The 30 % switchback
  shortfall of the first build was short runs, not the scoring.
- **Where the search falls short: layouts.** The annealer reaches a local
  optimum (a coordinate-descent polish after it gains 0.3 %; twice the
  controls, nothing); what it cannot do is change a layout (how many
  switchbacks, how many turns of a spiral). Tracing between settings found
  better layouts: 1-4 % typically, 25-43 % at grade weight 30 with corners
  free (long spirals).
- **Engine fixes from it** (branch `refine-route-starts`):
  - the route-finder's chain was dropped when the smooth fit to its route
    folded back at one tight grid turn (on the cone at grade weight 30, its
    2-turn spiral); now refitted with its corners rounded more (3, 6, 12 m);
  - the route-finder runs at the corner and bend weights times 1, 0.5 and 2,
    a chain from each distinct route (different layouts for the annealer),
    on grids of up to 50,000 cells (on the 7 km Graduation Peak Trail,
    336,000 cells, a search takes ~190 s and the three routes were all but
    the same).
  - Together: single thorough runs at grade weight 30, corners free, went
    from 1985 to 1174 on the cone (tracing found 1134) and 942 to 831 on the
    dome (709).
- **After the fixes (rebuild of 2026-10-21):** no line leaves its corridor.
  Cone and dome: the engine is never more than 1.4 % dearer than the
  formula, and cheaper at 68 of 72 settings (it turns less). Plane: within
  1 % or better at 30 of 36; dearer by 2-30 % at 6, all with corners dear
  (corner weight 100-300).
- **Why, at dear corners: the reference uses a blind spot in the scoring**
  (found 2026-10-21, correcting the first reading, "one long switchback"). Its
  line at those settings is a ladder of about 20 switchbacks, 8 m either side
  of the straight line, and the scoring counts one corner. The scoring reads
  the downhill side from the cross slope averaged over ~10 m of line, and
  drops a side held for less than 10 m (`score.CORNER_RUN_MIN_M`) as noise;
  each leg of the ladder holds its side 8.7-9.1 m, so none counts. With that
  minimum at 6 m the reference costs more than the engine's line at 5 of the
  6 settings (at 4 m, all 6). The engine is not short there; the reference
  is cheating. To change in the scoring (the owner's call): read the side
  from the ground's slope against the line's heading, not an average along
  the line that cancels on short legs, and a shorter minimum.
- **The route-finder did miss the corner weight** (fixed on branch
  `refine-route-corners`): it charged corner rates only for one grid turn of
  60 degrees or more, and built switchbacks from 25-45 degree turns, so the
  corner weight never reached it. It now carries the downhill side in its
  state and charges a change of side, as the scoring counts corners.
- **Cost:** a thorough run on the plane went from ~85 s to ~145 s (the
  three route-finder chains start from winding routes with many controls);
  the full build of all three surfaces took 4.7 hours on the Mac.
- **Rebuild of 2026-10-26, on the scoring of #28** (the downhill side from
  the ground's slope, a 4 m side minimum) and the route-finder of #27: 2.1
  hours for all three on the Mac (8 workers). The engine is within 1 % of
  the reference, or cheaper, at 107 of 108 settings: all 72 on the cone and
  the dome, 35 of 36 on the plane. The plane's six dear-corner shortfalls
  (2-30 %) are gone: the reference's ladder now pays for its corners. The
  one left: grade weight 2, corners free, 2 % dearer (the engine's line
  has 2 corners, the reference zigzag 3). Corners on the engine's lines: cone 2 ->
  6 in all, dome 0 -> 0, plane 120 -> 109.
- **Tried and not worth it:** a coordinate-descent polish after the search;
  more controls; smaller moves in tracing steps (worse: tracing needs the big
  moves to reach the other end's route); splicing candidate lines where they
  cross (2-opt style: worse on a zigzag slope, every splice is a sharp
  corner; might suit real terrain, where lines braid at shallow angles).

From the first build (2026-10-19, grade and turn weights only, everything
else off):

- **Spirals: good.** Turns free, within 0.1-5 % of the exact line on the
  cone and the dome.
- **Switchbacks: short.** On the uniform slope it fell well short of the
  reference zigzag where many switchbacks pay: grade weight 30, turns free,
  2257 against 1739 (30 %).
- **The corner dig hides dips.** With cut and fill free (weight 0), corners
  were dug 2 m into the slope (`corner_cut_max_m`), and the trail looped
  downhill on the ground while its design line climbed steadily: up to 12 m
  of ground lost on the plane. Capping the dig at 0.1 m removed it entirely.
  Excess climb reads the design line, so it cannot see these dips.
- The panel's MARGIN rule hid a winding answer much longer than the
  person's line (a spiral at 997 against an offered 2272).

## The real patch: the Graduation Peak gully (2026-10-26)

The owner's choice: grade and corners varied, as on the formula surfaces
(same grid, same fixed factors). `sandbox.Terrain` and `sandbox.TerrainUp`,
one patch of ground with a line along each diagonal (owner, 2026-10-26:
both, for the contrast):

- **Ground:** the Kachemak Bay 2023 lidar at 1 m, 303 x 301 m about
  59.43459, -151.66042, read the way the refine panel reads it
  (`core.refine.build_grid`) and cached as `.methods_cache/terrain-gully.npz`
  by `build_methods` on first use (not committed; the page carries the
  terrain at 2 m, as for the other surfaces). Both surfaces read it.
- **Lines:** made up (no scouting line is published), corner to corner,
  20 m in from the patch's corners along the diagonals, corridor 40 m:
  - `gully-across`, north-west to south-east, (-136.9, 135.9) to
    (136.9, -135.9): 31 m of climb in 386 m (8 %), across two incised
    gullies whose walls are 80-120 %. "Ground up to" 55 %.
  - `gully-up`, south-west to north-east, (-136.9, -135.9) to
    (136.9, 135.9): 90 m of climb in 386 m (23 %), along the main gully.
    "Ground up to" 80 %.
  - The first gully line (built 2026-10-26, replaced the same day) was the
    spur between the gullies, (-60, -95) to (125, -40), 46 m in 193 m
    (24 %): 13 corners at grade weight 30 with corners cheap, 0 at 300.
- **No formula:** stage 4 is skipped; the page compares with the straight
  line and says so.
- **Builds:** across 41 minutes, up 30 minutes, 8 workers each; 20 and 23
  jumps.

What they show (corners / steepest grade over 10 m / saving on the
straight line):

| grade weight | across, corner weight 0 → 300 | up, corner weight 0 → 300 |
|---|---|---|
| 1-5 | 0 corners at every corner weight; steepest 35 % → 24 % as grade weight rises; 12-30 % saved | 0 corners; steepest 37-41 % throughout; ~24 % saved, 414-425 m |
| 10 | 2 corners with corners free, else 0; steepest 14-21 % | 6 → 5 → 2 → 0 corners; 502-507 m with corners, 426 m without |
| 30 | 0 corners; steepest 10 %; 66 % saved, 432 m | 29 → 8 → 8 → 5 → 6 → 1 corners; steepest 15-17 %, 29 % at corner weight 300; 597-843 m |

- **Across holds its grade by weaving, not by corners.** 8 % overall is
  under every grade the weights ask for, so the line only eases the gully
  walls: longer by up to 12 %, more bend and cut/fill. The corner weight
  does nothing on this line: along each row of the grid the lines are alike
  but for search noise.
- **Up needs corners to hold a grade, and the corner weight sets how
  many.** At grade weight 30 the line doubles its length in 29 corners
  with corners free and gives them up as they get dearer, until at 300 it
  takes the steep pitches (29 %) rather than turn. At grade weight 1-5 it
  goes nearly straight up the gully at 37-41 %: the grade weight is too low
  to pay for a single corner. So on both lines the corner axis only matters
  in the top two grade rows; a grade axis shifted up (say 2-50) would spend
  the grid better. The owner's call.
- **Search misses.** Cost cannot fall as a weight rises. On the across
  line, where no line has a corner, cost should not change with the
  corner weight at all; it wanders by up to 0.6 % (search noise), and by
  3.2 % at grade 30, corner 100 → 167 (825 → 799). On the up line, four
  falls of under 0.4 %. As on the formula surfaces, the fix to try is to
  score every setting's line at every other setting and start from the
  cheapest, before polishing.
