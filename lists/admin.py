from django.contrib import admin

from .models import (
    List,
    ListAccess,
    ListAdmin,
    ListAttribute,
    ListRecord,
    ListRecordAccess,
    ListRecordValue,
    ListSendPermission,
    ListTemplate,
    PersonRelationship,
    RecordManager,
)


class ListAttributeInline(admin.TabularInline):
    model = ListAttribute
    extra = 0
    fields = ("position", "name", "type", "must_be_public", "choices", "choice_cap")


@admin.register(ListTemplate)
class ListTemplateAdmin(admin.ModelAdmin):
    list_display = ("name", "member_subject_mode", "default_record_role")
    search_fields = ("name",)
    inlines = [ListAttributeInline]


@admin.register(ListAttribute)
class ListAttributeAdmin(admin.ModelAdmin):
    list_display = ("template", "name", "type", "must_be_public", "position")
    list_filter = ("type", "must_be_public", "template")
    search_fields = ("name", "template__name")


class ListAdminInline(admin.TabularInline):
    model = ListAdmin
    extra = 0
    raw_id_fields = ("user",)


@admin.register(List)
class ListListAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "email_alias",
        "template",
        "parent",
        "visibility",
        "cohort_grade",
        "cohort_track",
        "archived_at",
    )
    list_filter = ("visibility", "template", "curriculum_track", "archived_at")
    search_fields = ("title", "email_alias")
    raw_id_fields = ("parent",)
    inlines = [ListAdminInline]


@admin.register(ListAdmin)
class ListAdminAdmin(admin.ModelAdmin):
    list_display = ("list", "user", "granted_at")
    raw_id_fields = ("list", "user")


@admin.register(ListAccess)
class ListAccessAdmin(admin.ModelAdmin):
    list_display = ("list", "user", "joined_at")
    raw_id_fields = ("list", "user")


class ListRecordValueInline(admin.TabularInline):
    model = ListRecordValue
    extra = 0
    raw_id_fields = ("attribute",)
    fields = ("attribute", "value")


class ListRecordAccessInline(admin.TabularInline):
    model = ListRecordAccess
    extra = 0
    raw_id_fields = ("attribute", "audience")
    fields = ("attribute", "audience")


@admin.register(ListRecord)
class ListRecordAdmin(admin.ModelAdmin):
    list_display = ("subject", "list", "role", "archived_at", "updated_at")
    list_filter = ("role", "list", "archived_at")
    search_fields = (
        "subject__family_name",
        "subject__given_name",
        "list__title",
    )
    raw_id_fields = ("list", "subject")
    inlines = [ListRecordValueInline, ListRecordAccessInline]


@admin.register(ListRecordValue)
class ListRecordValueAdmin(admin.ModelAdmin):
    list_display = ("record", "attribute")
    raw_id_fields = ("record", "attribute")
    # Encrypted values aren't searchable in SQL by design.
    search_fields = ("record__subject__family_name",)


@admin.register(ListRecordAccess)
class ListRecordAccessAdmin(admin.ModelAdmin):
    list_display = ("record", "attribute", "audience")
    raw_id_fields = ("record", "attribute", "audience")


@admin.register(PersonRelationship)
class PersonRelationshipAdmin(admin.ModelAdmin):
    list_display = ("related_person", "role", "subject_person", "created_at")
    search_fields = (
        "subject_person__family_name",
        "subject_person__given_name",
        "related_person__family_name",
        "related_person__given_name",
        "role",
    )
    raw_id_fields = ("subject_person", "related_person")


@admin.register(RecordManager)
class RecordManagerAdmin(admin.ModelAdmin):
    list_display = ("record", "user", "basis", "since")
    list_filter = ("basis",)
    raw_id_fields = ("record", "user")


@admin.register(ListSendPermission)
class ListSendPermissionAdmin(admin.ModelAdmin):
    list_display = (
        "target_list",
        "granted_to_list",
        "requires_release_click",
        "transitive",
        "granted_at",
    )
    list_filter = ("requires_release_click", "transitive")
    raw_id_fields = ("target_list", "granted_to_list", "granted_by")
