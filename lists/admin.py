from django.contrib import admin

from .models import (
    AdminInviteToken,
    AggregateAlias,
    InboundMessage,
    List,
    ListAccess,
    ListAdmin,
    ListAttribute,
    ListInviteToken,
    ListRecord,
    ListRecordAccess,
    ListRecordValue,
    ListSendPermission,
    ListTemplate,
    MailReleaseToken,
    OutboundMessage,
    PendingTransfer,
    PersonRelationship,
    RecordManager,
)


class ListAttributeInline(admin.TabularInline):
    model = ListAttribute
    extra = 0
    fields = (
        "position",
        "name",
        "type",
        "applies_to_role",
        "must_be_public",
        "choices",
        "choice_cap",
    )


@admin.register(ListTemplate)
class ListTemplateAdmin(admin.ModelAdmin):
    list_display = ("name", "member_subject_mode", "default_record_role")
    search_fields = ("name",)
    inlines = [ListAttributeInline]


@admin.register(ListAttribute)
class ListAttributeAdmin(admin.ModelAdmin):
    list_display = ("template", "name", "type", "applies_to_role", "must_be_public", "position")
    list_filter = ("type", "applies_to_role", "must_be_public", "template")
    search_fields = ("name", "template__name")


class ListAdminInline(admin.TabularInline):
    model = ListAdmin
    extra = 0
    autocomplete_fields = ("user",)


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
    autocomplete_fields = ("parent",)
    inlines = [ListAdminInline]


@admin.register(ListAdmin)
class ListAdminAdmin(admin.ModelAdmin):
    list_display = ("list", "user", "granted_at")
    autocomplete_fields = ("list", "user")


@admin.register(ListAccess)
class ListAccessAdmin(admin.ModelAdmin):
    list_display = ("list", "user", "joined_at")
    autocomplete_fields = ("list", "user")


class ListRecordValueInline(admin.TabularInline):
    model = ListRecordValue
    extra = 0
    autocomplete_fields = ("attribute",)
    fields = ("attribute", "value")


class ListRecordAccessInline(admin.TabularInline):
    model = ListRecordAccess
    extra = 0
    autocomplete_fields = ("attribute", "audience")
    fields = ("attribute", "sentinel", "audience")


@admin.register(ListRecord)
class ListRecordAdmin(admin.ModelAdmin):
    list_display = ("subject", "list", "role", "archived_at", "updated_at")
    list_filter = ("role", "list", "archived_at")
    search_fields = (
        "subject__family_name",
        "subject__given_name",
        "list__title",
    )
    autocomplete_fields = ("list", "subject")
    inlines = [ListRecordValueInline, ListRecordAccessInline]


@admin.register(ListRecordValue)
class ListRecordValueAdmin(admin.ModelAdmin):
    list_display = ("record", "attribute")
    autocomplete_fields = ("record", "attribute")
    # Encrypted values aren't searchable in SQL by design.
    search_fields = ("record__subject__family_name",)


@admin.register(ListRecordAccess)
class ListRecordAccessAdmin(admin.ModelAdmin):
    list_display = ("record", "attribute", "sentinel", "audience")
    list_filter = ("sentinel",)
    autocomplete_fields = ("record", "attribute", "audience")


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
    autocomplete_fields = ("subject_person", "related_person")


@admin.register(RecordManager)
class RecordManagerAdmin(admin.ModelAdmin):
    list_display = ("record", "user", "basis", "since")
    list_filter = ("basis",)
    autocomplete_fields = ("record", "user")


@admin.register(ListInviteToken)
class ListInviteTokenAdmin(admin.ModelAdmin):
    list_display = (
        "list",
        "target_email",
        "target_person",
        "mode",
        "invited_by",
        "created_at",
        "expires_at",
        "consumed_at",
    )
    list_filter = ("mode", "consumed_at")
    search_fields = ("token", "target_email", "list__title")
    autocomplete_fields = ("list", "invited_by", "target_person")
    readonly_fields = ("token", "created_at")


@admin.register(OutboundMessage)
class OutboundMessageAdmin(admin.ModelAdmin):
    list_display = (
        "message_id",
        "list",
        "aggregate",
        "from_email",
        "recipient_email",
        "status",
        "attempts",
        "sent_at",
        "created_at",
    )
    list_filter = ("status", "anonymized_from", "list")
    search_fields = (
        "message_id",
        "alias_token",
        "from_email",
        "recipient_email",
        "subject",
    )
    autocomplete_fields = ("list", "aggregate")
    readonly_fields = (
        "message_id",
        "alias_token",
        "created_at",
        "sent_at",
    )


@admin.register(InboundMessage)
class InboundMessageAdmin(admin.ModelAdmin):
    list_display = (
        "received_at",
        "from_email",
        "to_alias",
        "subject",
        "decision",
        "matched_list",
        "imap_uid",
    )
    list_filter = ("decision", "to_alias")
    search_fields = (
        "message_id",
        "from_email",
        "to_alias",
        "subject",
    )
    autocomplete_fields = ("matched_list", "matched_outbound")
    readonly_fields = (
        "imap_uidvalidity",
        "imap_uid",
        "message_id",
        "from_email",
        "to_alias",
        "to_domain",
        "subject",
        "raw_eml",
        "received_at",
        "created_at",
    )


@admin.register(MailReleaseToken)
class MailReleaseTokenAdmin(admin.ModelAdmin):
    list_display = (
        "list",
        "aggregate",
        "kind",
        "offer_anonymize",
        "created_at",
        "expires_at",
        "consumed_at",
        "resolution",
    )
    list_filter = ("kind", "resolution", "offer_anonymize")
    search_fields = ("token", "list__title", "aggregate__title", "inbound__from_email")
    autocomplete_fields = ("list", "aggregate")
    readonly_fields = ("token", "inbound", "created_at")


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
    autocomplete_fields = ("target_list", "granted_to_list", "granted_by")


@admin.register(AggregateAlias)
class AggregateAliasAdmin(admin.ModelAdmin):
    """Super-admin-only (Django Admin access = is_staff, which only super-admins
    hold in Fichtelink). CLAUDE.md / "Aggregate email aliases": configuration is
    super-admin only.
    """

    list_display = ("email_alias", "title", "scope_list", "created_by", "created_at")
    search_fields = ("email_alias", "title")
    autocomplete_fields = ("scope_list",)
    readonly_fields = ("created_by", "created_at")

    def save_model(self, request, obj, form, change):
        if not change and not obj.created_by_id:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)


@admin.register(PendingTransfer)
class PendingTransferAdmin(admin.ModelAdmin):
    list_display = (
        "person",
        "from_list",
        "to_list",
        "status",
        "requested_by",
        "requested_at",
        "resolved_at",
    )
    list_filter = ("status",)
    search_fields = (
        "person__family_name",
        "person__given_name",
        "from_list__title",
        "to_list__title",
    )
    autocomplete_fields = ("from_list", "to_list", "person", "requested_by", "resolved_by")
    readonly_fields = ("requested_at",)


@admin.register(AdminInviteToken)
class AdminInviteTokenAdmin(admin.ModelAdmin):
    list_display = (
        "list",
        "to_email",
        "mode",
        "from_user",
        "created_at",
        "expires_at",
        "consumed_at",
    )
    list_filter = ("mode", "consumed_at")
    search_fields = ("token", "to_email", "list__title")
    autocomplete_fields = ("list", "from_user")
    readonly_fields = ("token", "created_at")
