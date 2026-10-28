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

## State of play (2026-10-26): the one dated section; update it, don't work around it

- **Phase 0 and Phase 1 are merged** (hig314/trailgeek#1, 2026-09-27; the
  owner reports it running). Phase 1 gave `DemSource` (seeded by
  `import_dem_catalog` from landslidescience's lidar catalogue), `Trail`,
  `Track`, `Project`, GPX upload, live MVT tiles, the home map with lidar
  shading / 3D through demshade, and the D3 profile.
- **Trail design is merged and deployed** (hig314/trailgeek#2, merged and
  deployed from the Mac 2026-09-27; migration 0003 applied in production).
  **The owner's trails, scouting plans and the Tutka Tusk project are
  loaded in production** (2026-10-16, see Open). The 3D views, the /lidar/ seam fixes
  and the code sharing below **landed on main through hig314/trailgeek#4**
  (merge 9aa2b6c, deployed from the Mac 2026-10-16); PR #3 is redundant
  and can be closed.
  - An **Alignment is a continuous line of ordered Legs**. Each leg is
    existing trail to follow, or a build effort (new construction, reroute,
    restoration) with an effort factor. `Alignment.geom` is derived from the
    legs; consecutive legs share their joining vertex (the API refuses gaps).
    Variants (`parent`) let a tweak be compared with the original.
  - **Evaluator**: the pure-numpy package `trailgeek_analysis/` (grade,
    geometric TSA, side-slope, null-aware running means, bands with correct
    longest runs, the construction / maintenance rubric per leg kind).
    Terrain sampling is `core/dem.py`: lidar COGs on R2 via GDAL /vsicurl/
    (GeoDjango's GDAL, no rasterio), then the baked 3DEP context (`ctx_3dep`,
    through the tile Worker's URLs when the catalogue has them), then
    Terrarium.
    Always in the Huey worker (`core/tasks.py`), for live edits and saved
    alignments alike.
  - **Editor** (`core/static/core/js/tg_editor.js`, `tg_design.js`): drag,
    insert and delete points, extend by drawing or by **following existing
    trails** (shortest path over the trail network, `core/network.py`),
    split legs, change kinds, undo; a live evaluation of every change shows
    as Saved / Now / Change tradeoffs and a profile with the saved version in
    grey. Project view is a sortable compare table.
  - **3D views** (map.js): pitch goes to 90; Top / Oblique / Side buttons
    and a "Side view" link per trail or alignment place the camera
    side-on, just clearing the terrain (MapLibre calculateCameraOptionsFromTo).
    Above 80 degrees the centre re-solving (MapLibre clamping and the
    terrain-centre tracker, a fixed copy of demshade's in `dem_stack.js`;
    see VENDOR.md) is switched off, and line widths are rescaled under
    pitch: that was the "trails go fat near horizontal" bug.
  - **/lidar/'s 3DEP seam fixes** (landslidescience 9a99a0f, 2026-09-28):
    lidar composites over the baked 3DEP context (`ctx_3dep`, imported by
    `import_dem_catalog` from the catalogue's `context` member) with
    `fillMode 'outside'` and the survey's footprint, so the context runs
    right up to the survey boundary instead of a 0 m shelf at tile edges;
    Terrarium beyond the bake; the 512 px terrain mesh; a camera-height
    re-solve threshold; sky set only on a change.
  - **Code sharing with landslidescience** (docs/SISTER_PROJECTS.md): the
    shared files are synced by `tools/sync_shared.py`, pinned in
    `tools/shared.json`, and CI fails if one is edited here. The DEM-stack
    logic that /lidar/ keeps inline is now one DOM-free module,
    `core/static/core/js/dem_stack.js`, proposed for landslidescience; the
    local session moves it there (handoff steps in SISTER_PROJECTS.md).
  - **Import** (`core/importers.py`): `manage.py import_lines FILE --as
    trails|alignments` and the `/import/` page for trail editors. The
    owner's `260927_Trails.zip` (443 trails) and `250924_Scouting_plans.gpkg`
    (71 alignments) import cleanly in the sandbox; **they are not in the
    repo** (the repo is public and scouting lines can cross private land):
    load them with the command or the page after deploying.
- **Field support for the Tutka Tusk scouting season** (hig314/trailgeek#4,
  built on the Mac 2026-10-16, **merged and deployed the same day**;
  migration 0004 applied in production):
  - **Projects nest** (`Project.parent`) and **tracks attach to projects**
    (`Track.project`, on the upload form, the track API and the project
    panel as "Scouting tracks"). PLAN.md §0 (2026-10-16) has the reasoning:
    segment (Trail), route (not built), project (tree).
  - **Field exports** (`core/exports.py`): `/api/alignments/<id>.gpx`,
    `/api/projects/<slug>.gpx` (a GPX track per leg, named for GaiaGPS,
    waypoints at joints and ends, priority colours),
    `/api/projects/<slug>.zip` (one GPX per alignment plus the combined
    file) and
    `/api/projects/<slug>.geojson` (legs, for MapApp vector MBTiles; the
    recipe is in docs/SISTER_PROJECTS.md). Links are in the alignment and
    project panels. The GPX writer lives in `core/gpx.py`.
  - **Importer** reads Class → priority (Preferred 1 / Alternate 2 / Future
    3) and Notes → notes. Project members now see their projects everywhere
    (`access.visible_projects_q`), not only their alignments.
  - **Public projects are shareable and findable** (hig314/trailgeek#6): the
    Layers panel has a Project picker listing every project the visitor may
    see (`/api/projects/` now returns all visible projects with `can_edit`
    each; a private parent's slug is withheld), a public project opens
    read-only for anyone with view, GPX, zip and GeoJSON, and the panel says
    so; editing still needs sign-in, the trail_editors role and membership.
  - **Shading selector** in the Layers panel: Hillshade, Hillshade + slope
    ramp, or **Preset** (mod 5 m bands + slope ramp, hillshade in hard
    light), the same numbers as landslidescience's `LIDAR_PRESETS.preset`
    so the word means the same view on both sites; kept in the URL hash as
    the `p` lidar code. Replaced the slope checkbox. Also: basemap changes
    keep the overlays (listen before `setStyle`), the shade sits under the
    trail layers, sliders show their values, exaggeration defaults to 1.0×.
  - **Password reset** on the sign-in page (`/accounts/password_reset/`,
    Django's views, templates in `templates/registration/`). Email settings
    come from env vars; with no `EMAIL_HOST` the link is printed to the web
    log. **Production sends through Brevo** (port 2525, live 2026-10-18;
    docs/OPERATIONS.md).
  - **Dev data (now also in production, see Open):** `backdoor-trail` programme project (private,
    its description lists the Backdoor Trail in order) with `tutka-tusk` as
    a child (**public**, owner's decision 2026-10-16; a public child never
    names a private parent): the 8 lines of `Tutka_Tusk_sections.gpkg` as 8 single-leg
    alignments, evaluated 100 % on the `kbay_2023` lidar. The first field
    files were exported from it (`261016_Tutka_Tusk_plan.gpx`,
    `261016_Tutka_Tusk_legs.geojson`, in the owner's Tutka GIS data
    folder). Still to do by hand: stitch the preferred pieces (Lagoon
    Bypass, Link A, the two Tutka Tusk pieces, Upper ascent A) into one
    alignment of legs in the editor, with Link B and Upper B as variants.
- **The URL carries the whole view** (hig314/trailgeek#8, merge 06e7b37,
  built, browser-tested and deployed from the Mac 2026-10-17): camera
  angle (bearing and pitch, so Top / Oblique / Side views survive a link),
  basemap (always written now, default included), the hand-picked survey,
  shading mode, hillshade opacity, 3D terrain and its exaggeration, and the
  selection. map.js keeps it as **one table, `VIEW_PARAMS`**, the shape of
  landslidescience map.js's `_VIEW_PARAMS`: a row per key with `collect` /
  `apply`, and the writer, the hashchange handler and the cold load are
  loops over it, so a hand-edited URL and back/forward work and a new piece
  of state is a new row, never a line in a writer the reader lacks. A
  project link with its own `map=` keeps that view (it used to refit).
  **The camera went into the shared grammar first** (landslidescience branch
  `hash-camera`, dd09d49: `map=zoom/lat/lon[/bearing/pitch]` in MapLibre's
  own hash order, written only when non-zero and read as 0 when absent;
  lat/lon precision by zoom, never under 4 dp; `tools/hash_codec_check.js`
  there is the old-vs-new parser check as a script), then synced here along
  with the current demshade build (3f7b140) and bridge. That commit is on
  landslidescience's `main` and deployed there (owner's approval, from the
  Mac 2026-10-17, by the procedure in its CLAUDE.md); `tools/shared.json`
  pins it, so no re-pin is needed.
  - **View links and navigation links** (owner, 2026-10-17): a hash with
    `map=` is a view and says everything; one without (`#project=…`,
    `#align=…`, the panels' links) is navigation and changes only the
    selection, keeping the viewer's basemap, shading, opacity, 3D and
    exaggeration (in the page: as they are; on a cold load: as this
    browser last had them, localStorage `tg_view_prefs`, per viewer only).
    The Layers panel's survey picker is labelled **DEM**: it is not all lidar.
  - **Hash keys trailgeek owns**, through the shared grammar's extras
    pass-through: `t3d` `ve` `sh` `op` `trail` `track` `align` `project` `solo`.
    landslidescience's own extras are listed in ls_hash.js's header
    (inventory `id ids tab an sc ty c f sa da vol yr n10 lw lv pm pr pw pve
    smp pp ax bx ls gr trk trg`, glaciers `site t`); never claim one of
    those, and add a new trailgeek key here as well as to `VIEW_PARAMS`.
  - **Dev gotcha, again:** the browser cached the old `ls_hash.js` and
    demshade build across the sync (the page URL's query string does not
    bust `/static/` caches in dev); the first test run showed no pitch and
    4 dp coordinates until a `fetch(url, {cache: 'reload'})` of each script.
    Production is unaffected (WhiteNoise's manifest storage hashes names).
- **Refine route tool, first draft** (hig314/trailgeek#10, merge 55b003c,
  deployed from the Mac 2026-10-17; migration 0005 applied in production). An alignment's panel has **Refine…**: pick a continuous
  stretch of legs, shape a cost curve and weight per factor, run; the worker
  reads the line as a sketch of a smooth path (± `input_sigma_m`, sketched
  corners allowed to round), anneals its own smooth curve (a planar B-spline
  starting from a smooth fit to the line) with a taut-string design grade
  line through a cut/fill tube, and the panel
  compares up to three candidates with the input scored the same way and
  saves one as a variant (`Alignment.refinement` keeps settings and seed).
  Defaults are per user (`UserSetting`, key `refine`). Design, decisions and
  what changed while building: docs/REFINE_TOOL.md. Migration 0005.
- **Project notes** (hig314/trailgeek#11, merge 3004234, deployed from the
  Mac 2026-10-17; migration 0006 applied in production): `ProjectNote`, a point with a note, per project.
  The open project's notes (or the open alignment's project's) show as amber
  points; click one to read, edit, move or delete it; "Add a note" in the
  panel, then click the map. Only those who can edit the project see them,
  so a public project's notes stay private, and they are not in the
  exports. `tg_notes.js`, `core/notes_views.py`.
- **Refine engine and methods sandbox: #25-#30 merged and deployed**
  (owner's approval 2026-10-26; main 551b21c, deployed from the Mac the
  same day; no migrations). #26 went in on its own and #28-#29 through
  #30's merge (GitHub marked them merged). What each brought:
  - **#25** `refine-route-starts` → main: route-finder starts at several
    turn weights; a route chain no longer dropped for a cusp.
  - **#26** `methods-sandbox` → #25: the methods-page sandbox on formula
    surfaces (docs/METHODS_SANDBOX.md). Its precomputed lines were scored
    the old way; rebuilt in #29. `.methods_cache/` is untracked; on
    branches without its ignore entry, add files by name, never
    `git add -A`.
  - **#27** `refine-route-corners` → #25: route-finder corners by a change of
    the downhill side (state = cell, heading, side bit) and a climb-height
    estimate for A*. Whole-trail search ~175 s → ~290–350 s, peak memory
    2.81 → 3.12 GB, 8 % of the route moved > 10 m. REFINE_TOOL.md §24.
  - **#28** `scoring-corner-sides` → #27: scoring reads the downhill side
    from the ground's slope over ±5 m (`side_grids`) against the line's
    heading, side minimum 4 m (was 10) measured by the length its samples
    stand for; `polyline_distance` by 32 m tiles (identical output; the
    whole-trail Problem build 123 s → 4.9 s). REFINE_TOOL.md §25.
    Graduation Peak Trail input: 24 → 47 corners; the new ones (pairs near
    2.1, 4.2, 4.4, 5.0 km, six near 7.0 km) were reviewed by the owner on
    the map on 2026-10-26: real, "a good detection technique".
  - **#29** `sandbox-rebuild` → #28: #26 merged in, the sandbox rebuilt
    on the new scoring (2026-10-26, 2.1 h): within 1 % of the reference or
    cheaper at 107 of 108 settings.
  - **Owner's decisions, 2026-10-26:** rebuild now (done, #29); the real
    patch (the Graduation Peak gully, **#30** `methods-terrain` → #29)
    varies grade and corners, like the formula surfaces, on two lines
    corner to corner: `gully-across` (8 %, holds its grade by weaving, no
    corners) and `gully-up` (23 %, up to 29 corners at grade weight 30),
    for the contrast. Lidar cached in `.methods_cache/terrain-gully.npz`,
    not committed. The new corners on the Graduation Peak Trail: judged
    real.
  - **Still open for the owner:** whether the sandbox's grade axis moves up
    (2–50: on both gully lines the corner weight only matters at grade
    weight 10 and 30); whether to try scoring every setting's line at every
    other setting to cure the search misses (METHODS_SANDBOX.md).
  - Possible later speed-up: a compiled route search (numba is blocked:
    it needs numpy ≤ 2.1, the container has 2.5.3).
- **Merged and deployed 2026-10-26 evening** (main 76fa1a5, from the Mac,
  owner's approval; no migrations; the owner is testing on the live site):
  - **#32** `click-chooser`: a click on overlapping lines lists them (name,
    kind, length, project) instead of picking one; KBSP's 15 km "Tutka
    Tusk" (alignment 65) under the Tutka Tusk sections took the click, and
    Refine ran on all of it ("too long to refine").
  - **#33** `refine-polish-dp`: "Your line, polished" (a chain from the line as
    drawn, small moves) and the corridor search (`refine/corridor.py`,
    dynamic programming over cross-sections, under grade caps, then
    polished), and the panel lists what it found but did not offer, and
    why. REFINE_TOOL.md §26. The methods sandbox predates it: rebuild it
    (~5 h on the Mac) to see the new chains at all 108 settings.
- **Open:** R2 CORS for trailgeek.org is **done on both hosts**, verified
  from the Mac 2026-09-27 with an Origin header: the `landslidescience-lidar`
  bucket (owner) and the lidar-tiles Worker on `tiles.landslidescience.org`
  (deployed from branch `lidar-tiles-cors-trailgeek`, Worker version
  9381e78d; hig314/landslidescience#3 brings `main` in line and awaits
  approval). The bucket list lacks `www.landslidescience.org`, which the
  Worker list has.
  **Production data loaded 2026-10-16** with the owner's explicit
  permission: the dev database's 443 trails (380 public, 63 gated), the
  gated KBSP project (71 alignments), the private `backdoor-trail`
  programme and the public `tutka-tusk` project (8 alignments, evaluated),
  moved by `dumpdata --natural-foreign` from dev and `loaddata` in the
  production web container, then `tiles.bump()`. Dev and production now
  hold the same rows; alignment pks match (Tutka Tusk is 72–79). The one
  dev track (Stan's Alpine Ridge scout) was not moved, since its original
  file is in dev media. `260927_Trails.zip` is not on the Mac's disks that
  the local session searched; `250924_Scouting_plans.gpkg` is on the
  Nunatak drive under `Trail_science/Graduation_Peak`.
  Until lidar reaches the evaluator, evaluations use the baked 3DEP
  (~10 m) or Terrarium (~60 m data in Alaska) and say so. No backups yet,
  and uploads now exist. Local session: adopt `dem_stack.js` in /lidar/
  and fix the three demshade issues in VENDOR.md.
- **Project sections** (branch `project-sections`, built and tried in dev
  2026-10-26, migration 0008: a new table and a nullable column, no existing
  row changed): `ProjectSection`, `Alignment.section`; the project panel is
  a tree (section → options → variants); "Add as an option…" on a trail's
  panel. Step 1 of 3 (PLAN.md §0, 2026-10-26).
- **Assemble a route** (branch `assemble-route`, stacked on
  `project-sections` because migration 0009 follows 0008; built and tried
  in dev 2026-10-26): "Assemble a route…" on the project panel picks one
  option (or variant) per section, joins them (`core/assemble.py`: end to
  end; a branch leaving the middle of the section before cuts it there;
  gaps up to 3 m snapped, longer bridged by new construction; directions by
  shortest joins, overridable), shows the joins, the line, a live profile
  and stats, and saves one ordinary alignment with `Alignment.assembly`
  (choices, directions, cuts, joins). "Change the choices…" re-assembles as
  a variant. The tree has a "Whole routes" group. Step 2 of 3; next,
  fine-tuning the joins. Dev: #90 "Tutka Tusk: whole route (Link B, Upper
  B)", 14.6 km, the bypass cut at 301 m. **Dev data:** Tutka Tusk has six sections (Lagoon
  Bypass; Link A/B; Tutka Tusk lower #79; Upper ascent A/B; Tutka Tusk upper
  #78; Backdoor loop connection), "Between" #81/#82 unsorted. Not yet in
  production: set them up there by hand, or ask the local session to.
- **Next up:** the `Route` model (an ordered chain of Trail segments; seed
  it with the Backdoor Trail from PLAN.md §0); check evaluations against
  real lidar once deployed; revive switchback / curvature detection; a
  routing tool (PLAN.md Phase 5) can reuse the leg model and the evaluator;
  the profile's 10×/1:1 panels.
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
- **demshade is vendored** in `core/static/core/vendor/`, synced from
  landslidescience's vendored copy. Its source repo is on the owner's Mac
  only, so a cloud session can't rebuild it (see `VENDOR.md`). The source map must stay next to the `.js`, or
  `collectstatic` fails under WhiteNoise's manifest storage.
- **One copy of every shared thing.** Before writing a map helper, check
  landslidescience (public repo) per docs/SISTER_PROJECTS.md. Files taken
  from there are listed in `tools/shared.json` and brought in with
  `python tools/sync_shared.py sync --repo PATH` (or `--github`), never
  edited here: CI's `sync_shared.py check` fails on a local edit. To change
  one, change it in landslidescience first.
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
| `tools/` | `sync_shared.py` + `shared.json`: the files shared with landslidescience, pinned and checked in CI |
| `docs/` | operations, sister projects, algorithm spec |
| `Caddyfile`, `docker-compose*.yml`, `Dockerfile`, `entrypoint.sh` | the stack |
