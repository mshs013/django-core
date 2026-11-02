from django.apps import apps
from django.urls import include, path

urlpatterns = [path("", include("core.urls"))]
if apps.is_installed("core.seeder"):                                  # optional demo-data app (dev/staging only)
    urlpatterns.insert(0, path("seed/", include("core.seeder.urls")))

handler403 = "core.views.dashboard.error_403"
handler404 = "core.views.dashboard.error_404"
handler500 = "core.views.dashboard.error_500"
