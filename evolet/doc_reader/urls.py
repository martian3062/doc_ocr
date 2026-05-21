"""doc-ocr URL configuration."""
from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("pipeline.urls")),
]

if "django_rq" in settings.INSTALLED_APPS:
    urlpatterns.insert(1, path("django-rq/", include("django_rq.urls")))

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
