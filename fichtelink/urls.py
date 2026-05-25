from django.contrib import admin
from django.http import HttpResponse
from django.urls import path


def welcome(request):
    return HttpResponse(
        "<!doctype html><meta charset=\"utf-8\">"
        "<title>Fichtelink</title>"
        "<h1>Fichtelink</h1>"
        "<p>Phase 0 — Skeleton & Compose. Der Stack läuft.</p>",
        content_type="text/html; charset=utf-8",
    )


urlpatterns = [
    path("", welcome, name="welcome"),
    path("admin/", admin.site.urls),
]
