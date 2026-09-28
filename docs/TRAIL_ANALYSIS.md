# Trail analysis algorithms — porting spec

The trail design tools to be rebuilt in trailgeek (PLAN.md §5–6) come from two
private, unversioned script folders on the owner's Mac. **Neither is on
GitHub**, so this document is the reference for the port. It records what the
old code does, including its bugs, so the port can reproduce the good parts
and fix the rest deliberately.

| Old location (owner's Mac only) | What |
|---|---|
| `~/PycharmProjects/raster_cruncher_p3/trail_evaluator.py` (1,088 lines) | The evaluator: sample alignments against a DEM, grade, TSA, stats, effort rubric, SVG profiles |
| `~/PycharmProjects/raster_cruncher_p3/trail_lookup_tables.py` | Lookup tables for the router |
| `~/PycharmProjects/raster_cruncher_p3/trai_router.py` | The "Zax" stochastic route finder |
| `~/PycharmProjects/raster_cruncher_p3/Settings.py` | Router test inputs and rubric |
| `~/PycharmProjects/raster_cruncher_p3/data/` (296 MB) | Test DEMs, see *Test data* |
| `~/PycharmProjects/ian_trail_scripts/create_profiles_from_higs_big_csv_231204.py` | Ian's slope-segment profile figures (the model for the D3 profile) |

Old dependencies: GDAL/OGR (`osgeo`), numpy, matplotlib, pandas/geopandas/
shapely (Ian's). The port uses numpy only for the analysis
(`trailgeek_analysis/`), and GeoDjango's GDAL bindings for DEM reads
(`core/dem.py`); numba for the router only, later. No `osgeo` imports.

**Status (2026-09-27): the evaluator (§1) is ported** as
`trailgeek_analysis/evaluate.py`, tested against synthetic planes in
`trailgeek_analysis/tests/`. What changed from the old code, deliberately:

| §1 step | Port |
|---|---|
| 1. Direction | Input never rewritten; `Alignment.runs_uphill` records it. |
| 2. Resample | Per leg, equal steps ≤ spacing, so leg joints are samples; in the route's UTM zone. |
| 3. Sample | Bilinear, not nearest; lidar COG first, Terrarium where there is none; slope computed, not read from a slope raster. |
| 4. Grade / TSA | Grade as before (gaps bridged). TSA geometric (heading vs gradient), null below 3 % slope instead of 0. |
| 5. Running means | Null-aware; edge windows shrink symmetrically (keeps end elevations and climb exact). |
| 6. Stats | Longest runs count every point (single-point runs included); bands by |value|. |
| 7. Effort | |grade| bands (descents no longer fall outside); per leg kind: existing trail costs maintenance only; effort factor per leg. |
| 8–9. Outputs | JSON: summary, per-leg stats, bands, effort, and a downsampled profile for the D3 chart. No shapefiles or SVGs. |
| curvature | Not yet. |

---

## 1. Evaluator

### Inputs
- **Alignments**: a line layer with fields `Name`, `Priority` (1 = primary),
  `Trailhead`. In trailgeek these become `Alignment` rows in a `Project`.
- **DEM** raster (GeoTIFF or SAGA `.sdat`), with a null value (`-9999` or `0`
  in the settings seen).
- **Slope raster**, precomputed outside the script in degrees or %. The
  port should compute slope from the DEM instead (Horn, as demshade does),
  optionally from a smoothed DEM, and keep "bring your own slope" as an option.
- **Settings** (defaults are those used for the Ram Valley and Graduation
  Peak runs):

```python
sample_density = "5 ft"          # spacing along the line (≈1.524 m)
averaging      = {"grade_%": [9, 21], "TSA": [9], "slope_%": [5], "elev_m": [5, 9]}
threshold_sets = {"grade_%": [5, 18, 35], "slope_%": [10, 20, 73, 100], "TSA": [45, 60, 68]}
svg_settings   = {"x_scale": 0.06, "v_exag": 10}
```

Settings dicts in the old file (useful as seed Projects): `Hig_Alpine_route`,
`Hig_Alpine_preferred_singlepart`, `Ian_Alpine_route`, `existing_trail`,
`gentle_climb` (Grewingk 2021 lidar, EPSG:32605), `grad_peak_trail` (NGA
ArcticDEM 2015, EPSG:32605), `Ram_Valley_alignments[_plus]` (Ian's lidar and
Anchorage 2015 lidar, EPSG:32606).

### Pipeline (per alignment)

1. **Direction.** `fix_direction_of_paths` reverses any line whose start is
   higher than its end, so every alignment runs uphill. *Bug to fix:* it
   rewrites the input shapefile in place. The port keeps the stored geometry
   and records direction as a derived flag.
2. **Resample.** Points every `sample_density` along the line, with
   cumulative distance `d`, plus a flag every 100 ft for labelling. Port:
   `shapely.line_interpolate_point` in a local projected CRS (the DEM's UTM).
3. **Sample rasters.** Elevation and slope at each point by
   **nearest-pixel** lookup. Elevation is dropped when null or > 100000.
   Slope in degrees becomes percent as `tan(radians(deg)) * 100`, rounded
   to an integer. Port: bilinear sampling from a COG with rasterio
   (`/vsicurl/` to R2 works), with nearest kept as an option for parity tests.
4. **Grade and TSA**, from each point to the previous one on the same alignment:
   ```
   run   = d_i - d_{i-1}
   grade = (elev_i - elev_{i-1}) / run           # stored as round(grade*100), in %
   absgrade = |grade %|
   TSA   = degrees(acos(|grade %| / slope %))    # trail-slope alignment
   ```
   **TSA** is the angle between the trail and the fall line. 0° means the
   trail runs straight down the fall line, and 90° means it runs along the
   contour. When `|grade| > slope` (local noise), when slope is 0, or when
   slope is missing, TSA is set to 0. *Consider:* the port should set these
   to null rather than 0, because 0 means "fall line", the worst case.
   Grade and TSA are only computed when both elevations exist; the previous
   point only advances on a valid pair, so a run of missing elevations
   gets bridged by one long `run`.
5. **Running averages** (`calculate_running_average`): centred moving mean
   over an odd window `w` of points (`r9_grade_%` is a 9-point mean, ±6 m at
   5 ft spacing). Field names are truncated to 8 characters for shapefiles
   (`r9_grade`, `r21_grad`, `r5_elev_`), so ignore those names.
   *Bugs to fix:* nulls are summed as 0 (`np.nansum` over a zero-initialised
   array), and edge windows use an ad-hoc divisor that assumes the first value
   is the only null. Port: `pandas.Series.rolling(w, center=True,
   min_periods=1).mean()` per alignment, which skips nulls properly.
6. **Stats** (`alignment_stats`), per alignment:
   - `len (m)` (last `d`) and `n_pts`.
   - For each averaged column: min, max, mean and percentiles
     p2, p5, p25, p50, p75, p95, p98.
   - **Climb and descent**: sums of positive and negative elevation steps,
     for raw `elev_m` and each smoothed elevation column. Smoothed climb is
     the one to trust; raw lidar climb is inflated by noise.
   - **Threshold bands**: every value is binned by its **absolute value**
     against the thresholds, giving `n+1` bands, for example grade
     `0–5, 5–18, 18–35, 35–∞`. For each band the output has
     `percent_…`, `length_…` (count × spacing), and `longest_…`, the
     **longest continuous run** of consecutive points in that band.
     *Bug to fix:* a run's length is only compared to the record when the
     next point is in the same band, so a one-point run never registers and
     every run is under-counted by one point.
7. **Effort rubric** (`apply_effort_rubric`): a histogram of each point's
   smoothed side-slope (`r5_slope_%`) and smoothed grade (`r9_grade_%`) into
   bands, then `time += band_length / rate` for construction and for
   maintenance. Slope and grade contributions are summed.
   ```python
   effort_rubric = {
     "slope_column": "r5_slope", "grade_column": "r9_grade",
     "slope_effect": [   # side-slope of the ground the tread is cut into
       {"min": "0 %",  "max": "10 %",    "construct": "10 m/day",  "maintain": "1 mi/day"},   # turnpike
       {"min": "10 %", "max": "20 %",    "construct": "30 m/day",  "maintain": "1 mi/day"},   # drainage structures
       {"min": "20 %", "max": "50 %",    "construct": "100 m/day", "maintain": "1 mi/day"},   # ideal bench
       {"min": "50 %", "max": "90 %",    "construct": "50 m/day",  "maintain": "0.5 mi/day"}, # heavy cut
       {"min": "90 %", "max": "10000 %", "construct": "10 m/day",  "maintain": "0.5 mi/day"}, # walls / ledging
     ],
     "grade_effect": [   # grade of the tread itself
       {"min": "0 %",  "max": "15 %",    "construct": "1 mi/day",   "maintain": "3 mi/day"},
       {"min": "15 %", "max": "25 %",    "construct": "1 mi/day",   "maintain": "2 mi/day"},
       {"min": "25 %", "max": "45 %",    "construct": "20 m/day",   "maintain": "2 mi/day"},   # stairs, hardening
       {"min": "45 %", "max": "77 %",    "construct": "5 m/day",    "maintain": "0.5 mi/day"}, # build stairs
       {"min": "77 %", "max": "10000 %", "construct": "0.5 m/day",  "maintain": "0.1 mi/day"}, # effectively impossible
     ],
     "brush_effect": [],  # placeholder, never implemented
   }
   ```
   Note that the grade histogram uses signed grade against breaks starting
   at 0, so **descending** grades fall outside the rubric and only produce a
   warning. The port should use `|grade|`.
   Units are parsed by `units.smart_units` ("5 ft", "30 m/day", "1 mi/day",
   "10 %"). Use pint or a small parser; keep the strings in the UI.
8. **Outputs**: a point table (CSV and shapefile) with columns `pathname,
   id, d, start, end, flag, priority, trl_head, elev_m, x, y, slope_%,
   grade_%, absgrade, TSA` plus the running averages; a per-alignment summary
   CSV; per-alignment point and line shapefiles; and SVG profiles.
   In trailgeek these become a cached `Profile` (arrays) and summary columns.
9. **SVG profile** (`draw_svg_profiles`): 468×648 px, the elevation line
   with segments shaded grey by grade class (separators 5/25/40/77 %),
   `x_scale` 0.06, 10× vertical exaggeration. Superseded by the D3 profile.

`add_curvature` exists but is switched off: turn radius from the bearing
change between successive points (`100·d/θ` cm, capped at θ = π/2, with a
warning that tight corners need denser sampling). Worth reviving for
switchback detection.

## 2. Ian's profile figures (the model for the D3 profile)

Input: the evaluator's point table (`trl_head, pathname, priority, d,
elev_m, x, y, r9_grade`).

1. Build a (distance, elevation) LineString per route.
2. Smooth with `simplify(1 m)`, then **generalise with `simplify(2 m)`** to
   break the profile into constant-grade **slope segments**. For each segment:
   rise, run, grade.
3. Draw each segment as a band extruded ±25 ft about the profile, coloured by
   grade with a diverging RdYlBu ramp and breaks `-100, -35, -18, -5, 5, 18,
   35, 100` %. Uphill is red and downhill is blue; the colour list is
   `['#d73027','#fc8d59','#fee090','#ffffbf','#e0f3f8','#91bfdb','#4575b4']`,
   reversed.
4. Two stacked panels sharing the x axis: **10× vertical exaggeration** above,
   **1:1** below. Units are miles and feet. Other routes from the same
   trailhead are drawn in grey behind the primary route.
5. Outputs: one SVG per primary route, an "All Profiles" SVG, a slope-segment
   shapefile (EPSG:32606), and an xlsx of route stats (miles, climb and
   descent in ft, median, max and min grade).

The D3 version should keep: the segment generalisation (as a server-side
step or in JS), the diverging grade ramp, the dual exaggerated/true panels,
and grey sibling routes. It adds a hover cursor linked to the map and brush
selection with stats for the selected stretch.

## 3. Router ("Zax")

A stochastic, agent-based route finder. It is **not** least-cost routing.
Walkers alternate between starting at the start and at the end. Each one
steps pixel by pixel to one of 8 neighbours, drawn at random with weights
that favour good trail geometry. Traffic accumulates in rasters, and the
route network emerges from where walkers go.

### Lookup tables (`trail_lookup_tables.py`)
- `build_direction_table(penalty)`: 8×360. For each of the 8 step directions
  and each bearing to the destination,
  `1 - penalty * acos(cos(|vector - bearing|)) / π`. A penalty of 1 makes a
  step directly away from the destination impossible.
- `build_aspect_vs_vector_table()`: three 8×360 tables indexed by step
  direction and terrain aspect: `TSA` (0–90), `down_to_left` (which side is
  downhill), and `downward` (whether the step goes down). There is a comment
  that the vector convention here may not match the x,y convention used in
  the router; verify this in the port.
- `build_rubric_tables(rubric)`: 91×150, TSA (0–90°) by terrain slope
  (0–149 %). Grade is derived as `terrain / sqrt(1 + tan²(TSA))`, and the
  value is the **minimum** of three step-function scores for grade, TSA and
  terrain. Beyond the end of any table the score is 0.

Rubric (from `Settings.run_settings`), as (upper bound, weight):
```python
rubric = {
  "TSA":     [(45, 0.01), (68, 0.1), (90, 1)],
  "Grade":   [(0.01, 0.1), (0.05, 0.2), (0.1, 0.5), (0.15, 1), (0.25, 0.5), (0.55, 0.01), (0.77, 0.001)],
  "Terrain": [(0.05, 0.03), (0.1, 0.1), (0.25, 0.2), (0.6, 1), (0.9, 0.3), (2, 0.02)],
}
direction_penalty = 0.5
scale = 50000            # higher → walkers less likely to stop
total_iterations = 2_000_000
reset_interval = 10000   # wear rasters are snapshotted and zeroed this often
```

### Step weights (`pick_neighbor`)
For each of the 8 neighbours:
```
chances = scale * rubric_table[TSA, terrain_slope] + bonus
bonus   = 2*scale*opposite_wear[c] + same_wear[c] + Σ weight_arrays[y, x]
if chances > 0:
    if already visited by this walker: chances = 2          # discourages backtracking
    if down_to_left != prev_downward:  chances /= 150       # a switchback
    if downward == up_going:           chances /= 20        # wrong vertical direction
    weight = int(chances * direction_table[vector, bearing_to_destination])
```
There is also a "stop" option with weight 1. The walker's own cell gains
`same_wear += 1` when there were good options, and loses up to 5 when it was
boxed in. Up-going walkers leave `up` wear and down-going walkers leave
`down` wear. A walker is rewarded for following tracks from the opposite
direction, which is how the two ends meet.

### Known bugs to fix in the port
- **Stop condition:** `plane_distance((x, y), destination) < 20` measures
  from the walker's *start* coordinates, not its current `location`, so a
  walker essentially never registers arrival. It should use the current
  cell. "20 pixels = 10 m" also assumes 0.5 m pixels; use metres.
- `bonus` uses `weight_array[y, x]` (the current cell) rather than the
  candidate cell.
- Output rasters hardcode EPSG:26905; `array2raster` flips rows. Take CRS and
  transform from the DEM.
- It is pure-Python pixel loops: the comment says 800k iterations took 11
  hours. The inner loop needs numba (or a C/Rust kernel) and must run as a
  background job with progress reporting.

Outputs: `up.tif`, `down.tif` and `traffic.tif` (visit counts), snapshots
every reset interval, and `lengths.csv` (walk-length histogram).

PLAN.md also proposes `router-lcp`, a deterministic least-cost path using
the same rubric table as the cost surface (`skimage.graph.MCP_Geometric`),
as a baseline to compare Zax against.

## 4. Test data

In `~/PycharmProjects/raster_cruncher_p3/data/` on the owner's Mac (not in
git). All files are Float32 GeoTIFFs in NAD83 / UTM 5N (EPSG:26905), from the
Grewingk area, Kachemak Bay:

| File | Size | Notes |
|---|---|---|
| `Alpine_Ridge_Ascent.tif` | 4152×2720, 0.5 m | elev 3.7–482 m; has `_Slope` (%), `_Aspect`, `_shade`, 3 m contours |
| `test_lidar.tif` | 2912×1879, 0.5 m | with slope, aspect |
| `test_lidar_resample_smooth.tif` | 2910×1878 | smoothed; with slope, aspect, shade, contours |
| `test_lidar_3m.tif` | 485×313, 3 m | small: **the one to commit as a test fixture** |

Router test start/end pixels: `test_data` (80,1000)→(2200,150) on the smooth
test raster; `big_test_data` (610,1310)→(3150,430) on Alpine Ridge Ascent.

The alignment shapefiles used in real runs live on external drives
(`/Volumes/Pavlovs_sister/Science/Trail_science/…`,
`/Volumes/Hesketh/Trail_science/…`) and need to be brought in by the owner.
Public Grewingk lidar is also on landslidescience's R2 bucket (see
docs/SISTER_PROJECTS.md).
