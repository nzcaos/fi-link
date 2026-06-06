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

    def test_body_renders_for_non_html_parts(self):
        # The authored HTML description must also show on slot/contribution parts.
        self.slots_part.body = "SLOT_DESC_HTML"
        self.slots_part.save(update_fields=["body"])
        self.assertContains(self._get(), "SLOT_DESC_HTML")


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
        # A signed-up user has form access (auto-granted on signup in Phase 3).
        FormAccess.objects.create(form=self.form, user=self.bob)
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

    def test_add_button_shown(self):
        # Viewer has not contributed → last-row button offers to add one.
        self.assertContains(self._get(), "Beitrag eintragen")


class FormSignupActionTests(TestCase):
    def setUp(self):
        self.creator = _user("root", superuser=True)
        self.alice = _user("alice")  # has access
        self.bob = _user("bob")      # no access, no token
        self.form = Form.objects.create(
            title="Fest", created_by=self.creator, share_token="TOK"
        )
        FormAccess.objects.create(form=self.form, user=self.alice)
        self.slots_part = FormPart.objects.create(
            form=self.form, kind=FormPart.Kind.SLOTS
        )
        self.slot = FormSlot.objects.create(
            part=self.slots_part, label="Aufbau", capacity=1
        )
        self.contrib_part = FormPart.objects.create(
            form=self.form, kind=FormPart.Kind.CONTRIBUTIONS, contribution_required=True
        )

    def _client(self, user):
        c = Client()
        c.force_login(user)
        return c

    def _slot_url(self):
        return reverse(
            "forms:slot_signup", kwargs={"pk": self.form.pk, "slot_pk": self.slot.pk}
        )

    def _contrib_url(self):
        return reverse(
            "forms:contribute",
            kwargs={"pk": self.form.pk, "part_pk": self.contrib_part.pk},
        )

    # --- slot signup ---
    def test_slot_signup_creates_and_grants_access(self):
        resp = self._client(self.alice).post(self._slot_url(), {"name_visible": "on"})
        self.assertEqual(resp.status_code, 302)
        s = FormSignup.objects.get(slot=self.slot, user=self.alice)
        self.assertTrue(s.name_visible)
        self.assertFalse(s.email_visible)  # checkbox absent → hidden
        self.assertTrue(FormAccess.objects.filter(form=self.form, user=self.alice).exists())

    def test_slot_signup_without_access_or_token_forbidden(self):
        resp = self._client(self.bob).post(self._slot_url(), {"name_visible": "on"})
        self.assertEqual(resp.status_code, 403)
        self.assertFalse(FormSignup.objects.filter(user=self.bob).exists())

    def test_slot_capacity_enforced(self):
        FormSignup.objects.create(
            part=self.slots_part, slot=self.slot, user=self.creator
        )  # fills capacity 1
        self._client(self.alice).post(self._slot_url(), {"name_visible": "on"})
        self.assertEqual(FormSignup.objects.filter(slot=self.slot).count(), 1)

    def test_no_double_signup_same_slot(self):
        c = self._client(self.alice)
        c.post(self._slot_url(), {"name_visible": "on"})
        c.post(self._slot_url(), {"name_visible": "on"})
        self.assertEqual(
            FormSignup.objects.filter(slot=self.slot, user=self.alice).count(), 1
        )

    def test_slot_signup_get_renders_form(self):
        resp = self._client(self.alice).get(self._slot_url())
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Name sichtbar")

    def test_slot_visibility_editable_after_signup(self):
        c = self._client(self.alice)
        c.post(self._slot_url(), {"name_visible": "on"})  # create, email hidden
        s = FormSignup.objects.get(slot=self.slot, user=self.alice)
        self.assertFalse(s.email_visible)
        c.post(self._slot_url(), {"name_visible": "on", "email_visible": "on"})  # edit
        s.refresh_from_db()
        self.assertTrue(s.email_visible)
        self.assertEqual(
            FormSignup.objects.filter(slot=self.slot, user=self.alice).count(), 1
        )

    # --- contribution ---
    def test_contribute_creates_then_updates(self):
        c = self._client(self.alice)
        c.post(self._contrib_url(), {"contribution_text": "Apfelkuchen", "name_visible": "on"})
        s = FormSignup.objects.get(part=self.contrib_part, user=self.alice)
        self.assertEqual(s.contribution_text, "Apfelkuchen")
        c.post(self._contrib_url(), {"contribution_text": "Brezeln"})
        s.refresh_from_db()
        self.assertEqual(s.contribution_text, "Brezeln")
        self.assertEqual(
            FormSignup.objects.filter(part=self.contrib_part, user=self.alice).count(), 1
        )

    def test_contribute_required_validation(self):
        resp = self._client(self.alice).post(self._contrib_url(), {"name_visible": "on"})
        self.assertEqual(resp.status_code, 200)  # re-rendered with errors
        self.assertFalse(
            FormSignup.objects.filter(part=self.contrib_part, user=self.alice).exists()
        )

    # --- removal ---
    def test_remove_own(self):
        s = FormSignup.objects.create(part=self.slots_part, slot=self.slot, user=self.alice)
        self._client(self.alice).post(
            reverse("forms:signup_remove", kwargs={"pk": self.form.pk, "signup_pk": s.pk})
        )
        self.assertFalse(FormSignup.objects.filter(pk=s.pk).exists())

    def test_remove_other_forbidden(self):
        s = FormSignup.objects.create(part=self.slots_part, slot=self.slot, user=self.alice)
        resp = self._client(self.bob).post(
            reverse("forms:signup_remove", kwargs={"pk": self.form.pk, "signup_pk": s.pk})
        )
        self.assertEqual(resp.status_code, 403)
        self.assertTrue(FormSignup.objects.filter(pk=s.pk).exists())

    def test_admin_can_remove_other(self):
        s = FormSignup.objects.create(part=self.slots_part, slot=self.slot, user=self.alice)
        self._client(self.creator).post(
            reverse("forms:signup_remove", kwargs={"pk": self.form.pk, "signup_pk": s.pk})
        )
        self.assertFalse(FormSignup.objects.filter(pk=s.pk).exists())

    # --- closed form gates all writes ---
    def test_closed_form_blocks_signup_and_contribute(self):
        self.form.is_open = False
        self.form.save(update_fields=["is_open"])
        c = self._client(self.alice)
        self.assertEqual(c.post(self._slot_url(), {"name_visible": "on"}).status_code, 403)
        self.assertEqual(
            c.post(self._contrib_url(), {"contribution_text": "X"}).status_code, 403
        )
        self.assertFalse(FormSignup.objects.filter(user=self.alice).exists())


class FormBroadcastTests(TestCase):
    def setUp(self):
        self.creator = _user("root", superuser=True)
        self.alice = _user("alice")  # no FormAccess
        self.form = Form.objects.create(
            title="Sommerfest", created_by=self.creator, share_token="TOK"
        )
        self.part = FormPart.objects.create(form=self.form, kind=FormPart.Kind.SLOTS)
        self.slot = FormSlot.objects.create(part=self.part, label="Aufbau", capacity=2)

    def test_shared_anonymous_view(self):
        resp = Client().get(reverse("forms:shared", kwargs={"token": "TOK"}))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Sommerfest")
        self.assertContains(resp, "Anmelden")  # login handoff for anonymous

    def test_shared_invalid_token_404(self):
        resp = Client().get(reverse("forms:shared", kwargs={"token": "NOPE"}))
        self.assertEqual(resp.status_code, 404)

    def test_shared_signin_sets_session_and_redirects(self):
        c = Client()
        resp = c.get(reverse("forms:shared_signin", kwargs={"token": "TOK"}))
        self.assertEqual(resp.status_code, 302)
        self.assertIn(reverse("accounts:login"), resp.url)
        self.assertEqual(c.session["pending_form_token"], "TOK")

    def test_authenticated_view_carries_token(self):
        c = Client()
        c.force_login(self.alice)
        resp = c.get(reverse("forms:shared", kwargs={"token": "TOK"}))
        self.assertContains(resp, 'value="TOK"')  # hidden token in signup form
        self.assertContains(resp, "Eintragen")

    def test_token_signup_succeeds_without_prior_access(self):
        c = Client()
        c.force_login(self.alice)
        url = reverse(
            "forms:slot_signup", kwargs={"pk": self.form.pk, "slot_pk": self.slot.pk}
        )
        resp = c.post(url, {"token": "TOK", "name_visible": "on"})
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, reverse("forms:shared", kwargs={"token": "TOK"}))
        self.assertTrue(FormSignup.objects.filter(slot=self.slot, user=self.alice).exists())
        self.assertTrue(FormAccess.objects.filter(form=self.form, user=self.alice).exists())

    def test_share_link_generates_token_admin_only(self):
        fresh = Form.objects.create(title="Neu", created_by=self.creator)
        self.assertIsNone(fresh.share_token)
        c = Client()
        c.force_login(self.creator)
        resp = c.post(reverse("forms:share_link", kwargs={"pk": fresh.pk}))
        self.assertEqual(resp.status_code, 302)
        fresh.refresh_from_db()
        self.assertTrue(fresh.share_token)
        # Non-admin is forbidden.
        other = _user("other")
        c2 = Client()
        c2.force_login(other)
        resp = c2.post(reverse("forms:share_link", kwargs={"pk": self.form.pk}))
        self.assertEqual(resp.status_code, 403)


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
