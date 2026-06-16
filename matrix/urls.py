from django.urls import path

from . import views

app_name = "matrix"

urlpatterns = [
    path("zugang/", views.messenger_access, name="access"),
]
