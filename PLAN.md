# trailgeek.org — build plan

*Drafted 2026-09-24; status updated 2026-09-27. A trails-focused web map portal that shares tools, data
and hosting patterns with landslidescience.org and groundtruthalaska.org.*

## 0. Status (2026-09-27)

| Phase | State |
|---|---|
| 0 — Scaffold | **Done, live.** Repo public at hig314/trailgeek; droplet `trailgeek-web` provisioned; Cloudflare DNS; Caddy TLS; CI green; home map with demshade + 3D confirmed working. Not done: `hig-maplibre-kit` extraction (deferred, see below); R2 bucket `trailgeek-data` (create when uploads arrive in Phase 1); analytics; backups. |
| 1 — Portal MVP | **Merged** (hig314/trailgeek#1). Models, admin, DEM catalogue import, GPX upload, live MVT, demshade + 3D from the lidar catalogue, D3 profile, detail panel, URL hash. |
| 2 — Analysis engine | **Built, in review** (branch `claude/trailgeek-project-b53hqm`), pulled forward together with alignment editing: `trailgeek_analysis` evaluator with tests, legs, live and saved evaluations, per-leg effort, compare table, variants, line import. Not yet: segment generalisation, curvature/switchbacks, CSV/SVG export. |
| 3–6 | Not started. The editor (Phase 1's "Draw") is built without Terra Draw; see below. |

Changes from the original plan, decided during Phase 0:
- **`hig-maplibre-kit` is deferred.** Moving the shared JS out of
  landslidescience edits that repo, which has parallel work streams and its
  own test-before-ship rule. It will be a separate, reviewed change. Until
  then, shared files are synced from landslidescience by
  `tools/sync_shared.py` against a pin in `tools/shared.json`, and CI fails
  on a local edit (2026-09-28). The DEM-stack logic /lidar/ kept inline is
  factored into `dem_stack.js`, proposed for landslidescience
  (docs/SISTER_PROJECTS.md).
- **Ubuntu 24.04 with Docker from Docker's apt repo**, set up by
  `ops/provision.sh`, instead of DigitalOcean's Docker image, so the droplet
  can be rebuilt from the repo.
- **Work happens on branches and PRs**, because development moves to
  Claude Code on the web. Deploys still run from the owner's Mac
  (docs/OPERATIONS.md).
- Details that used to live only on the owner's Mac are now in
  `docs/`, especially the algorithm spec in docs/TRAIL_ANALYSIS.md.

Decided for near-horizontal 3D views (2026-09-28):
- MapLibre sizes draped lines from the scale at the map centre, and with
  terrain both its clamping and demshade's centre tracker re-derive zoom from
  height / cos(pitch). Near 90 degrees that sent the centre to the horizon
  and made trails ~40 m wide stripes. map.js now switches the re-solving off
  above 80 degrees, rescales line widths by the square root of near-to-centre
  ground scale when pitched, and places side views explicitly. Draped lines
  are still ground stripes, so very distant ones thin out and can break up at
  grazing angles; true 3D lines would need a custom or deck.gl layer.

Decided while building trail design (2026-09-27):
- **Alignment = ordered Legs** (`core.models.Leg`): each leg is existing
  trail or a build effort (new / reroute / restore) with an effort factor.
  `Alignment.geom` is derived; legs share joint vertices. This is the
  "continuous alignment with legs that are separate build efforts" model;
  §4 below predates it.
- **One copy of the analysis, server side.** `trailgeek_analysis` is pure
  numpy; the browser never recomputes grade or TSA. Live edits are
  evaluated by the same Huey job as saved alignments (debounced in the
  editor, polled), so the tweak-time numbers are the saved numbers.
- **GeoDjango's GDAL instead of rasterio** for DEM reads (`core/dem.py`):
  the image already has system libgdal for GeoDjango, and a second GDAL copy
  (rasterio wheels) in the same process is a known source of PROJ/driver
  clashes. `/vsicurl/` reads the R2 COGs in windows.
- **TSA is geometric**: the angle between the trail heading and the
  terrain gradient, from four probe points in the route's own UTM grid, so a
  lidar archive in another UTM zone needs no convergence correction. It
  equals acos(|grade|/slope) on a plane, without the clipping.
- **Terrarium is the fallback DEM** for points outside every lidar
  footprint, and results say which DEM each stretch came from.
- **The package lives at the repo root** (`trailgeek_analysis/`), not under
  `packages/`, until it has a second user; it imports nothing from Django.
- **Custom editor, not Terra Draw**: legs share joints, snap to trails and
  follow the trail network, which Terra Draw's generic modes do not model.
  landslidescience's Terra Draw wrapper is polygon-specific.

Decided during Phase 1:
- **`Track.geom` is a 3D LineString (Z = device elevation) with timestamps
  in a `times` JSON column**, not LineStringZM: GeoDjango fields carry Z
  but not M. Seconds since `taken_at`, one per vertex, is enough for replay.
- **Client-side profiles sample AWS Terrarium tiles**, not the lidar. That
  is a quick look; the authoritative lidar profile is the Phase 2 evaluator's
  job. Lidar PMTiles reads from a second origin also depend on R2 CORS.
- **Lidar surveys are registered once each in demshade, composited over
  Terrarium** (`fill`, `fillMode: 'missing'`), so one registration serves
  both shading and 3D terrain, instead of the lidar preview's three.
- **The MVT view is a plain Django view**, cached 60 s in the process cache.
  Good enough for hundreds of trails; a tile cache in Caddy or Redis is the
  step after that.

## 1. What it is

A public Django site at `https://trailgeek.org` with three faces:

1. **Portal** — a MapLibre map of trails, GPS tracklines, and design
   alignments over lidar/3DEP terrain, with 3D views, profiles and summary
   statistics.
2. **Tool library** — a pluggable set of analysis tools (trail evaluator,
   profile, router, and later snow / soil-water tools from collaborators),
   each a self-contained package that anyone can contribute.
3. **Content** — pages, project write-ups and methods, cross-linked with
   Ground Truth Alaska articles.

Design principle borrowed from landslidescience: *one copy of every shared
thing*. Trailgeek reuses, rather than re-implements, the pieces below.

## 2. What already exists, and what trailgeek takes from each

| Source | Take | How |
|---|---|---|
| `landslidescience` (Django 5.2, Docker, Caddy, MapLibre 5, no build step) | Dockerfile / compose / entrypoint pattern; `lidar_serve.py` ranged PMTiles+COG server with gating; `files` app; `pages` app; auth groups + `init_groups`; Umami first-party analytics; photos ingest (later) | Copy with a header naming the origin; extract to a package the first time the two copies diverge |
| `inventory/static/inventory/js/{basemaps,ls_hash,ls_tools,ls_export,dem_shade_bridge,dem_fill}.js` | The whole set | Move into a new repo **`hig-maplibre-kit`** (Vite, IIFE + ESM builds). Both sites vendor the IIFE, the way demshade is vendored today |
| `maplibre-gl-demshade` | Hillshade / slope / aspect / banding worker, `raster-dem` source for 3D terrain, DemShadeControl | npm dependency (publish it) or vendored IIFE |
| landslidescience lidar pipeline (`tools/lidar/*`, `datasets.json`, R2 bucket, `catalog.geojson`) | The **DEM catalog** itself | Trailgeek reads the public catalog URL and samples the same R2 COGs / terrain-RGB PMTiles. No second copy of lidar |
| GTA `gtt.tools` pattern (`TOOLS.md`, `{% tool "slug" %}`, `/tools/<slug>/` iframe of a static `dist/` + `tool.json`) | The **tool bundle contract** | Trailgeek tools use the same `dist/ + tool.json` shape, so a trailgeek tool can be embedded in a GTA article unchanged |
| `raster_cruncher_p3/trail_evaluator.py`, `trail_lookup_tables.py`, `trai_router.py`, `ian_trail_scripts` | The algorithms (grade, TSA, running averages, threshold/longest-run stats, effort rubric, slope-segment profiles, the "Zax" stochastic router) | Rewrite as a tested pure-Python package `trailgeek_analysis` (numpy / shapely / rasterio), no GDAL-Python, no hardcoded paths |
| `soil_model/web` (D3 + JS engine), `terrain_sandbox` (D3), `DEM_topo` (summit finder) | First external tools | Wrap as tool bundles; DEM_topo output loads as a layer |
| GeoLibre (`opengeos/GeoLibre`, MIT) | Plugin API shape; `@geolibre/embed` iframe; WASM Whitebox tools; PMTiles/COG/GeoParquet readers | See §5 |

## 3. Stack

| Layer | Choice | Why |
|---|---|---|
| Backend | Python 3.12, **Django 5.2 LTS**, **GeoDjango** | Matches landslidescience. Unlike landslides, trails are *this site's* data, so use real ORM models on PostGIS, not raw SQL against another stack's DB |
| DB | PostGIS 16 (own container) | Trail geometry, tracks, samples, jobs |
| Jobs | **Huey + Redis** (same as GTA) | Trail analysis and routing are long-running and need rasterio/GDAL; keep them out of request threads |
| Raster IO | rasterio (`/vsicurl/` to R2 COGs), pyproj, shapely, numpy, numba (router only) | One Docker image with `libgdal` shared by web and worker |
| Frontend (site) | MapLibre GL JS 5.x, pmtiles, Terra Draw, demshade, **D3 v7**, vanilla ES modules, no bundler | Same as landslidescience; keeps the barrier to contribution low |
| Frontend (tools) | Vite + TypeScript per tool, built to `dist/` | Tools are the one place a build step earns its keep (WASM, workers, TS types) |
| Vector tiles | `ST_AsMVT` Django view for live trails (cached); tippecanoe → PMTiles for static reference layers | Live edits appear without rebuilds |
| Static / object storage | Cloudflare R2 bucket `trailgeek-data` (uploads, derived rasters, PMTiles); WhiteNoise for site static | Same as landslidescience lidar |
| Web server | Caddy in the compose stack (auto TLS) | Cloudflare DNS proxied → droplet |
| Analytics | Umami, first-party proxied (copy `analytics.py`) | |
| License | MIT for code; per-dataset licenses for data | Matches demshade |

**Hosting decision:** a new DigitalOcean droplet (`trailgeek-web`, sfo3, 4 GB
to start, plus a `trailgeek-dev` later). The monitoring droplet that hosts
landslidescience is a 4 GB box with little headroom, and the worker will
burn CPU. Compose stack: `caddy`, `web`, `worker`, `postgis`, `redis`,
`umami`. Deploy flow is the landslidescience one: `git pull`, `docker compose
build && up -d`, and **dev → owner tests → explicit approval → push + deploy**.

## 4. Data model (GeoDjango)

- **`Trail`** — slug, name, description (Markdown), `status` (existing /
  proposed / historic), region, tags, `geom` MultiLineString 4326,
  `source`, `visibility` (public / gated / private), owner.
- **`Track`** — an uploaded GPS trackline (GPX / KML / GeoJSON / CSV).
  `geom` LineStringZM (time in M), device, `taken_at`, optional `trail`,
  owner, visibility. Original file kept in R2, never re-encoded.
- **`Project`** — a trail design study: name, AOI polygon, chosen
  `DemSource`, collaborators, settings (sample spacing, thresholds, rubric).
- **`Alignment`** — one candidate line within a Project: name, priority,
  trailhead, `geom`, plus the uphill-direction flag (the evaluator
  normalizes direction into a *derived* geometry; it never rewrites the
  input, unlike `fix_direction_of_paths` today).
- **`DemSource`** — catalog entry: name, footprint, resolution, CRS,
  `cog_url`, `terrain_pmtiles_url`, `slope_pmtiles_url`, `gated`. Seeded
  from landslidescience's `catalog.geojson`; fallback rows for USGS 3DEP
  and AWS Terrarium.
- **`Tool`** — registry: slug, name, kind (`client` / `server` / `both`),
  manifest JSON, version, enabled, `engines`.
- **`Job`** — tool, inputs JSON, status, progress, outputs JSON, log,
  created_by, timestamps. Huey task id.
- **`Profile`** (derived) — for a Track or Alignment against a DemSource:
  arrays of distance, elevation, grade, slope, TSA, running averages, stored
  as a compact JSON/Parquet blob plus summary stats columns. Regenerated by
  the evaluator tool; cached so the D3 chart is instant.
- **`Page`** — copied from landslidescience `pages`, body Markdown.
- Photos: later, by lifting `inventory/photos.py`.

## 5. The tool framework (the open-source collaboration surface)

A **tool** is a directory (usually its own repo) with up to two halves:

```
trailgeek-tool-<slug>/
  tool.json           # manifest: slug, name, version, kind, entry, style,
                      #   inputs schema, outputs, engines, python: "pkg.module:Tool"
  web/                # optional: Vite+TS -> dist/index.js, dist/style.css
  py/                 # optional: pip package exposing a Tool subclass
  README.md, LICENSE, tests/
```

**JS side.** Tools receive an `app` object whose API is deliberately a
subset of GeoLibre's `GeoLibreAppAPI`, so the same tool can run inside
GeoLibre with a thin adapter and vice-versa:

```
activate(app) / deactivate(app)
app.getMap()                       // MapLibre instance
app.addGeoJsonLayer(name, fc)      // returns layerId
app.getSelected()                  // current Trail / Track / Alignment
app.onSelectionChange(cb)
app.sampleTerrain(coords, opts)    // client-side terrain-RGB decode (demshade)
app.readRasterWindow(demId, bbox)  // for in-browser numeric tools
app.registerPanel({id, title, mount})   // sidebar panel
app.registerControl(IControl, pos)
app.runJob(toolSlug, inputs) -> Promise<Job>   // server half, with progress
app.getState()/setState()          // URL-hash persistence
```

**Python side.** `trailgeek_analysis.tools.Tool` base class:

```python
class Tool:
    slug: str
    input_schema: dict     # JSON Schema; the site renders a form from it
    def run(self, inputs, ctx) -> dict   # ctx gives DEM access, progress(), storage
```

Registered via a Python entry point group `trailgeek.tools`, so `pip install
trailgeek-tool-snow` is all a collaborator needs on the worker image.
Outputs are typed (`geojson`, `raster_cog`, `table`, `profile`, `svg`) and
the portal knows how to display each type.

**Tool loading.** Same three paths as GeoLibre: bundled in the repo,
manifest URL, or drop-in folder under `/var/www/tools/<slug>/` (the GTA
convention). The GTA `{% tool "slug" %}` embed works on the same bundle.

**GeoLibre itself** is not the host framework (it is a React/Tauri app, not
designed as a library). It is used two ways: an **"Open in GeoLibre"**
button that hands the current layers over via `?data=` URLs, giving users
1,000+ WASM Whitebox tools for free; and, for tools that need raster
algebra in the browser, the same Whitebox WASM engine, if it is published
separately (to verify; otherwise tools call the server half).

**Reference tools shipped with the site** (each also a worked example for
contributors):

| Tool | Kind | Ported from |
|---|---|---|
| `profile` | client | `hig_svg` profiles + Ian's slope-segment SVGs → D3 |
| `evaluator` | server | `trail_evaluator.py` |
| `router-zax` | server (long job) | `trai_router.py` |
| `router-lcp` | server | new: least-cost path (`skimage.graph.MCP_Geometric`) using the same rubric tables, as a cross-check for Zax |
| `summits` | offline → layer | `DEM_topo` output |
| `soil-column` | client | `soil_model/web` |

## 6. The analysis package (`trailgeek_analysis`)

Pure Python, no Django import, pip-installable, pytest against the
Grewingk test rasters already in `raster_cruncher_p3/data/` (0.5 m lidar,
EPSG:26905). Modules:

- `sampling.py` — resample a line at fixed spacing in a local projected CRS
  (`shapely.interpolate`), sample DEM/slope with rasterio (bilinear, not
  nearest), read COGs over HTTP, tile-cache.
- `geometry.py` — grade, |grade|, **TSA** = acos(|grade|/slope), bearing,
  curvature (the disabled `add_curvature`), climb/descent.
- `smoothing.py` — running averages via `np.convolve` with proper null
  handling (today nulls are summed as 0).
- `stats.py` — percentiles, threshold bands, percent-of-length, **longest
  continuous run** per band, per-alignment summary table.
- `effort.py` — the construction / maintenance rubric (side-slope and grade
  bands → m/day), parameterized, defaults from `effort_rubric_settings`.
- `segments.py` — Ian's `simplify`-based constant-grade segmentation.
- `router/` — Zax walker with the stop-condition bug fixed (it measures from
  the start pixel, not the current location), numba-jitted inner loop,
  lookup tables from `trail_lookup_tables.py`, EPSG taken from the DEM;
  outputs traffic / up / down COGs.
- `io.py` — GPX/KML/GeoJSON/CSV readers and GPX/GeoJSON/CSV/SVG writers.

Everything the old scripts wrote to shapefiles becomes GeoJSON + Parquet;
everything they drew to SVG becomes a D3 spec the client renders.

## 7. Portal features

- **Map**: basemaps (`basemaps.js`), demshade hillshade/slope/aspect with
  sun controls, **3D terrain toggle** (demshade `raster-dem` from lidar
  PMTiles, 3DEP/Terrarium outside lidar footprints), sky/fog, wiper compare
  (later).
- **Trails and tracks** as live MVT from PostGIS, styled by status/owner;
  click → detail panel; URL hash holds view + selection (`ls_hash.js`).
- **Upload**: drag a GPX; it becomes a Track, gets an instant client-side
  profile from terrain tiles, and queues a server evaluation for the
  authoritative one.
- **Draw**: Terra Draw lines for new Alignments inside a Project; profile
  updates live while drawing (client sampling).
- **Profile (D3)**: distance × elevation, grade-class banding (±5/18/35/77
  defaults, editable), 10× and 1:1 panels, sibling alignments in grey,
  hover-linked cursor on the map, brush to select a segment and see its
  stats. Export SVG/PNG/CSV.
- **Compare**: side-by-side alignment table (length, climb, median/max grade,
  % in each band, longest steep run, construction/maintenance days).
- **3D**: fly-through along a line (MapLibre free camera +
  `queryTerrainElevation`), track replay by time, pitch/bearing presets,
  high-res export (`ls_export.js`). deck.gl only if very large point sets
  need it (demshade already coexists with it in GeoLibre).
- **Routing**: pick start/end on the map, choose rubric, run Zax / LCP as a
  job, view traffic raster as an overlay, promote a result to an Alignment.
- **Accounts**: `data_users`, `trail_editors`, `site_admins` groups via
  `init_groups`; gated DEMs and private projects.
- **Content**: Markdown pages, project write-ups, methods; `{% tool %}`-style
  embeds; cross-links to GTA articles and race pages.

## 8. Phases

**Phase 0 — Scaffold (1–2 weeks)**
Repo `hig314/trailgeek` (public). Django 5.2 + GeoDjango + PostGIS + Huey in
compose; Caddy; `.env.example`; `init_groups`; pages; analytics; CI running
pytest + a JS syntax check. New droplet, Cloudflare DNS, R2 bucket, first
deploy of a "hello map". Also create `hig-maplibre-kit` and move the shared
JS modules into it (landslidescience switches to the vendored build in the
same step, so there is one copy from day one).

**Phase 1 — Portal MVP (3–4 weeks)**
Models, admin, GPX upload, MVT trails, demshade + 3D, DEM catalog import
from landslidescience, client-side profile with D3, URL hash, detail panel.
Seed with the Grewingk / Ram Valley / Graduation Peak alignments.

**Phase 2 — Analysis engine (3–4 weeks)**
`trailgeek_analysis` with tests; evaluator job; Profile caching; compare
table; effort estimates; server-side authoritative profiles; exports.

**Phase 3 — Tool framework (2–3 weeks)**
Manifest + JS API + Python `Tool` + entry points; move profile and evaluator
onto it; `trailgeek-tool-template` cookiecutter; CONTRIBUTING.md; GeoLibre
adapter and "Open in GeoLibre"; embed a trailgeek tool in a GTA article to
prove the shared contract.

**Phase 4 — 3D and tracklines (2–3 weeks)**
Fly-through, replay, multi-track overlay, high-res export, wiper compare.

**Phase 5 — Routing (3 weeks, can overlap)**
Zax port with bug fix + numba; LCP tool; traffic COG overlay; promote-to-
alignment.

**Phase 6 — Collaborator tools and content (ongoing)**
Soil-column tool from `soil_model`; snow tool scaffold (collaborator);
summit layer from `DEM_topo`; photos; trail write-ups; public launch.

## 9. Repo layout

```
trailgeek/
  docker-compose.yml, docker-compose.override.yml, docker-compose.prod.yml
  Dockerfile, entrypoint.sh, Caddyfile, .env.example
  manage.py, requirements.txt
  trailgeek/            # settings, urls, analytics.py, rangeserve.py
  core/                 # Trail, Track, Project, Alignment, DemSource, MVT views
  jobs/                 # Job model, Huey tasks, tool registry + entry-point loader
  tools/                # bundled reference tools (profile, evaluator, ...)
  pages/, files/        # lifted from landslidescience
  static/js/            # site glue; vendors hig-maplibre-kit + demshade IIFEs
  packages/trailgeek_analysis/   # pip package, own tests (moves to its own repo when stable)
  ops/                  # provision.sh, backup, r2_sync.sh (from GTA / landslidescience)
  docs/                 # CLAUDE.md, ONBOARDING.md, HAZARDS.md, TOOLS.md, DATA.md
```

## 10. Open questions and assumptions

- **Droplet**: assumed new. If cost matters more than isolation, trailgeek
  can join the monitoring droplet's Caddy + Docker networks exactly like
  landslidescience, at the price of shared CPU with the worker.
- **Gated lidar across sites**: trailgeek's worker samples R2 with its own
  token; browser access to gated PMTiles from trailgeek needs either a
  shared auth cookie domain (not possible across .org domains) or trailgeek
  proxying through its own `rangeserve.py` with its own user gate. Assume
  the latter.
- **Whitebox WASM as a standalone package**: unverified. Not on the
  critical path; server-side tools cover the gap.
- **Seed data**: assumed the Trail_science volume alignments (Grewingk,
  Ram Valley, Graduation Peak) and the owner's GPS tracks.
- **Framework for tools**: assumed vanilla TS with a tiny API rather than
  React, so a collaborator can write a tool in one file. React tools still
  work, they just bundle React themselves.
