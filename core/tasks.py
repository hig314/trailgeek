"""Huey tasks: everything that reads rasters runs here, in the worker,
never in a web request.

    evaluate_live(payload)     the editor's live evaluation; result kept in
                               Huey's result store for the browser to poll
    evaluate_saved(pk)         evaluation of a saved alignment, stored on it

`ping` proves the worker is wired up:
    docker compose exec web python manage.py shell -c "from core.tasks import ping; print(ping()(blocking=True, timeout=10))"
"""
import logging

from huey.contrib.djhuey import task

log = logging.getLogger(__name__)


@task()
def ping():
    return "pong"


@task()
def evaluate_live(payload):
    from . import evaluation
    from .models import Project
    project = Project.objects.filter(pk=payload.get("project_id")).first()
    settings = project.effective_settings() if project else None
    try:
        result = evaluation.run(payload["legs"], settings, project, live=True)
        result["headline"] = evaluation.headline(result)
        return {"ok": True, "result": result, "seq": payload.get("seq")}
    except Exception as e:
        log.exception("live evaluation failed")
        return {"ok": False, "error": str(e), "seq": payload.get("seq")}


@task()
def evaluate_saved(alignment_id):
    from . import evaluation
    from .models import Alignment
    a = Alignment.objects.select_related("project").filter(pk=alignment_id).first()
    if a is None or not a.legs.exists():
        return None
    evaluation.evaluate_alignment(a)
    return alignment_id
