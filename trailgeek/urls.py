from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path

from core import design_views as design
from core import tiles as core_tiles
from core import views as core_views

urlpatterns = [
    path("", core_views.home, name="home"),
    path("healthz", core_views.healthz, name="healthz"),
    path("admin/", admin.site.urls),
    path("accounts/login/", auth_views.LoginView.as_view(), name="login"),
    path("accounts/logout/", auth_views.LogoutView.as_view(), name="logout"),
    # Map data. Vector tiles are live from PostGIS; the GeoJSON endpoints
    # give one feature with its full (3D) geometry for the detail panel.
    path("tiles/trails/<int:z>/<int:x>/<int:y>.mvt", core_tiles.trails_mvt, name="trails_mvt"),
    path("api/dems.geojson", core_views.api_dems, name="api_dems"),
    path("api/trails/<slug:slug>.geojson", core_views.api_trail, name="api_trail"),
    path("api/tracks/<int:pk>.geojson", core_views.api_track, name="api_track"),
    # Trail design: alignments made of legs, evaluations, following trails.
    path("api/alignments/", design.api_alignment_create, name="api_alignment_create"),
    path("api/alignments/<int:pk>.geojson", design.api_alignment, name="api_alignment"),
    path("api/alignments/<int:pk>/save", design.api_alignment_save, name="api_alignment_save"),
    path("api/alignments/<int:pk>/duplicate", design.api_alignment_duplicate, name="api_alignment_duplicate"),
    path("api/alignments/<int:pk>/delete", design.api_alignment_delete, name="api_alignment_delete"),
    path("api/alignments/<int:pk>/evaluate", design.api_alignment_evaluate, name="api_alignment_evaluate"),
    path("api/evaluate/", design.api_evaluate, name="api_evaluate"),
    path("api/evaluate/<str:task_id>/", design.api_evaluate_result, name="api_evaluate_result"),
    path("api/route/trails", design.api_trail_route, name="api_trail_route"),
    path("api/projects/", design.api_projects, name="api_projects"),
    path("api/projects/<slug:slug>.json", design.api_project, name="api_project"),
    path("import/", design.import_lines, name="import_lines"),
    path("tracks/upload/", core_views.track_upload, name="track_upload"),
    path("tracks/<int:pk>/download/", core_views.track_download, name="track_download"),
    # Pages last: its <slug>/ route is a catch-all.
    path("", include("pages.urls")),
]
