from django.urls import path

from . import views

app_name = "forms"

urlpatterns = [
    path("", views.form_index, name="index"),
    path("<int:pk>/", views.form_detail, name="detail"),
    path(
        "<int:form_pk>/assets/<int:asset_pk>/",
        views.form_asset,
        name="asset",
    ),
]
