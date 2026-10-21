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

From the build of 2026-10-20 (every factor at its default, grade and corner
weights varied, 6 x 6 settings, thorough runs + polishing + tracing):

- **With enough search, the engine finds the answer.** At all 108 settings
  over the three surfaces, the engine's line is never more than 1 % worse
  than the formula's (cone, worst 0.8 %) and often better, by turning less
  (dome, up to 7 %; plane, where its fewer, longer switchbacks beat the
  reference zigzag at the cheapest grade).
- **But one run is not enough search.** A third to a half of the settings
  were first improved by polishing from a neighbour's line, and a fifth to
  a third again by a tracing reaching them; on the plane, settings were still
  improving after four rounds. The switchback shortfall of the first build
  (30 %) was the search, not the scoring. The Refine panel runs one search:
  seeding it from more than one start (a neighbour's answer, or the line
  traced from one) is the lead this gives.
- Jumps (a change of route along an edge): cone 20, dome 10, plane 40 of 60
  edges' worth; the plane's include 22 at settings still improving in the
  last re-tracing round.

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

## Next

A real patch: the Graduation Peak gully near 59.43459, -151.66042, about
200 m across, from a made-up start and end (no scouting line is published),
with the factors the owner chooses to vary.
