# CLAUDE.md — trailgeek.org

Trails-focused map portal. Roadmap in [PLAN.md](PLAN.md). Sister sites:
landslidescience.org (`../landslidescience`, the pattern this repo copies)
and groundtruthalaska.org (`../GTA`). Shared browser code comes from
`../maplibre-gl-demshade` (vendored IIFE, see `core/static/core/vendor/VENDOR.md`).

## Workflow — dev → test → (revise → test) → GitHub + production

Never push to GitHub or deploy until the owner has tested in local dev and
explicitly approved. Local commits are fine; the push is the sync point with
production. Deploy is `ops/deploy.sh`.

## Environments

| | Dev | Prod |
|---|---|---|
| Where | this machine, `docker compose up -d` | droplet `trailgeek-web`, `/opt/trailgeek` |
| URL | http://localhost:8002 (8000 = Tethys, 8001 = landslidescience) | https://trailgeek.org |
| Compose | `docker-compose.yml` + `override` (auto) | `docker-compose.yml` + `docker-compose.prod.yml` |
| TLS | none | Caddy, Let's Encrypt |

Production host: droplet `trailgeek-web` (id 603685081, sfo3, s-2vcpu-4gb,
Ubuntu 24.04), reserved IP **137.184.246.228**, cloud firewall
`trailgeek-web-fw` (22/80/443 only), root SSH with the `macbook` key.
DNS is Cloudflare; the zone is `trailgeek.org`.

## Stack

Python 3.12, Django 5.2, GeoDjango on PostGIS 16 (own container, named
volume `pgdata`), Huey on Redis for long jobs (`worker` service, same image),
gunicorn + WhiteNoise, Caddy in prod. Frontend: MapLibre 5 from unpkg,
vanilla JS, no build step. `data/` is gitignored and volume-mounted.

## Conventions

- One copy of every shared thing. Before writing a map helper, check
  landslidescience's `inventory/static/inventory/js/` and demshade.
- Role groups live in `core/roles.py`; `init_groups` runs on every web start.
- Only the web container migrates (`SKIP_MIGRATE=1` on the worker).
