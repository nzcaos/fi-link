"""Phase 7b tests — composable forms: access control, ordered part rendering,
dynamic list part honouring per-field/subject-name visibility, asset serving
+ access gate, and [[asset:ID]] substitution.

Run on the deploy VM (`docker compose run --rm web python manage.py test forms`).
"""
from __future__ import annotations

from django.test import Client, TestCase
from django.urls import reverse

from accounts.models import Person, User
from lists.models import (
    List,
    ListAttribute,
    ListRecord,
    ListRecordAccess,
    ListRecordValue,
    ListTemplate,
)

from .models import Form, FormAccess, FormPart, FormPartAsset
from .permissions import can_user_access_form


def _user(username, *, superuser=False):
    p = Person.objects.create(
        given_name="A", family_name=username.title(), email=f"{username}@x.org"
    )
    return User.objects.create_user(
        person=p, username=username, is_superuser=superuser, is_staff=superuser
    )


class FormAccessControlTests(TestCase):
    def setUp(self):
        self.creator = _user("root", superuser=True)
        self.form = Form.objects.create(title="Infoblatt", created_by=self.creator)
        self.granted = _user("granted")
        FormAccess.objects.create(form=self.form, user=self.granted)
        self.ungranted = _user("ungranted")

    def test_permission_helper(self):
        self.assertTrue(can_user_access_form(self.granted, self.form))
        self.assertTrue(can_user_access_form(self.creator, self.form))
        self.assertFalse(can_user_access_form(self.ungranted, self.form))

    def test_detail_forbidden_without_access(self):
        c = Client()
        c.force_login(self.ungranted)
        resp = c.get(reverse("forms:detail", kwargs={"pk": self.form.pk}))
        self.assertEqual(resp.status_code, 403)

    def test_detail_ok_with_access(self):
        c = Client()
        c.force_login(self.granted)
        resp = c.get(reverse("forms:detail", kwargs={"pk": self.form.pk}))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Infoblatt")

    def test_detail_requires_login(self):
        resp = Client().get(reverse("forms:detail", kwargs={"pk": self.form.pk}))
        self.assertEqual(resp.status_code, 302)

    def test_index_lists_only_accessible(self):
        other = Form.objects.create(title="Geheim", created_by=self.creator)
        c = Client()
        c.force_login(self.granted)
        resp = c.get(reverse("forms:index"))
        self.assertContains(resp, "Infoblatt")
        self.assertNotContains(resp, "Geheim")
        # Super-admin sees all.
        c.force_login(self.creator)
        resp = c.get(reverse("forms:index"))
        self.assertContains(resp, "Geheim")


class FormRenderTests(TestCase):
    def setUp(self):
        self.creator = _user("root", superuser=True)
        self.viewer = _user("viewer")  # has form access, NOT a list member

        # A list with one public and one private attribute.
        self.template = ListTemplate.objects.create(name="Klasse")
        self.attr_pub = ListAttribute.objects.create(
            template=self.template, name="Telefon", type=ListAttribute.Type.PHONE, position=0
        )
        self.attr_priv = ListAttribute.objects.create(
            template=self.template, name="Adresse", type=ListAttribute.Type.TEXT, position=1
        )
        self.lst = List.objects.create(
            title="Klasse 5a", email_alias="5a", template=self.template,
            visibility=List.Visibility.PRIVATE,
        )
        subj = Person.objects.create(given_name="Kind", family_name="Eins")
        self.record = ListRecord.objects.create(
            list=self.lst, subject=subj, role=ListRecord.Role.MEMBER
        )
        ListRecordValue.objects.create(record=self.record, attribute=self.attr_pub, value="PUBVAL")
        ListRecordValue.objects.create(record=self.record, attribute=self.attr_priv, value="PRIVVAL")
        # attr_pub is public; attr_priv has no access row. (Subject-name default
        # public row is written by the ListRecord post_save signal.)
        ListRecordAccess.objects.create(record=self.record, attribute=self.attr_pub, audience=None)

        self.form = Form.objects.create(title="Aushang", created_by=self.creator)
        FormAccess.objects.create(form=self.form, user=self.viewer)
        self.static_part = FormPart.objects.create(
            form=self.form, order=0, title="Kopf", body="HEADER_HTML"
        )
        self.list_part = FormPart.objects.create(
            form=self.form, order=1, title="Klassenliste", list=self.lst
        )

    def _get(self):
        c = Client()
        c.force_login(self.viewer)
        return c.get(reverse("forms:detail", kwargs={"pk": self.form.pk}))

    def test_static_and_dynamic_parts_render(self):
        resp = self._get()
        self.assertContains(resp, "HEADER_HTML")
        self.assertContains(resp, "Kind Eins")  # subject name public by default

    def test_dynamic_part_respects_field_visibility(self):
        resp = self._get()
        self.assertContains(resp, "PUBVAL")     # public field visible
        self.assertNotContains(resp, "PRIVVAL")  # private field hidden

    def test_part_order_respected(self):
        resp = self._get()
        body = resp.content.decode()
        self.assertLess(body.index("Kopf"), body.index("Klassenliste"))

    def test_anonymised_name_when_not_visible(self):
        # Drop the default public name row → viewer (no list access) sees ?N.
        ListRecordAccess.objects.filter(
            record=self.record, attribute__isnull=True
        ).delete()
        resp = self._get()
        self.assertNotContains(resp, "Kind Eins")
        self.assertContains(resp, "?1")


class FormAssetTests(TestCase):
    def setUp(self):
        self.creator = _user("root", superuser=True)
        self.viewer = _user("viewer")
        self.ungranted = _user("nope")
        self.form = Form.objects.create(title="Mit Bild", created_by=self.creator)
        FormAccess.objects.create(form=self.form, user=self.viewer)
        self.part = FormPart.objects.create(form=self.form, order=0, body="")
        self.asset = FormPartAsset.objects.create(
            part=self.part, title="Logo", mime_type="image/png", data=b"\x89PNGdata"
        )

    def test_asset_served_with_mime_and_bytes(self):
        c = Client()
        c.force_login(self.viewer)
        resp = c.get(
            reverse("forms:asset", kwargs={"form_pk": self.form.pk, "asset_pk": self.asset.pk})
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Content-Type"], "image/png")
        self.assertEqual(resp.content, b"\x89PNGdata")

    def test_asset_access_gated(self):
        c = Client()
        c.force_login(self.ungranted)
        resp = c.get(
            reverse("forms:asset", kwargs={"form_pk": self.form.pk, "asset_pk": self.asset.pk})
        )
        self.assertEqual(resp.status_code, 403)

    def test_asset_of_other_form_404(self):
        other = Form.objects.create(title="Other", created_by=self.creator)
        FormAccess.objects.create(form=other, user=self.viewer)
        c = Client()
        c.force_login(self.viewer)
        # asset belongs to self.form, not `other` → 404 under other's URL.
        resp = c.get(
            reverse("forms:asset", kwargs={"form_pk": other.pk, "asset_pk": self.asset.pk})
        )
        self.assertEqual(resp.status_code, 404)

    def test_body_asset_placeholder_substituted(self):
        self.part.body = f"Bild: [[asset:{self.asset.pk}]] Ende"
        self.part.save(update_fields=["body"])
        c = Client()
        c.force_login(self.viewer)
        resp = c.get(reverse("forms:detail", kwargs={"pk": self.form.pk}))
        asset_url = reverse(
            "forms:asset", kwargs={"form_pk": self.form.pk, "asset_pk": self.asset.pk}
        )
        self.assertContains(resp, asset_url)
        self.assertNotContains(resp, "[[asset:")
