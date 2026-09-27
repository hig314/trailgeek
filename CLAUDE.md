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

## State of play (2026-09-27): the one dated section; update it, don't work around it

- **Phase 0 and Phase 1 are merged** (hig314/trailgeek#1, 2026-09-27; the
  owner reports it running). Phase 1 gave `DemSource` (seeded by
  `import_dem_catalog` from landslidescience's lidar catalogue), `Trail`,
  `Track`, `Project`, GPX upload, live MVT tiles, the home map with lidar
  shading / 3D through demshade, and the D3 profile. Basemaps, the hash
  grammar and the demshade bridge are copied verbatim from landslidescience.
- **Trail design (this branch, `claude/trailgeek-project-b53hqm`, PR open,
  awaiting the owner's local test and approval):**
  - An **Alignment is a continuous line of ordered Legs**. Each leg is
    existing trail to follow, or a build effort (new construction, reroute,
    restoration) with an effort factor. `Alignment.geom` is derived from the
    legs; consecutive legs share their joining vertex (the API refuses gaps).
    Variants (`parent`) let a tweak be compared with the original.
  - **Evaluator**: the pure-numpy package `trailgeek_analysis/` (grade,
    geometric TSA, side-slope, null-aware running means, bands with correct
    longest runs, the construction / maintenance rubric per leg kind).
    Terrain sampling is `core/dem.py`: lidar COGs on R2 via GDAL /vsicurl/
    (GeoDjango's GDAL, no rasterio), then Terrarium where there is no lidar.
    Always in the Huey worker (`core/tasks.py`), for live edits and saved
    alignments alike.
  - **Editor** (`core/static/core/js/tg_editor.js`, `tg_design.js`): drag,
    insert and delete points, extend by drawing or by **following existing
    trails** (shortest path over the trail network, `core/network.py`),
    split legs, change kinds, undo; a live evaluation of every change shows
    as Saved / Now / Change tradeoffs and a profile with the saved version in
    grey. Project view is a sortable compare table.
  - **Import** (`core/importers.py`): `manage.py import_lines FILE --as
    trails|alignments` and the `/import/` page for trail editors. The
    owner's `260927_Trails.zip` (443 trails) and `250924_Scouting_plans.gpkg`
    (71 alignments) import cleanly in the sandbox; **they are not in the
    repo** (the repo is public and scouting lines can cross private land):
    load them with the command or the page after deploying.
- **Open:** the R2 CORS change for trailgeek.org (bucket done by the owner;
  the lidar-tiles Worker origin change in landslidescience still to do). Until
  lidar reaches the worker, evaluations use Terrarium (~60 m data in Alaska)
  and say so. No backups yet, and uploads now exist.
- **Next up:** check evaluations against real lidar once deployed; revive
  switchback / curvature detection; a routing tool (PLAN.md Phase 5) can
  reuse the leg model and the evaluator; the profile's 10×/1:1 panels.
- **Deliberately deferred:** extracting landslidescience's shared JS into
  `hig-maplibre-kit` (a separate reviewed change). Uptime check and
  analytics are open items in OPERATIONS.md.

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
- **Early-stage relaxation (decided 2026-09-26, until real trail data and
  users arrive):** approval by reading the PR is enough when CI is green
  and the cloud session ran a browser smoke test of the map. A local Docker
  test is still required for a migration that alters existing rows, any
  change to `Dockerfile`, `entrypoint.sh`, compose, Caddy or `ops/`, and
  anything touching authentication or uploads. Approval stays explicit.
- **Deploying needs the owner's Mac**, which is the only machine with the
  droplet's SSH key: `ops/deploy.sh`. A cloud session can't deploy. It should
  finish the PR and tell the owner what to run, including any
  post-deploy steps such as a management command.
- Migrations: commit them with the change. `makemigrations --check` in CI
  fails if one is missing.

## Two Claude Code instances: cloud builds, local runs the Mac-only steps

The owner runs a cloud session (claude.ai/code) and a local one (Claude
Code CLI on the Mac). Each has a lane, set by what only the Mac can do.

| | Cloud session | Local session (Mac) |
|---|---|---|
| Does | Build features on a branch; run the Django tests on its own PostGIS and a Playwright smoke test; open and drive the PR; keep docs current | Run the Docker stack and test a branch; deploy (`ops/deploy.sh`) and post-deploy commands; rebuild vendored demshade; load data from the external drives; Cloudflare / R2 / DNS settings |
| Can't | Reach landslidescience.org or the R2 archives; run Docker; deploy | Nothing in principle, but it has no CI feedback loop of its own beyond pushing |
| Writes | Code, tests, migrations, docs, the PR description | Ops changes, data loads, `VENDOR.md` updates, test results |

**Handoff, cloud → local:** the PR description carries a "For the owner to
test" section listing exact commands and post-deploy steps. The dated
state-of-play above says what is built and what is open. The local session
starts by reading both, then `git fetch origin && git checkout <branch>`.
The first check of any local run is `git branch --show-current`: the dev
compose file bind-mounts the source, so a wrong branch shows up as
"Unknown command" or a missing page, not as an error about branches.

**Handoff, local → cloud:** test results, deploy outcome and anything
learned on the Mac go into a PR comment (or, after merge, into the
state-of-play and docs/OPERATIONS.md), committed and pushed. The cloud
session reads that before continuing. Never leave a fact only in a chat
window; a cloud session cannot see the local one and vice versa.

**Shared rules for both:** branch + PR, never push to `main`; update the
state-of-play rather than working around it; one copy of every shared
thing. When both are active on the same branch, pull before editing and
keep commits small so the other side can rebase without conflict.

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
- `HUEY_IMMEDIATE=1` runs tasks inline (no Redis, no worker) for a quick
  look without the compose stack; tests set it automatically.
- A cloud session can run the tests without Docker: `apt-get install
  postgresql-16-postgis-3 gdal-bin`, start PostgreSQL, create the `trailgeek`
  database with the postgis extension, and export the CI env vars
  (docs/OPERATIONS.md). This worked on 2026-09-26. For the live editor,
  also `apt-get install redis-server`, `redis-server --daemonize yes`,
  `REDIS_URL=redis://localhost:6379/0` and `manage.py run_huey` (2026-09-27).
- Local dev is on port **8002** (8000 = Tethys, 8001 = landslidescience).

## Layout

| Path | What |
|---|---|
| `trailgeek/` | settings, urls, wsgi |
| `core/` | models, admin, `/api/` and `/tiles/` views, GPX upload, importers, DEM sampling (`dem.py`), evaluation glue, trail-network routing, home map and editor JS, role groups + `init_groups`, Huey tasks |
| `trailgeek_analysis/` | the evaluator: pure Python + numpy, no Django, own tests |
| `pages/` | `Page` model (Markdown), served at `/<slug>/` (catch-all, so it is routed last) |
| `templates/` | `base.html`, login |
| `ops/` | `provision.sh` (droplet setup, idempotent), `deploy.sh` |
| `docs/` | operations, sister projects, algorithm spec |
| `Caddyfile`, `docker-compose*.yml`, `Dockerfile`, `entrypoint.sh` | the stack |
