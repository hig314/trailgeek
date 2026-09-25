#!/bin/bash
# Deploy the current origin/main to production. Run from your workstation
# ONLY after the change has been tested in local dev and approved.
#   ops/deploy.sh
set -euo pipefail
HOST=${TRAILGEEK_HOST:-root@trailgeek.org}
ssh "$HOST" 'set -e
  cd /opt/trailgeek
  git pull --ff-only
  C="docker compose -f docker-compose.yml -f docker-compose.prod.yml"
  $C build
  $C up -d --remove-orphans
  $C exec -T web python manage.py check --deploy --fail-level ERROR
  sleep 2
  curl -fsS -H "Host: trailgeek.org" -H "X-Forwarded-Proto: https" http://localhost/healthz >/dev/null 2>&1 \
    || $C exec -T web python -c "import urllib.request;print(urllib.request.urlopen(\"http://localhost:8000/healthz\").read())"
  $C ps'
