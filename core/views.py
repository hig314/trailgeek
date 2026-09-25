from django.db import connection
from django.http import JsonResponse
from django.shortcuts import render


def home(request):
    return render(request, "core/home.html")


def healthz(request):
    """Liveness plus a database round-trip, for the uptime check and deploys."""
    with connection.cursor() as cur:
        cur.execute("SELECT PostGIS_Lib_Version()")
        postgis = cur.fetchone()[0]
    return JsonResponse({"ok": True, "postgis": postgis})
