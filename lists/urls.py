from django.urls import path

from . import views

app_name = "lists"

urlpatterns = [
    path("", views.list_index, name="index"),
    path("home/", views.list_home, name="home"),
    path("new/", views.list_create, name="create"),
    path("rollover/", views.rollover_preview, name="rollover_preview"),
    path("rollover/execute/", views.rollover_execute, name="rollover_execute"),
    path("transfers/<int:transfer_pk>/decide/", views.transfer_decide, name="transfer_decide"),
    path("<int:pk>/", views.list_detail, name="detail"),
    path("<int:pk>/invite/", views.list_invite, name="invite"),
    path("<int:pk>/admins/", views.admin_manage, name="admin_manage"),
    path("<int:pk>/cohort/", views.list_cohort_edit, name="cohort_edit"),
    path("<int:pk>/transfers/", views.transfers_pending, name="transfers_pending"),
    path("<int:pk>/send-test/", views.list_send_test, name="send_test"),
    path("<int:pk>/join-tokens/", views.list_join_tokens, name="join_tokens"),
    path(
        "<int:pk>/join-tokens/<str:token>/revoke/",
        views.revoke_join_token,
        name="revoke_join_token",
    ),
    path("<int:pk>/records/new/", views.record_create_self, name="record_create_self"),
    path(
        "<int:pk>/records/new-associate/",
        views.record_create_associate,
        name="record_create_associate",
    ),
    path("<int:pk>/records/<int:record_pk>/edit/", views.record_edit, name="record_edit"),
    path(
        "<int:pk>/records/<int:record_pk>/transfer/",
        views.transfer_initiate,
        name="transfer_initiate",
    ),
    path(
        "<int:pk>/records/<int:record_pk>/remove/",
        views.record_remove,
        name="record_remove",
    ),
    path("invite/<str:token>/", views.invite_accept, name="invite_accept"),
    path("join/<str:token>/", views.join_via_token, name="join_via_token"),
    path("admin-invite/<str:token>/", views.admin_invite_accept, name="admin_invite_accept"),
    path("mail/release/<str:token>/", views.mail_release, name="mail_release"),
]
