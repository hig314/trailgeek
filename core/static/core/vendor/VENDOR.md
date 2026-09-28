# Vendored browser builds

| File | Source | Version |
|---|---|---|
| maplibre-gl-demshade.iife.js (+ .map), .css | /Users/Hig/Claude_projects/maplibre-gl-demshade/dist (MIT) | 0.1.0, commit ca9d31d, copied 2026-09-25 |

Rebuild in the source repo (`npm run build`) and copy both files; never edit
them here. Once demshade is published to npm this vendoring goes away.

The `.map` must travel with the `.js`: WhiteNoise's manifest storage follows
the `sourceMappingURL` comment and fails `collectstatic` if it is missing.

## Known issues to fix in the demshade source (owner's Mac)

- `trackTerrainCenter` (terrain-center.ts): the function it returns to
  detach does not cancel a re-solve already queued with `map.once('idle')`,
  so the camera can still be moved after detaching; and it re-solves zoom and
  centre at any pitch, which degenerates near 90° (zoom collapses, the
  centre flies to the horizon). trailgeek carries a fixed copy as
  `trackTerrainCentre` in `core/static/core/js/map.js` (2026-09-28); switch
  back to `DemShade.trackTerrainCenter` once the vendored build has both
  fixes.
