from django.contrib import admin
from django.shortcuts import redirect, render
from django.urls import include, path


def welcome(request):
    # Authenticated users skip the landing page and go straight to a list
    # view (CLAUDE.md / *Post-login landing*); only anonymous visitors see
    # the public welcome screen.
    if request.user.is_authenticated:
        return redirect("lists:home")
    return render(request, "welcome.html")


urlpatterns = [
    path("", welcome, name="welcome"),
    path("admin/", admin.site.urls),
    path("auth/", include("accounts.urls")),
    path("lists/", include("lists.urls")),
    path("forms/", include("forms.urls")),
]
