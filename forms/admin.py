from django import forms as djforms
from django.contrib import admin

from .models import (
    Form,
    FormAccess,
    FormPart,
    FormPartAsset,
    FormSignup,
    FormSlot,
)


class FormPartInline(admin.TabularInline):
    model = FormPart
    extra = 0
    fields = ("order", "kind", "title")


class FormAccessInline(admin.TabularInline):
    model = FormAccess
    extra = 0
    autocomplete_fields = ("user",)


@admin.register(Form)
class FormAdmin(admin.ModelAdmin):
    """Super-admin only (Django Admin access = is_staff, which only super-admins
    hold). Parts and access are edited inline; a part's slots, HTML body, and
    image assets are edited on the FormPart page."""

    list_display = ("title", "created_by", "is_open", "created_at")
    list_filter = ("is_open",)
    search_fields = ("title",)
    readonly_fields = ("created_by", "share_token", "created_at", "updated_at")
    inlines = [FormPartInline, FormAccessInline]

    def save_model(self, request, obj, form, change):
        if not change and not obj.created_by_id:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)


class FormPartAssetForm(djforms.ModelForm):
    """Lets the super-admin upload an image rather than paste raw bytes. The
    uploaded file fills `data` and `mime_type`; editing without re-uploading
    keeps the stored bytes."""

    upload = djforms.FileField(
        required=False,
        label="Bild hochladen",
        help_text="Optional: ersetzt Daten + MIME-Typ durch die hochgeladene Datei.",
    )

    class Meta:
        model = FormPartAsset
        fields = ("title", "mime_type")

    def save(self, commit=True):
        obj = super().save(commit=False)
        upload = self.cleaned_data.get("upload")
        if upload is not None:
            obj.data = upload.read()
            obj.mime_type = getattr(upload, "content_type", "") or obj.mime_type
        if commit:
            obj.save()
        return obj


class FormPartAssetInline(admin.TabularInline):
    model = FormPartAsset
    form = FormPartAssetForm
    extra = 0
    fields = ("title", "mime_type", "upload")


class FormSlotInline(admin.TabularInline):
    model = FormSlot
    extra = 0
    fields = ("order", "label", "capacity")


@admin.register(FormPart)
class FormPartAdmin(admin.ModelAdmin):
    list_display = ("form", "order", "kind", "title")
    list_filter = ("form", "kind")
    search_fields = ("title", "form__title")
    autocomplete_fields = ("form",)
    inlines = [FormSlotInline, FormPartAssetInline]


@admin.register(FormSignup)
class FormSignupAdmin(admin.ModelAdmin):
    """Read-only moderation overview of who signed up for what."""

    list_display = (
        "part",
        "slot",
        "user",
        "contribution_text",
        "name_visible",
        "email_visible",
        "created_at",
    )
    list_filter = ("part__form", "name_visible", "email_visible")
    search_fields = ("user__username", "contribution_text", "slot__label")
    readonly_fields = (
        "part",
        "slot",
        "user",
        "contribution_text",
        "name_visible",
        "email_visible",
        "created_at",
        "updated_at",
    )

    def has_add_permission(self, request):
        return False


@admin.register(FormAccess)
class FormAccessAdmin(admin.ModelAdmin):
    list_display = ("form", "user", "granted_at")
    autocomplete_fields = ("form", "user")
