from django.urls import path

from . import views

app_name = "lists"

urlpatterns = [
    path("", views.list_index, name="index"),
    path("new/", views.list_create, name="create"),
    path("<int:pk>/", views.list_detail, name="detail"),
    path("<int:pk>/invite/", views.list_invite, name="invite"),
    path("<int:pk>/records/new/", views.record_create_self, name="record_create_self"),
    path("<int:pk>/records/<int:record_pk>/edit/", views.record_edit, name="record_edit"),
    path("invite/<str:token>/", views.invite_accept, name="invite_accept"),
]
