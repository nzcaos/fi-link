from django.contrib import admin
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect, render
from django.urls import include, path


def welcome(request):
    # Authenticated users skip the landing page and go straight to a list
    # view (CLAUDE.md / *Post-login landing*); only anonymous visitors see
    # the public welcome screen.
    if request.user.is_authenticated:
        return redirect("lists:home")
    return render(request, "welcome.html")


def hilfe(request):
    # In-app HTML rendering of docs/benutzerhandbuch.md. Readable for everyone —
    # help should not require a login.
    return render(request, "hilfe.html")


def hilfe_superadmin(request):
    # In-app HTML rendering of docs/superadmin-handbuch.md. Super-admin only.
    if not (request.user.is_authenticated and request.user.is_superuser):
        raise PermissionDenied
    return render(request, "hilfe_superadmin.html")


urlpatterns = [
    path("", welcome, name="welcome"),
    path("hilfe/", hilfe, name="hilfe"),
    path("hilfe/superadmin/", hilfe_superadmin, name="hilfe_superadmin"),
    path("admin/", admin.site.urls),
    path("auth/", include("accounts.urls")),
    path("lists/", include("lists.urls")),
    path("forms/", include("forms.urls")),
    path("matrix/", include("matrix.urls")),
]
