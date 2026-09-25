#!/bin/sh
set -e
mkdir -p /app/data/media
# Only the web container migrates; the worker sets SKIP_MIGRATE=1 so two
# containers never race on the same migration.
if [ -z "$SKIP_MIGRATE" ]; then
  python manage.py migrate --noinput
  python manage.py init_groups
fi
exec "$@"
