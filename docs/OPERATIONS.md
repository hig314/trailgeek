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
- **No backups yet.** The database lives in the `pgdata` Docker volume on
  the droplet, and DigitalOcean droplet backups are off. It holds nothing
  irreplaceable yet, but must be sorted out before real trail data arrives:
  a nightly `pg_dump` to R2 or Spaces, following GTA's `ops/gta-backup`.
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
try to install PostgreSQL + PostGIS and GDAL locally with apt, with the same
environment variables as CI. That may or may not work in the sandbox.

Tests need `DJANGO_SECRET_KEY` set and a PostGIS database. The settings
switch static storage to plain `StaticFilesStorage` under `manage.py test`,
so no `collectstatic` is needed.
