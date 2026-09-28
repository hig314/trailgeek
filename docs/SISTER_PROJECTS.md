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

### How trailgeek shares code with landslidescience (2026-09-28)

The shared files are **synced, not hand-copied**. `tools/shared.json` pins a
landslidescience commit and the SHA-256 of each file's upstream content;
`tools/sync_shared.py` does the rest:

| Command | Does |
|---|---|
| `python tools/sync_shared.py check` | CI step: every copy still matches its pin. An edit here fails the build. |
| `python tools/sync_shared.py status --repo ~/path/to/landslidescience` (or `--github`) | Which shared files changed upstream since the pin |
| `python tools/sync_shared.py sync --repo …` (or `--github`) | Copy them all at `origin/main` (`--ref` for another), rewrite the provenance headers, update the pin |

Synced today, from landslidescience @ 1c63e60:

| trailgeek | landslidescience |
|---|---|
| `core/static/core/js/basemaps.js` | `inventory/static/inventory/js/basemaps.js` |
| `core/static/core/js/ls_hash.js` | `inventory/static/inventory/js/ls_hash.js` |
| `core/static/core/js/dem_shade_bridge.js` | `inventory/static/inventory/js/dem_shade_bridge.js` |
| `core/static/core/vendor/maplibre-gl-demshade.iife.js` (+ `.map`) | `inventory/static/inventory/js/vendor/…` (demshade 3adeb09) |

The .js copies carry a four-line provenance header (the check strips it);
the vendored build is byte-identical. `basemaps.js` carries thumbnail paths
under `inventory/img/` that do not exist here; trailgeek does not call
`thumbnailUrl`. `tg_sample.js` re-implements the Terrarium decode from
`dem_fill.js` rather than copying the whole compositing protocol, because
demshade now does the compositing in its worker (`fill`).

**What was still duplicated, and what to do about it.** The logic that turns
the catalogue into a DEM stack lives inline in /lidar/'s template
(`pages/templates/pages/lidar_preview.html`), so trailgeek could only copy
it by hand, and the two had already drifted: /lidar/ gained the boundary
fill, the baked context, the 512 mesh and the relative threshold on
2026-09-26/28, and trailgeek had its own fixed centre tracker. That logic
is now one DOM-free module, **`core/static/core/js/dem_stack.js`**
(`window.LSDemStack`), written in trailgeek from /lidar/ @ 1c63e60 and used
by trailgeek's map. It is listed under `proposed` in `tools/shared.json`:

| `LSDemStack.` | Replaces in lidar_preview.html |
|---|---|
| `outerRings(geometry)` | `outerOf[p.id] = cs.map(poly => poly[0])` |
| `contextOpts(ctx, beyond)` | the two `DemShade.addDataset(fc.context.id…)` calls |
| `surveyOpts(p, fill, footprint)` | the options in `ensureCtxReg` |
| `terrainSource(id, p, {size, minzoom})` | the `map.addSource('dem', …)` in `rebuildDem` |
| `trackTerrainCentre(map, opts)` | `DemShade.trackTerrainCenter(map, {threshold: …})`, with the fixes in vendor/VENDOR.md |
| `relativeThreshold(fraction, min)` | `DemShade.relativeThreshold`, which is a fixed 0.5 m on MapLibre 5 (VENDOR.md) |
| `floorTerrainMinimum(map, zMin)` | `floorTerrainMinimum` / `watchTerrainForFloor` |
| `skySwitch(map, sky)` | the `skyOn` bookkeeping in `applySky` |

**Handoff to the owner's local session** (a cloud session cannot push to
landslidescience; the landslidescience-side steps follow its own
test-before-ship rule):

1. Copy trailgeek's `core/static/core/js/dem_stack.js` to landslidescience
   `inventory/static/inventory/js/dem_stack.js`, dropping the "Status" note
   in its header. Load it after `dem_shade_bridge.js` in
   `lidar_preview.html` and replace the inline pieces in the table above.
   The page keeps its controls (`ctx`, `ctxlive`, `tmesh`, `fog`) and
   passes plain values in. Test /lidar/ (context on/off, live 3DEP, both
   meshes, a deep survey such as Pedersen at high exaggeration, a 3D
   permalink), then ship it there.
2. Fix the three demshade issues in vendor/VENDOR.md in the demshade
   source, rebuild, and vendor the build into landslidescience.
3. In trailgeek: move the `proposed` entry into `files` in
   `tools/shared.json` (`"header": true`), run `python tools/sync_shared.py
   sync --repo …`, and open a PR. From then on the module has one home.

`hig-maplibre-kit` (PLAN.md §2) stays the longer-term home for all of these,
still deliberately deferred: it edits landslidescience, which has parallel
work streams (its `WORKSTREAMS.md`). The sync above is what keeps the two
sites identical in the meantime, and it moves to the kit unchanged when the
kit exists (only the pinned repo and paths change).

### Lidar data (public, usable today)
- **Catalogue:** `https://landslidescience.org/lidar/catalog.geojson`
  (checked 2026-09-25: 43 surveys). Each feature has a footprint and these
  properties: `id, title, year, region, product, source, source_url,
  horizontal_crs, vertical_datum, native_res_m, z_min, z_max, min_zoom,
  max_zoom, bounds, cog_url, pmtiles_url, slope_url, slope_tiles_url,
  slope_step, tiles_url, ortho_url, fill_mode, coverage_km2, notes`, plus
  byte sizes. `catalog-gated.geojson` lists the gated surveys and needs a
  signed-in session. A top-level `context` member (when the bake exists)
  describes `ctx_3dep`, the USGS 3DEP 1/3 arc-second context baked around
  the surveys (`tools/lidar/bake_context.py`: Mapbox terrain-RGB PMTiles,
  z5-z13, plus a tile-Worker `tiles_url`). `import_dem_catalog` stores it as
  a context row; the map composites lidar over it, and the evaluator reads
  it (through `tiles_url`) between lidar and Terrarium.
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
`VENDOR.md` there), synced from landslidescience's vendored copy so the two
sites run the same build. A cloud session **can't rebuild it**. Use the vendored
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
