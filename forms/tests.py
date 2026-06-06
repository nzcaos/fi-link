"""Forms tests — access control, part rendering (HTML), signup-model
constraints, asset serving + access gate, and [[asset:ID]] substitution.

Phase 1 of the signup rework: the dynamic-list embedding is gone; slot/
contribution *rendering* and the signup *actions* arrive in later phases. These
tests cover the schema, HTML rendering, and the model-level invariants.

Run on the deploy VM (`docker compose run --rm web python manage.py test forms`).
"""
from __future__ import annotations

from django.db import IntegrityError, transaction
from django.test import Client, TestCase
from django.urls import reverse

from accounts.models import Person, User

from .models import (
    Form,
    FormAccess,
    FormPart,
    FormPartAsset,
    FormSignup,
    FormSlot,
)
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


class FormPartRenderTests(TestCase):
    def setUp(self):
        self.creator = _user("root", superuser=True)
        self.viewer = _user("viewer")
        self.form = Form.objects.create(title="Aushang", created_by=self.creator)
        FormAccess.objects.create(form=self.form, user=self.viewer)
        self.html_part = FormPart.objects.create(
            form=self.form, order=0, kind=FormPart.Kind.HTML,
            title="Kopf", body="HEADER_HTML",
        )
        self.slots_part = FormPart.objects.create(
            form=self.form, order=1, kind=FormPart.Kind.SLOTS, title="Helfer",
        )

    def _get(self):
        c = Client()
        c.force_login(self.viewer)
        return c.get(reverse("forms:detail", kwargs={"pk": self.form.pk}))

    def test_html_part_renders(self):
        resp = self._get()
        self.assertContains(resp, "HEADER_HTML")

    def test_part_order_respected(self):
        body = self._get().content.decode()
        self.assertLess(body.index("Kopf"), body.index("Helfer"))

    def test_non_html_part_shows_placeholder(self):
        # Phase 1: slot/contribution parts are not yet rendered.
        resp = self._get()
        self.assertContains(resp, "in Kürze ergänzt")

    def test_closed_form_shows_hint(self):
        self.form.is_open = False
        self.form.save(update_fields=["is_open"])
        self.assertContains(self._get(), "geschlossen")


class FormSignupModelTests(TestCase):
    def setUp(self):
        self.creator = _user("root", superuser=True)
        self.alice = _user("alice")
        self.bob = _user("bob")
        self.form = Form.objects.create(title="Sommerfest", created_by=self.creator)
        self.slots_part = FormPart.objects.create(
            form=self.form, order=0, kind=FormPart.Kind.SLOTS
        )
        self.slot_a = FormSlot.objects.create(
            part=self.slots_part, order=0, label="Aufbau", capacity=2
        )
        self.slot_b = FormSlot.objects.create(
            part=self.slots_part, order=1, label="Abbau", capacity=2
        )
        self.contrib_part = FormPart.objects.create(
            form=self.form, order=1, kind=FormPart.Kind.CONTRIBUTIONS
        )

    def test_visibility_defaults(self):
        s = FormSignup.objects.create(
            part=self.slots_part, slot=self.slot_a, user=self.alice
        )
        self.assertTrue(s.name_visible)
        self.assertFalse(s.email_visible)

    def test_no_double_signup_same_slot(self):
        FormSignup.objects.create(part=self.slots_part, slot=self.slot_a, user=self.alice)
        with self.assertRaises(IntegrityError), transaction.atomic():
            FormSignup.objects.create(
                part=self.slots_part, slot=self.slot_a, user=self.alice
            )

    def test_same_user_different_slots_allowed(self):
        FormSignup.objects.create(part=self.slots_part, slot=self.slot_a, user=self.alice)
        FormSignup.objects.create(part=self.slots_part, slot=self.slot_b, user=self.alice)
        self.assertEqual(FormSignup.objects.filter(user=self.alice).count(), 2)

    def test_different_users_same_slot_allowed(self):
        FormSignup.objects.create(part=self.slots_part, slot=self.slot_a, user=self.alice)
        FormSignup.objects.create(part=self.slots_part, slot=self.slot_a, user=self.bob)
        self.assertEqual(self.slot_a.signups.count(), 2)

    def test_one_contribution_per_part_user(self):
        FormSignup.objects.create(
            part=self.contrib_part, user=self.alice, contribution_text="Apfelkuchen"
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            FormSignup.objects.create(
                part=self.contrib_part, user=self.alice, contribution_text="Brezeln"
            )


class FormAssetTests(TestCase):
    def setUp(self):
        self.creator = _user("root", superuser=True)
        self.viewer = _user("viewer")
        self.ungranted = _user("nope")
        self.form = Form.objects.create(title="Mit Bild", created_by=self.creator)
        FormAccess.objects.create(form=self.form, user=self.viewer)
        self.part = FormPart.objects.create(
            form=self.form, order=0, kind=FormPart.Kind.HTML, body=""
        )
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
        # Defense-in-depth against stored XSS via an uploaded SVG/HTML asset.
        self.assertEqual(resp["X-Content-Type-Options"], "nosniff")
        self.assertIn("sandbox", resp["Content-Security-Policy"])

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
