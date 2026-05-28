from django.contrib import admin
from django.shortcuts import render
from django.urls import include, path


def welcome(request):
    return render(request, "welcome.html")


urlpatterns = [
    path("", welcome, name="welcome"),
    path("admin/", admin.site.urls),
    path("auth/", include("accounts.urls")),
    path("lists/", include("lists.urls")),
    path("forms/", include("forms.urls")),
]
