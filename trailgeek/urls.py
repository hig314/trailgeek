from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path

from core import design_views as design
from core import exports as core_exports
from core import notes_views as notes
from core import water
from core import refine_views
from core import tiles as core_tiles
from core import views as core_views

urlpatterns = [
    path("", core_views.home, name="home"),
    path("healthz", core_views.healthz, name="healthz"),
    path("admin/", admin.site.urls),
    path("accounts/login/", auth_views.LoginView.as_view(), name="login"),
    path("accounts/logout/", auth_views.LogoutView.as_view(), name="logout"),
    # Password reset by email (templates/registration/password_reset_*.html).
    path("accounts/password_reset/", auth_views.PasswordResetView.as_view(
        email_template_name="registration/password_reset_email.txt",
        subject_template_name="registration/password_reset_subject.txt"), name="password_reset"),
    path("accounts/password_reset/done/", auth_views.PasswordResetDoneView.as_view(), name="password_reset_done"),
    path("accounts/reset/<uidb64>/<token>/", auth_views.PasswordResetConfirmView.as_view(), name="password_reset_confirm"),
    path("accounts/reset/done/", auth_views.PasswordResetCompleteView.as_view(), name="password_reset_complete"),
    # Map data. Vector tiles are live from PostGIS; the GeoJSON endpoints
    # give one feature with its full (3D) geometry for the detail panel.
    path("tiles/trails/<int:z>/<int:x>/<int:y>.mvt", core_tiles.trails_mvt, name="trails_mvt"),
    path("api/dems.geojson", core_views.api_dems, name="api_dems"),
    path("api/trails/<slug:slug>.geojson", core_views.api_trail, name="api_trail"),
    path("api/trails/<slug:slug>/study", design.api_trail_study, name="api_trail_study"),
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
    path("api/alignments/<int:pk>/refine", refine_views.api_alignment_refine, name="api_alignment_refine"),
    path("api/alignments/<int:pk>/refine/accept", refine_views.api_alignment_refine_accept,
         name="api_alignment_refine_accept"),
    path("api/twi/", water.api_twi, name="api_twi"),
    path("api/twi/jobs/<str:job>/", water.api_twi_job, name="api_twi_job"),
    path("api/refine/settings", refine_views.api_refine_settings, name="api_refine_settings"),
    path("api/factors/", refine_views.api_factors, name="api_factors"),
    path("api/refine/jobs/<str:job>/", refine_views.api_refine_job, name="api_refine_job"),
    path("api/projects/", design.api_projects, name="api_projects"),
    path("api/projects/<slug:slug>.json", design.api_project, name="api_project"),
    path("api/projects/<slug:slug>/notes.geojson", notes.api_project_notes, name="api_project_notes"),
    path("api/projects/<slug:slug>/notes/new", notes.api_project_note_create, name="api_project_note_create"),
    path("api/notes/<int:pk>", notes.api_note_update, name="api_note_update"),
    path("api/notes/<int:pk>/delete", notes.api_note_delete, name="api_note_delete"),
    # Field exports: GPX for GaiaGPS / phones, GeoJSON of the legs for offline map products.
    path("api/alignments/<int:pk>.gpx", core_exports.api_alignment_gpx, name="api_alignment_gpx"),
    path("api/projects/<slug:slug>.gpx", core_exports.api_project_gpx, name="api_project_gpx"),
    path("api/projects/<slug:slug>.zip", core_exports.api_project_zip, name="api_project_zip"),
    path("api/projects/<slug:slug>.geojson", core_exports.api_project_geojson, name="api_project_geojson"),
    path("import/", design.import_lines, name="import_lines"),
    path("tracks/upload/", core_views.track_upload, name="track_upload"),
    path("tracks/<int:pk>/download/", core_views.track_download, name="track_download"),
    # Pages last: its <slug>/ route is a catch-all.
    path("", include("pages.urls")),
]
