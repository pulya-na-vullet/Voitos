from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path
from django.views.generic import RedirectView

urlpatterns = [
    path("", RedirectView.as_view(pattern_name="panel:users", permanent=False)),
    path("django-admin/", admin.site.urls),
    path("panel/", include(("panel.urls", "panel"), namespace="panel")),
    path("api/v1/", include(("api.urls", "api"), namespace="api")),
]

# Media/static: DEBUG или явные флаги (Waitress без nginx).
_serve_media = settings.DEBUG or getattr(settings, "SERVE_MEDIA", False)
_serve_static = settings.DEBUG or getattr(settings, "SERVE_STATIC", False)
if _serve_media:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
if _serve_static:
    # В DEBUG — исходники из STATICFILES_DIRS; иначе — collectstatic → STATIC_ROOT
    static_root = (
        settings.STATICFILES_DIRS[0]
        if settings.DEBUG and settings.STATICFILES_DIRS
        else settings.STATIC_ROOT
    )
    if static_root:
        urlpatterns += static(settings.STATIC_URL, document_root=static_root)
