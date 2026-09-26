# Sister projects and reusable pieces

trailgeek's rule is *one copy of every shared thing*. Before writing a map
helper, a server utility or a pipeline step, check whether one of these
already has it.

**Visibility matters for cloud sessions.** A Claude Code session on the web
only sees GitHub. Several of these projects exist only on the owner's Mac;
they are marked **local only**, and their relevant content is summarised here
or in [TRAIL_ANALYSIS.md](TRAIL_ANALYSIS.md).

| Project | Where | Visibility |
|---|---|---|
| landslidescience.org | [hig314/landslidescience](https://github.com/hig314/landslidescience) | public |
| groundtruthalaska.org | [hig314/groundtruthalaska](https://github.com/hig314/groundtruthalaska) | **private** |
| Tethys monitoring stack (hosts landslidescience's DB and Caddy) | hig314/tethys-timescale-grafana | private |
| maplibre-gl-demshade | `~/Claude_projects/maplibre-gl-demshade` | **local only** (not yet on GitHub or npm) |
| raster_cruncher_p3, ian_trail_scripts | `~/PycharmProjects/…` | **local only**, see TRAIL_ANALYSIS.md |
| soil_model, terrain_sandbox, DEM_topo | `~/Claude_projects/…` | **local only** |

## landslidescience.org (public repo)

Django 5.2 in Docker, behind Caddy on the monitoring droplet
(`143.198.140.54`). Landslide data is in another stack's PostGIS, read by raw
SQL; Django's own tables are SQLite. The frontend is vanilla ES5 IIFE
scripts, MapLibre 5 from unpkg, and no build step. Read its `ONBOARDING.md`,
`HAZARDS.md` and `CLAUDE.md` before copying anything.

Reusable pieces:

| Piece | Path in that repo | Use in trailgeek |
|---|---|---|
| Basemap descriptors and tile-URL transforms | `inventory/static/inventory/js/basemaps.js` | Basemap picker (Phase 1) |
| URL-hash view-state codec | `inventory/static/inventory/js/ls_hash.js` | Shareable views |
| Pointer policy: tool group, click dispatcher, cursor, drags, Escape | `inventory/static/inventory/js/ls_tools.js` | Every map tool (draw, profile, pick) |
| High-resolution PNG export | `inventory/static/inventory/js/ls_export.js` | Map export |
| demshade bridge and DEM compositing | `inventory/static/inventory/js/dem_shade_bridge.js`, `dem_fill.js` | Lidar over 3DEP |
| Ranged PMTiles/COG server with auth gating | `landslidescience/lidar_serve.py` | Serving gated DEMs (Django has no HTTP Range support) |
| Lidar viewer (3D terrain, sky/fog, DEM difference) | `pages/templates/pages/lidar_preview.html` | Reference for the 3D view |
| Lidar pipeline: COG, terrain-RGB PMTiles, slope PMTiles, catalogue | `tools/lidar/build_lidar.py`, `datasets.json`, `make_catalog.py`, `r2_sync.sh` | trailgeek reads its output; it doesn't rebuild it |
| First-party Umami analytics proxy with SSO | `landslidescience/analytics.py`, `_analytics.html` | Phase 1 or later |
| Hosted files app | `files/` | If needed |
| Role groups, `init_groups`, auto-join signal | `inventory/auth.py`, `inventory/signals.py` | Already copied in shape to `core/roles.py` |
| Photo ingest with EXIF GPS and HEIC | `inventory/photos.py` | Trail photos, later |

The plan (PLAN.md §2) is to move the shared JS modules into their own package,
`hig-maplibre-kit`, vendored by both sites. **This is deliberately not done
yet**: it edits landslidescience, which has parallel work streams (see its
`WORKSTREAMS.md`) and its own test-before-ship rule. Do it as a separate,
reviewed change. Until then, copy what you need and put a header comment
naming the source file.

**Copied so far (Phase 1, verbatim, from landslidescience @ 7327b63):**
`basemaps.js`, `ls_hash.js`, `dem_shade_bridge.js`, into
`core/static/core/js/`. `basemaps.js` carries thumbnail paths under
`inventory/img/` that do not exist here; trailgeek does not call
`thumbnailUrl`. `tg_sample.js` re-implements the Terrarium decode from
`dem_fill.js` rather than copying the whole compositing protocol, because
demshade now does the compositing in its worker (`fill`).

### Lidar data (public, usable today)
- **Catalogue:** `https://landslidescience.org/lidar/catalog.geojson`
  (checked 2026-09-25: 43 surveys). Each feature has a footprint and these
  properties: `id, title, year, region, product, source, source_url,
  horizontal_crs, vertical_datum, native_res_m, z_min, z_max, min_zoom,
  max_zoom, bounds, cog_url, pmtiles_url, slope_url, slope_tiles_url,
  slope_step, tiles_url, ortho_url, fill_mode, coverage_km2, notes`, plus
  byte sizes. `catalog-gated.geojson` lists the gated surveys and needs a
  signed-in session.
- **Files** are on Cloudflare R2 bucket `landslidescience-lidar`, served at
  `https://lidar.landslidescience.org/cog/<id>` (archive COG, local UTM,
  NAD83(2011)) and `https://lidar.landslidescience.org/pmtiles/<id>`
  (terrain-RGB PMTiles, plus a slope PMTiles). Use the URLs in the
  catalogue rather than building them.
- **Gated surveys** are for signed-in users only on landslidescience. A
  browser on trailgeek.org can't use that session, so gated data would need
  trailgeek's own ranged proxy and gate.
- **CORS:** the R2 bucket and tile Worker must allow the trailgeek.org
  origin before the archives load from this site (docs/OPERATIONS.md, open
  items). The catalogue endpoint is already `Access-Control-Allow-Origin: *`.
- **Evaluator test areas are in the catalogue:** `grewingk_2021` (Alpine
  Ridge, Grewingk) and `anchorage_2015` (Ram Valley). Also relevant:
  `kbay_2023`, `homer_2019`, `seldovia_2019`, `glen_alps_2024`,
  `eagle_river_2024`. Seed trailgeek's `DemSource` table from this catalogue
  (PLAN.md §4).

## groundtruthalaska.org (private repo)

Django 4.2, uWSGI and nginx on its own droplets (`gta-web`, `gta-dev`). It
has no geometry fields. The relevant part is its **tools** pattern, in
`TOOLS.md`, `src/gtt/tools/` and `ops/deploy-tool`:
- A tool is a separate repo whose static `dist/` holds `index.html` and
  `tool.json`.
- It is rsynced to `/var/www/tools/<slug>/`, served at `/tools-static/`,
  iframed at `/tools/<slug>/`, and embeddable in articles with
  `{% tool "slug" %}`.

trailgeek tools use the same `dist/` + `tool.json` contract (PLAN.md §5), so
a trailgeek tool can be embedded in a GTA article unchanged. GTA also has the
Graduation Ridge trail race pages and a large trip-report corpus to
cross-link.

## maplibre-gl-demshade (local only)

A MapLibre plugin (MIT, TypeScript, Vite). It renders hillshade, slope,
aspect, elevation banding and DEM difference in a Web Worker, from
terrain-RGB or Terrarium tiles (PMTiles, XYZ, or an ArcGIS ImageServer such
as USGS 3DEP). It also provides a `raster-dem` source, so PMTiles can drive 3D
terrain. `DemShadeControl` is a slider panel with a 3D toggle.

trailgeek vendors its IIFE build in `core/static/core/vendor/` (see
`VENDOR.md` there). A cloud session **can't rebuild it**. Use the vendored
copy, and ask the owner to rebuild if a change is needed. Publishing it to
GitHub and npm is an open item.

Browser API as used here: `new MapLibreGlDemShade.DemShade()`,
`addSource(id, {tiles|pmtiles, encoding, maxzoom, alphaNoData})`,
`rasterSource(id, shade)`, `rasterDemSource(id)`, `tileUrl(id, shade)`,
`new DemShadeControl({demshade, source, shade, opacity, collapsed})`.
Shading options: `azimuth, altitude, hillshade, blend, slope, banding,
aspect, exaggeration`.

## Candidate collaborator tools (local only)

- **soil_model**: a 1D soil column coupling heat, Richards-equation water
  and freeze/thaw. It has a Python/FiPy reference, a pure-JS port
  (`web/js/model.js`) cross-checked against the Python, and a D3 web sandbox.
  Its next planned step is lateral (Dupuit) drainage on slopes, which is
  relevant to trail drainage. It will be the first external tool bundle
  (`soil-column`).
- **Snow**: nothing built yet. soil_model's roadmap includes a multi-layer
  energy-balance snowpack. Colleagues are expected to contribute here.
- **DEM_topo**: a summit finder with prominence and isolation filters, from
  big lidar COGs to GeoPackage. Its output could become a layer of named
  peaks along trails.
- **terrain_sandbox**: a D3/canvas landscape-evolution toy, already a static
  bundle. It is educational.
- **Map_app ("FieldMap")**: an offline Android map app that renders trails and
  GPS tracks from MBTiles. It could consume trailgeek exports later.

## GeoLibre (external, MIT)

[opengeos/GeoLibre](https://github.com/opengeos/GeoLibre) is a
browser/desktop GIS built with React, MapLibre, deck.gl, DuckDB-WASM and more
than 1,000 Whitebox WASM tools. It is **not** used as trailgeek's framework,
because it is an app rather than a library. trailgeek borrows its plugin API
shape (`activate(app)`, `app.getMap()`, `addGeoJsonLayer`, `registerRightPanel`,
`readRasterWindow`) so tools can run in both, and will offer an "Open in
GeoLibre" hand-off using its `?data=` URL parameter. Its plugin API is
documented in `docs/plugin-api.md` in that repo.
