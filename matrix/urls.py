from django.urls import path

from . import views

app_name = "matrix"

urlpatterns = [
    path("zugang/", views.messenger_access, name="access"),
    path("liste/<int:pk>/senden/", views.send_message, name="send"),
    path("liste/<int:pk>/moderation/", views.room_moderation, name="moderation"),
]
