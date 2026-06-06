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
from .permissions import (
    can_user_access_form,
    can_user_admin_form,
    can_user_see_signup_email,
    can_user_see_signup_name,
)


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


class FormVisibilityHelperTests(TestCase):
    def setUp(self):
        self.creator = _user("root", superuser=True)
        self.viewer = _user("viewer")
        self.alice = _user("alice")
        self.form = Form.objects.create(title="Fest", created_by=self.creator)
        self.part = FormPart.objects.create(form=self.form, kind=FormPart.Kind.CONTRIBUTIONS)
        self.hidden = FormSignup.objects.create(
            part=self.part, user=self.alice, name_visible=False, email_visible=False
        )

    def test_admin_helper(self):
        self.assertTrue(can_user_admin_form(self.creator, self.form))  # creator
        self.assertFalse(can_user_admin_form(self.viewer, self.form))

    def test_name_hidden_for_others_visible_to_self_and_admin(self):
        self.assertFalse(can_user_see_signup_name(self.viewer, self.hidden))
        self.assertTrue(can_user_see_signup_name(self.alice, self.hidden))    # own
        self.assertTrue(can_user_see_signup_name(self.creator, self.hidden))  # admin

    def test_email_switch(self):
        self.assertFalse(can_user_see_signup_email(self.viewer, self.hidden))
        self.hidden.email_visible = True
        self.hidden.save(update_fields=["email_visible"])
        self.assertTrue(can_user_see_signup_email(self.viewer, self.hidden))


class FormSlotRenderTests(TestCase):
    def setUp(self):
        self.creator = _user("root", superuser=True)
        self.viewer = _user("viewer")  # has access, not signed up
        self.alice = _user("alice")
        self.bob = _user("bob")
        self.form = Form.objects.create(title="Sommerfest", created_by=self.creator)
        FormAccess.objects.create(form=self.form, user=self.viewer)
        self.part = FormPart.objects.create(
            form=self.form, kind=FormPart.Kind.SLOTS, title="Helfer"
        )
        self.slot = FormSlot.objects.create(
            part=self.part, label="Aufbau", capacity=3
        )
        FormSignup.objects.create(
            part=self.part, slot=self.slot, user=self.alice,
            name_visible=True, email_visible=True,
        )
        FormSignup.objects.create(
            part=self.part, slot=self.slot, user=self.bob, name_visible=False,
        )

    def _get_as(self, user):
        c = Client()
        c.force_login(user)
        return c.get(reverse("forms:detail", kwargs={"pk": self.form.pk}))

    def test_visible_name_and_email_shown(self):
        resp = self._get_as(self.viewer)
        self.assertContains(resp, "A Alice")
        self.assertContains(resp, "alice@x.org")

    def test_hidden_name_shows_vergeben(self):
        resp = self._get_as(self.viewer)
        self.assertContains(resp, "vergeben")
        self.assertNotContains(resp, "A Bob")  # bob is not the logged-in viewer

    def test_free_count_and_capacity(self):
        resp = self._get_as(self.viewer)
        self.assertContains(resp, "2/3")          # 2 filled of 3
        self.assertContains(resp, "noch 1 frei")

    def test_own_signup_marked(self):
        # Bob (name hidden to others) still sees his own row marked.
        resp = self._get_as(self.bob)
        self.assertContains(resp, "(du)")


class FormContributionRenderTests(TestCase):
    def setUp(self):
        self.creator = _user("root", superuser=True)
        self.viewer = _user("viewer")
        self.alice = _user("alice")
        self.bob = _user("bob")
        self.form = Form.objects.create(title="Kuchen", created_by=self.creator)
        FormAccess.objects.create(form=self.form, user=self.viewer)
        self.part = FormPart.objects.create(
            form=self.form, kind=FormPart.Kind.CONTRIBUTIONS,
            title="Kuchenspende", contribution_label="Was bringst du mit?",
        )
        FormSignup.objects.create(
            part=self.part, user=self.alice, contribution_text="Apfelkuchen",
            name_visible=True,
        )
        FormSignup.objects.create(
            part=self.part, user=self.bob, contribution_text="Brezeln",
            name_visible=False,
        )

    def _get(self):
        c = Client()
        c.force_login(self.viewer)
        return c.get(reverse("forms:detail", kwargs={"pk": self.form.pk}))

    def test_label_and_texts_render(self):
        resp = self._get()
        self.assertContains(resp, "Was bringst du mit?")
        self.assertContains(resp, "Apfelkuchen")
        self.assertContains(resp, "Brezeln")  # text visible even when name hidden

    def test_name_visibility(self):
        resp = self._get()
        self.assertContains(resp, "A Alice")
        self.assertContains(resp, "anonym")
        self.assertNotContains(resp, "A Bob")


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
