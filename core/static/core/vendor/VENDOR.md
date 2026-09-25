# Vendored browser builds

| File | Source | Version |
|---|---|---|
| maplibre-gl-demshade.iife.js (+ .map), .css | /Users/Hig/Claude_projects/maplibre-gl-demshade/dist (MIT) | 0.1.0, commit ca9d31d, copied 2026-09-25 |

Rebuild in the source repo (`npm run build`) and copy both files; never edit
them here. Once demshade is published to npm this vendoring goes away.

The `.map` must travel with the `.js`: WhiteNoise's manifest storage follows
the `sourceMappingURL` comment and fails `collectstatic` if it is missing.
