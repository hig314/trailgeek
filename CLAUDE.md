# CLAUDE.md — trailgeek.org

A trails-focused web map portal: trails, GPS tracklines and trail design
analysis on lidar terrain, with 3D views, D3 profiles, and a pluggable tool
framework that colleagues (snow, soil water) can contribute to. The owner is
a GIS-literate geoscientist, not a professional web developer, so explain
server and Django reasoning rather than assume it.

| Read | For |
|---|---|
| [PLAN.md](PLAN.md) | Architecture, data model, tool framework, phases. **§0 has the current status.** |
| [docs/OPERATIONS.md](docs/OPERATIONS.md) | Production droplet, DNS, deploy, local dev, testing without Docker, open ops items |
| [docs/SISTER_PROJECTS.md](docs/SISTER_PROJECTS.md) | What to reuse from landslidescience, GTA, demshade, and the public lidar catalogue |
| [docs/TRAIL_ANALYSIS.md](docs/TRAIL_ANALYSIS.md) | Spec of the evaluator, profile and router algorithms to port, with their known bugs |

## State of play (2026-09-26): the one dated section; update it, don't work around it

- **Phase 0 is done and live** at https://trailgeek.org (first deploy
  2026-09-25): Django 5.2 + GeoDjango + PostGIS + Huey + Caddy, Markdown
  pages in `/admin/`, role groups, `/healthz`, CI.
- **Phase 1 portal MVP is built on branch `claude/trailgeek-project-b53hqm`
  (PR open, awaiting the owner's test and approval; not deployed).** It adds:
  - Models in `core/models.py`: `DemSource`, `Trail`, `Track`, `Project`,
    `Alignment` (PLAN.md §4), with GeoDjango admin.
  - `manage.py import_dem_catalog`: seeds `DemSource` from
    landslidescience's public lidar catalogue plus two context DEMs
    (AWS Terrarium, USGS 3DEP). **Run it after deploying**; the map falls
    back to Terrarium until then.
  - GPX upload at `/tracks/upload/` (role `trail_editors`), original file
    kept under `data/media/`.
  - Live vector tiles `/tiles/trails/{z}/{x}/{y}.mvt` (ST_AsMVT, three
    layers, visibility-filtered, 60 s cache) and GeoJSON detail endpoints
    under `/api/`.
  - Home map (`core/static/core/js/map.js`): basemap picker, lidar
    hillshade/slope + 3D from the catalogue through demshade, trails and
    tracks, detail panel, D3 profile (`tg_profile.js`) from GPS elevations
    or from Terrarium tiles (`tg_sample.js`), URL hash state.
  - Copied verbatim from landslidescience with source headers:
    `basemaps.js`, `ls_hash.js`, `dem_shade_bridge.js`.
- **Open before Phase 1 counts as done:** the owner must add
  `https://trailgeek.org` to the `landslidescience-lidar` R2 bucket's CORS
  allowlist (and the lidar tile Worker) or lidar shading and 3D will not
  load from this origin (the map then falls back to Terrarium and says so
  in the console). Seed alignments (Grewingk, Ram Valley, Graduation Peak)
  are on the owner's external drives and are not loaded yet.
- **Next up:** Phase 2, the `trailgeek_analysis` package and the evaluator
  job, which replaces the coarse client profile with a lidar one. Also
  Terra Draw for alignments, KML/GeoJSON upload, and the profile's dual
  10×/1:1 panels.
- **Deliberately deferred:** extracting landslidescience's shared JS into
  `hig-maplibre-kit` (it touches landslidescience; do it as a separate
  reviewed change). Backups, uptime check and analytics are open items in
  OPERATIONS.md. Backups matter more now that uploads exist.

## Workflow: build → owner tests → owner approves → merge + deploy

This is load-bearing, carried over from landslidescience, where it was
earned by incidents. Production is public.

- **Work on a branch and open a PR.** Never push directly to `main`: `main`
  is what production runs, and `ops/deploy.sh` deploys `origin/main`.
- CI runs on every push. Read its result (`gh run list`, `gh run view
  --log-failed`) before asking for review.
- The owner tests the branch, either locally (`git checkout <branch> &&
  docker compose up -d`) or by reading the PR, and says whether it is
  approved. **Only after explicit approval** is the PR merged and deployed.
- **Deploying needs the owner's Mac**, which is the only machine with the
  droplet's SSH key: `ops/deploy.sh`. A cloud session can't deploy. It should
  finish the PR and tell the owner what to run, including any
  post-deploy steps such as a management command.
- Migrations: commit them with the change. `makemigrations --check` in CI
  fails if one is missing.

## Stack and conventions

- Python 3.12, Django 5.2, GeoDjango on **PostGIS 16** in its own container.
  Trail data is this site's own, so it uses **real ORM models** (unlike
  landslidescience, which reads another stack's DB by raw SQL).
- **Huey + Redis** for anything slow (raster sampling, the evaluator, the
  router): the `worker` service, same image. Never do raster work in a
  request.
- Frontend: **MapLibre GL JS 5.24** from unpkg, vanilla JS in IIFEs, **no
  build step** for the site. D3 v7 for charts. Tool bundles (Phase 3) may use
  Vite + TypeScript.
- **demshade is vendored** in `core/static/core/vendor/`. Its source repo is
  on the owner's Mac only, so a cloud session can't rebuild it (see
  `VENDOR.md`). The source map must stay next to the `.js`, or
  `collectstatic` fails under WhiteNoise's manifest storage.
- **One copy of every shared thing.** Before writing a map helper, check
  landslidescience (public repo) per docs/SISTER_PROJECTS.md. When copying,
  add a header comment naming the source file.
- Role groups live in `core/roles.py`; `init_groups` runs on every web
  container start. Only the web container migrates (`SKIP_MIGRATE=1` on the
  worker).
- Settings come from env vars only (`.env.example`). There is one settings
  file.
- `window.tgMap` is the home map, for console debugging.
- A cloud session can run the tests without Docker: `apt-get install
  postgresql-16-postgis-3 gdal-bin`, start PostgreSQL, create the `trailgeek`
  database with the postgis extension, and export the CI env vars
  (docs/OPERATIONS.md). This worked on 2026-09-26.
- Local dev is on port **8002** (8000 = Tethys, 8001 = landslidescience).

## Layout

| Path | What |
|---|---|
| `trailgeek/` | settings, urls, wsgi |
| `core/` | models, admin, `/api/` and `/tiles/` views, GPX upload, home map JS, role groups + `init_groups`, Huey tasks, `import_dem_catalog` |
| `pages/` | `Page` model (Markdown), served at `/<slug>/` (catch-all, so it is routed last) |
| `templates/` | `base.html`, login |
| `ops/` | `provision.sh` (droplet setup, idempotent), `deploy.sh` |
| `docs/` | operations, sister projects, algorithm spec |
| `Caddyfile`, `docker-compose*.yml`, `Dockerfile`, `entrypoint.sh` | the stack |
