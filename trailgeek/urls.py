from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path

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
    path("api/alignments/<int:pk>.geojson", core_views.api_alignment, name="api_alignment"),
    path("tracks/upload/", core_views.track_upload, name="track_upload"),
    path("tracks/<int:pk>/download/", core_views.track_download, name="track_download"),
    # Pages last: its <slug>/ route is a catch-all.
    path("", include("pages.urls")),
]
