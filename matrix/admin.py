from django.contrib import admin

from .models import MatrixAccount, MatrixRoom, MatrixServiceAccount


@admin.register(MatrixAccount)
class MatrixAccountAdmin(admin.ModelAdmin):
    list_display = ("matrix_user_id", "user", "onboarding_status", "created_at")
    list_filter = ("onboarding_status",)
    search_fields = ("matrix_user_id", "user__username")
    # Never surface the encrypted password in the admin.
    exclude = ("password",)
    readonly_fields = ("created_at", "updated_at")


@admin.register(MatrixRoom)
class MatrixRoomAdmin(admin.ModelAdmin):
    list_display = ("room_id", "list", "created_at")
    search_fields = ("room_id", "list__title")
    readonly_fields = ("created_at",)


@admin.register(MatrixServiceAccount)
class MatrixServiceAccountAdmin(admin.ModelAdmin):
    list_display = ("matrix_user_id", "device_id", "created_at")
    # Never surface the encrypted access token.
    exclude = ("access_token",)
    readonly_fields = ("created_at", "updated_at")
