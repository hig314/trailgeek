# Vendored browser builds

| File | Source | Version |
|---|---|---|
| maplibre-gl-demshade.iife.js (+ .map) | landslidescience `inventory/static/inventory/js/vendor/`, which vendors /Users/Hig/Claude_projects/maplibre-gl-demshade/dist (MIT) | demshade 3adeb09, from landslidescience 1c63e60, synced 2026-09-28 |
| maplibre-gl-demshade.css | /Users/Hig/Claude_projects/maplibre-gl-demshade/dist (landslidescience does not carry it) | 0.1.0, commit ca9d31d, copied 2026-09-25 |

The .js and .map are taken from landslidescience, byte for byte, by
`python tools/sync_shared.py sync --repo PATH` (or `--github`), so both sites
run the same build. `tools/shared.json` pins the commit and each file's hash,
and CI fails if a copy is edited here. To change the build: rebuild in the
demshade source repo (`npm run build`), vendor it into landslidescience
first, then sync. Never edit the files here. Once demshade is published to
npm this vendoring goes away.

The `.map` must travel with the `.js`: WhiteNoise's manifest storage follows
the `sourceMappingURL` comment and fails `collectstatic` if it is missing.

## Known issues to fix in the demshade source (owner's Mac)

Still present in 3adeb09 (read from the source map, 2026-09-28):

- `trackTerrainCenter` (terrain-center.ts): the function it returns to
  detach does not cancel a re-solve already queued with `map.once('idle')`,
  so the camera can still be moved after detaching.
- `trackTerrainCenter` re-solves zoom and centre at any pitch, which
  degenerates near 90° (zoom collapses, the centre flies to the horizon).
- `relativeThreshold` reads the camera height from
  `map.getFreeCameraOptions()`, which MapLibre 5.24 does not have, so it
  always returns its minimum (0.5 m): /lidar/'s "2 % of camera height" is
  in effect a fixed 0.5 m. `map.transform.getCameraAltitude() -
  map.getCenterElevation()` gives the height on MapLibre 5.

trailgeek carries fixed versions of both as `LSDemStack.trackTerrainCentre`
and `LSDemStack.relativeThreshold` in `core/static/core/js/dem_stack.js`.
Once the demshade build has the fixes, those two become thin wrappers over
`DemShade.trackTerrainCenter` / `DemShade.relativeThreshold`.
