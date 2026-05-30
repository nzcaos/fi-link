from django.contrib import admin

from .models import ActivationToken, Passkey, Person, User, WebAuthnChallenge


@admin.register(Person)
class PersonAdmin(admin.ModelAdmin):
    list_display = ("family_name", "given_name", "email", "has_user")
    search_fields = ("family_name", "given_name", "email")
    list_filter = ("created_at",)

    @admin.display(boolean=True, description="hat Login")
    def has_user(self, obj: Person) -> bool:
        return hasattr(obj, "user")


@admin.register(User)
class UserAdmin(admin.ModelAdmin):
    list_display = ("username", "person", "is_active", "is_staff", "is_superuser")
    search_fields = ("username", "person__family_name", "person__given_name", "person__email")
    list_filter = ("is_active", "is_staff", "is_superuser")
    autocomplete_fields = ("person", "last_selected_list")
    readonly_fields = ("last_login", "date_joined")


@admin.register(Passkey)
class PasskeyAdmin(admin.ModelAdmin):
    list_display = ("user", "label", "created_at", "last_used_at")
    search_fields = ("label", "user__username", "user__person__family_name")
    list_filter = ("created_at", "last_used_at")
    autocomplete_fields = ("user",)
    readonly_fields = ("credential_id", "public_key", "sign_count", "created_at", "last_used_at")


@admin.register(WebAuthnChallenge)
class WebAuthnChallengeAdmin(admin.ModelAdmin):
    list_display = ("purpose", "expected_user", "created_at", "expires_at")
    list_filter = ("purpose",)
    autocomplete_fields = ("expected_user",)
    readonly_fields = ("challenge", "created_at")


@admin.register(ActivationToken)
class ActivationTokenAdmin(admin.ModelAdmin):
    list_display = ("email", "person", "user", "purpose", "created_at", "expires_at", "consumed_at")
    list_filter = ("purpose", "consumed_at")
    search_fields = ("email", "person__family_name", "person__given_name", "token")
    autocomplete_fields = ("person", "user")
    readonly_fields = ("token", "created_at")
