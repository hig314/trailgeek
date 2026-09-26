# trailgeek

Source for [trailgeek.org](https://trailgeek.org): a web map portal for
trails, GPS tracklines and trail design analysis, built on Django, PostGIS,
MapLibre GL JS and D3.

- [PLAN.md](PLAN.md): architecture and roadmap, with current status in §0
- [docs/OPERATIONS.md](docs/OPERATIONS.md): production, deploy, local dev
- [docs/SISTER_PROJECTS.md](docs/SISTER_PROJECTS.md): shared pieces from landslidescience.org and groundtruthalaska.org
- [docs/TRAIL_ANALYSIS.md](docs/TRAIL_ANALYSIS.md): the trail design algorithms being ported

## Run it locally

Needs Docker.

```bash
cp .env.example .env          # then set DJANGO_SECRET_KEY and POSTGRES_PASSWORD
docker compose up -d --build
docker compose exec web python manage.py createsuperuser
docker compose exec web python manage.py import_dem_catalog   # lidar catalogue from landslidescience
open http://localhost:8002/
```

Tests: `docker compose exec web python manage.py test`

## Layout

| Path | What |
|---|---|
| `trailgeek/` | settings, urls |
| `core/` | the map, trails, tracks, projects and alignments, the DEM catalogue, GPX upload, vector tiles, role groups, Huey tasks |
| `pages/` | Markdown pages edited in `/admin/` |
| `ops/` | droplet provisioning and deploy scripts |
| `docs/` | operations, sister projects, algorithm spec |

## Contributing

Work on a branch and open a pull request; CI runs the tests. Changes reach
production only after review, via `ops/deploy.sh`.

## License

MIT. Data layers carry their own licenses.
