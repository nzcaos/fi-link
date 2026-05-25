from django.urls import path

from . import views

app_name = "accounts"

urlpatterns = [
    # Registration
    path("register/", views.register_start, name="register_start"),
    path("register/force/", views.register_force, name="register_force"),
    path("activate/<str:token>/", views.activate, name="activate"),
    path("register/passkey-begin", views.register_passkey_begin, name="register_passkey_begin"),
    path("register/passkey-finish", views.register_passkey_finish, name="register_passkey_finish"),

    # Login / logout
    path("login/", views.login_start, name="login"),
    path("login-begin", views.login_begin, name="login_begin"),
    path("login-finish", views.login_finish, name="login_finish"),
    path("logout", views.logout_view, name="logout"),

    # Passkey management
    path("passkeys/", views.passkeys_index, name="passkeys"),
    path("passkeys/list", views.passkeys_list_fragment, name="passkey_list_fragment"),
    path("passkeys/add-begin", views.passkey_add_begin, name="passkey_add_begin"),
    path("passkeys/add-finish", views.passkey_add_finish, name="passkey_add_finish"),
    path("passkeys/<int:pk>/delete", views.passkey_delete, name="passkey_delete"),
]
