# Operations

## Production

| | |
|---|---|
| URL | https://trailgeek.org (`www.` redirects there; http redirects to https) |
| Droplet | `trailgeek-web`, id 603685081, sfo3, s-2vcpu-4gb (2 vCPU / 4 GB), Ubuntu 24.04 |
| Reserved IP | **137.184.246.228** (the droplet's own IP is 137.184.224.12; use the reserved one) |
| Firewall | DigitalOcean cloud firewall `trailgeek-web-fw`, applied by tag `trailgeek`: inbound 22, 80, 443 and ICMP only |
| SSH | `root@137.184.246.228`, key only (the owner's `macbook` key), fail2ban on sshd |
| Repo on the droplet | `/opt/trailgeek` (clone of `main`); `.env` there, mode 600 |
| DNS | Cloudflare zone `trailgeek.org`: A records for apex and `www` to the reserved IP, **DNS only (grey cloud)** |
| TLS | Caddy with Let's Encrypt; certs persist in the `caddy_data` volume |
| Stack | `docker compose -f docker-compose.yml -f docker-compose.prod.yml`: caddy, web (gunicorn, 3 workers), worker (Huey), db (PostGIS 16), redis |
| Admin | https://trailgeek.org/admin/, superuser `hig` |
| First deployed | 2026-09-25 |

`ops/provision.sh` built the droplet. It installs Docker, 2 GB of swap,
capped container logs, key-only SSH, fail2ban, unattended upgrades and the
Anchorage timezone. It is idempotent: re-run it after changing it. The sshd
limits in it exist because a fresh public IP gets dozens of scanner
connections at once, which exhausts sshd's default pre-auth limit and
randomly drops real logins.

### Deploy

**Only after the owner has tested the change and explicitly approved it.**

```bash
ops/deploy.sh          # from a machine with the SSH key: pulls main, builds, up -d, check --deploy
```

Post-deploy steps by change (run on the droplet, `$C` as below):

| Change | Command |
|---|---|
| Trail design (legs, evaluator, import) | Migration 0003 runs on web start and gives every existing alignment one leg. **Restart the worker too** (`$C up -d` does): it now runs the evaluations. Then load the owner's data, either at https://trailgeek.org/import/ (trail editors; simplest from the Mac) or by copying the files to the droplet and running `$C exec web python manage.py import_lines /app/data/260927_Trails.zip --as trails --owner hig` and `$C exec web python manage.py import_lines /app/data/250924_Scouting_plans.gpkg --as alignments --project-name "Scouting plans 2025-09" --owner hig` (the compose stack mounts `./data` at `/app/data`). Each imported alignment queues an evaluation; the 88 km lines take ~30 s each on Terrarium. |
| Phase 1 (first deploy of the trail models) | `$C exec web python manage.py import_dem_catalog` to seed the DEM catalogue from landslidescience. Re-run whenever that catalogue is rebuilt. Then give yourself `trail_editors` in /admin/ to upload tracks. |

Deploying needs SSH access to the droplet, which only the owner's Mac has.
A cloud Claude Code session can't deploy; it can prepare the change and
say what the owner should run. A GitHub Actions deploy workflow (manual
trigger, SSH key stored as a repository secret) would remove that
dependency. It is an open item that needs the owner's decision.

Useful commands on the droplet:
```bash
cd /opt/trailgeek
C="docker compose -f docker-compose.yml -f docker-compose.prod.yml"
$C ps
$C logs -f web            # or worker, caddy, db
$C exec web python manage.py shell
$C exec web python manage.py createsuperuser
$C restart caddy          # retries certificate issuance
```

### Production `.env` keys
`DJANGO_SECRET_KEY`, `DJANGO_DEBUG=0`,
`DJANGO_ALLOWED_HOSTS=trailgeek.org,www.trailgeek.org`,
`DJANGO_CSRF_TRUSTED_ORIGINS=https://trailgeek.org,https://www.trailgeek.org`,
`POSTGRES_DB/USER/PASSWORD/HOST/PORT`, `REDIS_URL`, `SITE_DOMAIN`,
`ACME_EMAIL`. The secrets were generated on the droplet and exist nowhere
else. Changing `DJANGO_SECRET_KEY` logs everyone out.

### Known state and open items
- **R2 CORS for trailgeek.org (needed by Phase 1; confirmed 2026-09-26
  from localhost:8002: "No 'Access-Control-Allow-Origin' header").** The
  lidar archives are read by demshade in a Web Worker with `fetch`, which
  needs CORS from two places, both configured in landslidescience's
  Cloudflare account, so this is a local-session (Mac) job:
  1. **The R2 bucket** `landslidescience-lidar` (custom domain
     `lidar.landslidescience.org`): Cloudflare dashboard → R2 → the bucket
     → Settings → CORS policy. Add the trailgeek origins to
     `AllowedOrigins`, keeping the existing ones:
     ```json
     [{"AllowedOrigins": ["https://landslidescience.org", "https://www.landslidescience.org",
                          "http://localhost:8001",
                          "https://trailgeek.org", "https://www.trailgeek.org", "http://localhost:8002"],
       "AllowedMethods": ["GET", "HEAD"],
       "AllowedHeaders": ["Range", "If-None-Match", "If-Range"],
       "ExposeHeaders": ["ETag", "Content-Range", "Content-Length", "Accept-Ranges", "Last-Modified"],
       "MaxAgeSeconds": 86400}]
     ```
     The existing policy may already list more headers; add origins, do
     not remove anything. `Range` in AllowedHeaders and `Content-Range` in
     ExposeHeaders are what make ranged PMTiles reads work at all.
  2. **The tile Worker** `lidar-tiles` (`tiles.landslidescience.org`), used
     when a catalogue row has `tiles_url`: in the landslidescience repo,
     `workers/lidar-tiles/wrangler.toml`, append the same three origins to
     `ALLOWED_ORIGINS` (the comment there says it mirrors the bucket
     policy) and redeploy with `npx wrangler deploy` from that directory.
     That is a landslidescience change, so it goes through that repo's
     own branch-and-test rule.
  Until both are done the map logs one warning per survey
  (`lidar <id> is not readable from http://localhost:8002`), leaves the
  survey out of the Lidar picker and shades from Terrarium. Reload after
  the change; Cloudflare applies CORS policy edits within a minute.
- **Evaluations need outbound HTTPS from the worker** to
  `lidar.landslidescience.org` (COG byte ranges) and
  `s3.amazonaws.com` (Terrarium tiles). The droplet firewall is inbound
  only, so this works; a DEM that cannot be read is skipped with a warning
  in the result, and the worker log has the GDAL error.
- **No backups yet.** The database lives in the `pgdata` Docker volume on
  the droplet, and DigitalOcean droplet backups are off. Phase 1 adds
  uploaded GPX originals under `/opt/trailgeek/data/media/` (bind-mounted
  `./data`), so a backup now needs both `pg_dump` and that directory:
  nightly to R2 or Spaces, following GTA's `ops/gta-backup`. The
  `trailgeek-data` R2 bucket from PLAN.md §3 is not created yet; uploads go
  to local disk until it is.
- `manage.py check --deploy` gives two warnings on purpose. W008 (no
  SSL redirect) is handled by Caddy. W004 (HSTS) is off until the site is
  settled, because browsers cache HSTS and it is hard to undo.
- No uptime check yet. landslidescience and GTA use DigitalOcean uptime
  checks; add one for `https://trailgeek.org/healthz`.
- No analytics yet. Umami would be copied from landslidescience.
- Cloudflare proxying (orange cloud) is off. Turning it on needs SSL mode
  "Full (strict)" and a check that Caddy still renews certificates.
- The owner's Cloudflare API token (`~/.cloudflare.env` on the Mac) can read
  the zone but can't edit DNS.

## Local development

```bash
cp .env.example .env    # set DJANGO_SECRET_KEY and POSTGRES_PASSWORD
docker compose up -d --build
docker compose exec web python manage.py test
open http://localhost:8002
```

- Port 8002, because 8000 is the owner's Tethys stack and 8001 is
  landslidescience.
- The override file bind-mounts the source and runs `runserver`, so code
  changes reload without a rebuild. A change to `requirements.txt` or the
  `Dockerfile` needs `docker compose build`.
- Only the web container migrates; the worker sets `SKIP_MIGRATE=1`.
- The owner's Mac is short of disk space. Docker Desktop crashed on
  2026-09-25 when the disk filled.

## Testing without Docker (cloud sessions, CI)

CI (`.github/workflows/ci.yml`) runs on every push to any branch. It
installs `gdal-bin` on Ubuntu 24.04 and starts a `postgis/postgis:16-3.4`
service, then runs `makemigrations --check`, the Django tests, and
`node --check` on the site JS. A cloud session can push a branch and read
the CI result with `gh run list` / `gh run view --log-failed`. It can also
install PostgreSQL + PostGIS and GDAL locally with apt, with the same
environment variables as CI. This worked on 2026-09-26:

```bash
apt-get update && apt-get install -y postgresql-16-postgis-3 gdal-bin
service postgresql start
su postgres -c "psql -c \"CREATE USER trailgeek WITH SUPERUSER PASSWORD 'ci';\""
su postgres -c "psql -c 'CREATE DATABASE trailgeek OWNER trailgeek;'"
su postgres -c "psql -d trailgeek -c 'CREATE EXTENSION postgis;'"
export DJANGO_SECRET_KEY=ci DJANGO_DEBUG=1 POSTGRES_HOST=localhost POSTGRES_PASSWORD=ci
python manage.py test
```

For the live editor the worker must run too: `apt-get install
redis-server`, `redis-server --daemonize yes`, export
`REDIS_URL=redis://localhost:6379/0`, and start `python manage.py run_huey`
beside `runserver`. `HUEY_IMMEDIATE=1` instead runs tasks inline in the web
process (no Redis), which is enough for a quick look.

The sandbox blocks unpkg.com and landslidescience.org but not the AWS
terrain tiles or the npm registry, so a browser smoke test of the map is
possible with Playwright's bundled Chromium by serving MapLibre and D3 from
`npm pack` copies and stubbing basemap tiles (done for Phase 1).

Tests need `DJANGO_SECRET_KEY` set and a PostGIS database. The settings
switch static storage to plain `StaticFilesStorage` under `manage.py test`,
so no `collectstatic` is needed.
