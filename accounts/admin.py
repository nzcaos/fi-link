from django.contrib import admin

from .models import Person, User


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
    raw_id_fields = ("person",)
    readonly_fields = ("last_login", "date_joined")
