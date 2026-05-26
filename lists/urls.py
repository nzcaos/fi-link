from django.urls import path

from . import views

app_name = "lists"

urlpatterns = [
    path("", views.list_index, name="index"),
    path("new/", views.list_create, name="create"),
    path("<int:pk>/", views.list_detail, name="detail"),
]
