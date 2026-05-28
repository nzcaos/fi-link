from django import forms as djforms
from django.contrib import admin

from .models import Form, FormAccess, FormPart, FormPartAsset


class FormPartInline(admin.TabularInline):
    model = FormPart
    extra = 0
    fields = ("order", "title", "list")
    autocomplete_fields = ("list",)


class FormAccessInline(admin.TabularInline):
    model = FormAccess
    extra = 0
    autocomplete_fields = ("user",)


@admin.register(Form)
class FormAdmin(admin.ModelAdmin):
    """Super-admin only (Django Admin access = is_staff, which only super-admins
    hold). Parts and access are edited inline; a part's HTML body + its image
    assets are edited on the FormPart page."""

    list_display = ("title", "created_by", "created_at")
    search_fields = ("title",)
    readonly_fields = ("created_by", "created_at", "updated_at")
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


@admin.register(FormPart)
class FormPartAdmin(admin.ModelAdmin):
    list_display = ("form", "order", "title", "list")
    list_filter = ("form",)
    search_fields = ("title", "form__title")
    autocomplete_fields = ("form", "list")
    inlines = [FormPartAssetInline]


@admin.register(FormAccess)
class FormAccessAdmin(admin.ModelAdmin):
    list_display = ("form", "user", "granted_at")
    autocomplete_fields = ("form", "user")
