# trailgeek

Source for [trailgeek.org](https://trailgeek.org): a web map portal for
trails, GPS tracklines and trail design analysis, built on Django, PostGIS,
MapLibre GL JS and D3. See [PLAN.md](PLAN.md) for the roadmap.

## Run it locally

Needs Docker.

```bash
cp .env.example .env          # then set DJANGO_SECRET_KEY and POSTGRES_PASSWORD
docker compose up -d --build
docker compose exec web python manage.py createsuperuser
open http://localhost:8002/
```

Tests: `docker compose exec web python manage.py test`

## Layout

| Path | What |
|---|---|
| `trailgeek/` | settings, urls |
| `core/` | the map, trails and tracks (models arrive in Phase 1), role groups, Huey tasks |
| `pages/` | Markdown pages edited in `/admin/` |
| `ops/` | droplet provisioning and deploy scripts |

## License

MIT. Data layers carry their own licenses.
