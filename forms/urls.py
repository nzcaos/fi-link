from django.urls import path

from . import views

app_name = "forms"

urlpatterns = [
    path("", views.form_index, name="index"),
    path("s/<str:token>/", views.shared, name="shared"),
    path("s/<str:token>/signin", views.shared_signin, name="shared_signin"),
    path("<int:pk>/", views.form_detail, name="detail"),
    path("<int:pk>/share-link", views.share_link, name="share_link"),
    path("<int:pk>/slots/<int:slot_pk>/signup", views.slot_signup, name="slot_signup"),
    path("<int:pk>/parts/<int:part_pk>/contribute", views.contribute, name="contribute"),
    path("<int:pk>/signups/<int:signup_pk>/remove", views.signup_remove, name="signup_remove"),
    path(
        "<int:form_pk>/assets/<int:asset_pk>/",
        views.form_asset,
        name="asset",
    ),
]
