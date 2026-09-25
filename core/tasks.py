"""Huey tasks. Phase 0 has only a ping, used to prove the worker is wired up:
    docker compose exec web python manage.py shell -c "from core.tasks import ping; print(ping()(blocking=True, timeout=10))"
"""
from huey.contrib.djhuey import task


@task()
def ping():
    return "pong"
