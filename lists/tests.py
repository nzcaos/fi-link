"""Phase-3a/3b unit tests: visibility service, creation/edit permissions,
record-edit form, invite-token flow.

Tests run on the deploy VM (`docker compose run --rm web python manage.py test
lists`). Lokal kein Runtime-Stack vorhanden.
"""
from __future__ import annotations

from datetime import timedelta

from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from django.core import mail

from accounts.models import Person, User
from .forms import ListCreateForm, ListInviteForm, RecordEditForm
from .models import (
    List,
    ListAccess,
    ListAdmin,
    ListAttribute,
    ListInviteToken,
    ListRecord,
    ListRecordAccess,
    ListRecordValue,
    ListTemplate,
    RecordManager,
)
from .permissions import (
    can_user_admin_list,
    can_user_create_sublist_under,
    can_user_create_top_level_list,
    can_user_edit_record,
    can_user_see_list,
    eligible_parents_for,
)
from .visibility import can_user_see_field, visible_attributes_for


def _make_user(*, username: str, given: str = "Test", family: str = "User", email: str | None = None, **extra) -> User:
    person = Person.objects.create(given_name=given, family_name=family, email=email)
    return User.objects.create_user(person=person, username=username, **extra)


def _make_super(username: str = "super") -> User:
    person = Person.objects.create(given_name="Super", family_name="Admin")
    return User.objects.create_user(
        person=person, username=username, is_superuser=True, is_staff=True
    )


class VisibilityServiceTests(TestCase):
    def setUp(self):
        self.template = ListTemplate.objects.create(name="Schulklasse")
        self.attr_name = ListAttribute.objects.create(
            template=self.template, name="Name", type=ListAttribute.Type.TEXT, position=0
        )
        self.attr_phone = ListAttribute.objects.create(
            template=self.template, name="Telefon", type=ListAttribute.Type.PHONE, position=1
        )
        self.attr_public = ListAttribute.objects.create(
            template=self.template,
            name="Klasse",
            type=ListAttribute.Type.TEXT,
            must_be_public=True,
            position=2,
        )

        self.lst = List.objects.create(
            title="Klasse 5a",
            email_alias="5a",
            template=self.template,
            visibility=List.Visibility.PRIVATE,
        )
        self.other_lst = List.objects.create(
            title="Elternbeirat",
            email_alias="elternbeirat",
            template=self.template,
            visibility=List.Visibility.PRIVATE,
        )

        self.owner = _make_user(username="owner", given="Anna", family="Müller")
        self.member = _make_user(username="member", given="Berta", family="Schmid")
        self.outsider = _make_user(username="outsider", given="Carla", family="X")
        self.admin = _make_user(username="adminuser", given="Dora", family="Y")
        self.super_ = _make_super()

        self.record = ListRecord.objects.create(list=self.lst, subject=self.owner.person)
        RecordManager.objects.create(
            record=self.record, user=self.owner, basis=RecordManager.Basis.SELF_REGISTERED
        )

        ListAccess.objects.create(list=self.lst, user=self.member)
        ListAccess.objects.create(list=self.other_lst, user=self.member)
        ListAdmin.objects.create(list=self.lst, user=self.admin)

    def test_must_be_public_visible_to_everyone(self):
        self.assertTrue(can_user_see_field(self.outsider, self.record, self.attr_public))
        self.assertTrue(can_user_see_field(self.member, self.record, self.attr_public))

    def test_no_access_rows_invisible_to_outsider(self):
        self.assertFalse(can_user_see_field(self.outsider, self.record, self.attr_phone))

    def test_owner_sees_everything(self):
        self.assertTrue(can_user_see_field(self.owner, self.record, self.attr_phone))
        self.assertTrue(can_user_see_field(self.owner, self.record, self.attr_name))

    def test_list_admin_sees_everything(self):
        self.assertTrue(can_user_see_field(self.admin, self.record, self.attr_phone))

    def test_super_admin_sees_everything(self):
        self.assertTrue(can_user_see_field(self.super_, self.record, self.attr_phone))

    def test_public_audience_visible_to_all_authenticated(self):
        ListRecordAccess.objects.create(
            record=self.record, attribute=self.attr_phone, audience=None
        )
        self.assertTrue(can_user_see_field(self.outsider, self.record, self.attr_phone))

    def test_audience_list_visible_to_its_members_only(self):
        ListRecordAccess.objects.create(
            record=self.record, attribute=self.attr_phone, audience=self.other_lst
        )
        self.assertTrue(can_user_see_field(self.member, self.record, self.attr_phone))
        self.assertFalse(can_user_see_field(self.outsider, self.record, self.attr_phone))

    def test_archived_record_hides_non_public_from_outsider(self):
        ListRecordAccess.objects.create(
            record=self.record, attribute=self.attr_phone, audience=None
        )
        self.record.archived_at = self.record.created_at
        self.record.save()
        self.assertFalse(can_user_see_field(self.outsider, self.record, self.attr_phone))
        # Owner and admin still see archived data.
        self.assertTrue(can_user_see_field(self.owner, self.record, self.attr_phone))
        self.assertTrue(can_user_see_field(self.admin, self.record, self.attr_phone))
        # must_be_public stays visible even on archived (attribute-level rule).
        self.assertTrue(can_user_see_field(self.outsider, self.record, self.attr_public))

    def test_anonymous_sees_only_must_be_public(self):
        from django.contrib.auth.models import AnonymousUser

        anon = AnonymousUser()
        self.assertTrue(can_user_see_field(anon, self.record, self.attr_public))
        self.assertFalse(can_user_see_field(anon, self.record, self.attr_phone))

    def test_public_audience_list_acts_as_universal_membership(self):
        public_list = List.objects.create(
            title="Förderverein",
            email_alias="fv",
            template=self.template,
            visibility=List.Visibility.PUBLIC_VISIBLE,
        )
        ListRecordAccess.objects.create(
            record=self.record, attribute=self.attr_phone, audience=public_list
        )
        # Outsider is in no list explicitly, but the audience list is public →
        # they count as in its Benutzergruppe.
        self.assertTrue(can_user_see_field(self.outsider, self.record, self.attr_phone))

    def test_visible_attributes_orders_by_position_and_filters(self):
        attrs = list(visible_attributes_for(self.outsider, self.record))
        self.assertEqual([a.pk for a in attrs], [self.attr_public.pk])


class CreationPermissionTests(TestCase):
    def setUp(self):
        self.template = ListTemplate.objects.create(name="Schulklasse")
        self.parent = List.objects.create(
            title="Elternbeirat",
            email_alias="eb",
            template=self.template,
            visibility=List.Visibility.PRIVATE,
        )
        self.public_parent = List.objects.create(
            title="Förderverein",
            email_alias="fv",
            template=self.template,
            visibility=List.Visibility.PUBLIC_VISIBLE,
        )
        self.archived = List.objects.create(
            title="Klasse 12a (alt)",
            email_alias="12a-2020",
            template=self.template,
            visibility=List.Visibility.PRIVATE,
        )
        self.archived.archived_at = self.archived.created_at
        self.archived.save()

        self.super_ = _make_super()
        self.member = _make_user(username="member")
        self.admin = _make_user(username="adminuser")
        self.outsider = _make_user(username="outsider")

        ListAccess.objects.create(list=self.parent, user=self.member)
        ListAdmin.objects.create(list=self.parent, user=self.admin)

    def test_only_super_admin_creates_top_level(self):
        self.assertTrue(can_user_create_top_level_list(self.super_))
        self.assertFalse(can_user_create_top_level_list(self.member))
        self.assertFalse(can_user_create_top_level_list(self.admin))
        self.assertFalse(can_user_create_top_level_list(self.outsider))

    def test_sublist_under_parent_requires_membership_or_admin(self):
        self.assertTrue(can_user_create_sublist_under(self.admin, self.parent))
        self.assertTrue(can_user_create_sublist_under(self.member, self.parent))
        self.assertFalse(can_user_create_sublist_under(self.outsider, self.parent))
        self.assertTrue(can_user_create_sublist_under(self.super_, self.parent))

    def test_public_parent_lets_anyone_create_sublist(self):
        self.assertTrue(can_user_create_sublist_under(self.outsider, self.public_parent))

    def test_archived_parent_blocks_new_sublist_for_regular_users(self):
        # Make outsider a member of the archived list — still blocked.
        ListAccess.objects.create(list=self.archived, user=self.outsider)
        self.assertFalse(can_user_create_sublist_under(self.outsider, self.archived))
        # Super-admin can still create under it (curation case).
        self.assertTrue(can_user_create_sublist_under(self.super_, self.archived))

    def test_eligible_parents_for_regular_user(self):
        parents = set(eligible_parents_for(self.member).values_list("pk", flat=True))
        self.assertIn(self.parent.pk, parents)
        self.assertIn(self.public_parent.pk, parents)
        self.assertNotIn(self.archived.pk, parents)

    def test_eligible_parents_for_super_admin_lists_all_non_archived(self):
        parents = set(eligible_parents_for(self.super_).values_list("pk", flat=True))
        self.assertEqual(parents, {self.parent.pk, self.public_parent.pk})

    def test_can_user_admin_and_see_list(self):
        self.assertTrue(can_user_admin_list(self.admin, self.parent))
        self.assertFalse(can_user_admin_list(self.member, self.parent))
        self.assertTrue(can_user_see_list(self.member, self.parent))
        self.assertFalse(can_user_see_list(self.outsider, self.parent))
        self.assertTrue(can_user_see_list(self.outsider, self.public_parent))


class EditPermissionTests(TestCase):
    def setUp(self):
        self.template = ListTemplate.objects.create(name="Schulklasse")
        self.attr = ListAttribute.objects.create(
            template=self.template, name="Name", type=ListAttribute.Type.TEXT
        )
        self.lst = List.objects.create(
            title="Klasse 5a",
            email_alias="5a",
            template=self.template,
            visibility=List.Visibility.PRIVATE,
        )
        self.owner = _make_user(username="owner")
        self.admin = _make_user(username="adminuser")
        self.outsider = _make_user(username="outsider")
        self.super_ = _make_super()

        self.record = ListRecord.objects.create(list=self.lst, subject=self.owner.person)
        RecordManager.objects.create(
            record=self.record, user=self.owner, basis=RecordManager.Basis.CREATOR
        )
        ListAdmin.objects.create(list=self.lst, user=self.admin)

    def test_owner_edits(self):
        self.assertTrue(can_user_edit_record(self.owner, self.record))

    def test_admin_edits(self):
        self.assertTrue(can_user_edit_record(self.admin, self.record))

    def test_outsider_blocked(self):
        self.assertFalse(can_user_edit_record(self.outsider, self.record))

    def test_super_admin_edits(self):
        self.assertTrue(can_user_edit_record(self.super_, self.record))

    def test_archived_blocks_even_owner(self):
        self.record.archived_at = self.record.created_at
        self.record.save()
        self.assertFalse(can_user_edit_record(self.owner, self.record))
        self.assertFalse(can_user_edit_record(self.admin, self.record))
        self.assertFalse(can_user_edit_record(self.super_, self.record))


class RecordEditFormTests(TestCase):
    def setUp(self):
        self.template = ListTemplate.objects.create(name="Schulklasse")
        self.attr_name = ListAttribute.objects.create(
            template=self.template, name="Name", type=ListAttribute.Type.TEXT, position=0,
            must_be_public=True,
        )
        self.attr_phone = ListAttribute.objects.create(
            template=self.template, name="Telefon", type=ListAttribute.Type.PHONE, position=1
        )
        self.lst = List.objects.create(
            title="Klasse 5a",
            email_alias="5a",
            template=self.template,
            visibility=List.Visibility.PRIVATE,
        )
        self.parent_lst = List.objects.create(
            title="Elternbeirat",
            email_alias="eb",
            template=self.template,
            visibility=List.Visibility.PRIVATE,
        )
        self.lst.parent = self.parent_lst
        self.lst.save()

        self.owner = _make_user(username="owner")
        self.record = ListRecord.objects.create(list=self.lst, subject=self.owner.person)
        RecordManager.objects.create(
            record=self.record, user=self.owner, basis=RecordManager.Basis.SELF_REGISTERED
        )

    def test_save_writes_values_and_access_rows(self):
        form = RecordEditForm(
            data={
                f"attr_{self.attr_name.pk}": "Anna Müller",
                f"attr_{self.attr_phone.pk}": "0123 456789",
                f"vis_{self.attr_phone.pk}": [f"list-{self.parent_lst.pk}"],
            },
            record=self.record,
            user=self.owner,
        )
        self.assertTrue(form.is_valid(), form.errors)
        form.save()

        name_val = ListRecordValue.objects.get(record=self.record, attribute=self.attr_name).value
        phone_val = ListRecordValue.objects.get(record=self.record, attribute=self.attr_phone).value
        self.assertEqual(name_val, "Anna Müller")
        self.assertEqual(phone_val, "0123 456789")

        # must_be_public attribute should NOT get visibility rows.
        self.assertFalse(
            ListRecordAccess.objects.filter(record=self.record, attribute=self.attr_name).exists()
        )
        # phone visibility row points at parent list.
        rows = ListRecordAccess.objects.filter(record=self.record, attribute=self.attr_phone)
        self.assertEqual(rows.count(), 1)
        self.assertEqual(rows.first().audience_id, self.parent_lst.pk)

    def test_save_replaces_existing_access_rows(self):
        ListRecordAccess.objects.create(
            record=self.record, attribute=self.attr_phone, audience=None
        )
        form = RecordEditForm(
            data={
                f"attr_{self.attr_name.pk}": "Anna",
                f"attr_{self.attr_phone.pk}": "111",
                f"vis_{self.attr_phone.pk}": [f"list-{self.lst.pk}"],
            },
            record=self.record,
            user=self.owner,
        )
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        rows = ListRecordAccess.objects.filter(record=self.record, attribute=self.attr_phone)
        self.assertEqual(rows.count(), 1)
        self.assertEqual(rows.first().audience_id, self.lst.pk)


class B3VisibilityWriteGateTests(TestCase):
    """B3: only RecordManagers / subject's-own-USER / super-admin may toggle
    the visibility matrix. A ListAdmin without a RecordManager row sees the
    matrix disabled and submitted vis_* values are dropped on save."""

    def setUp(self):
        self.template = ListTemplate.objects.create(name="Schulklasse")
        self.attr_phone = ListAttribute.objects.create(
            template=self.template, name="Telefon", type=ListAttribute.Type.PHONE, position=0
        )
        self.lst = List.objects.create(
            title="Klasse 5a",
            email_alias="5a",
            template=self.template,
            visibility=List.Visibility.PRIVATE,
        )
        self.parent_lst = List.objects.create(
            title="Elternbeirat",
            email_alias="eb",
            template=self.template,
            visibility=List.Visibility.PRIVATE,
        )
        self.lst.parent = self.parent_lst
        self.lst.save()

        self.owner = _make_user(username="owner")
        self.admin = _make_user(username="adminuser")
        self.super_ = _make_super()
        self.record = ListRecord.objects.create(list=self.lst, subject=self.owner.person)
        RecordManager.objects.create(
            record=self.record, user=self.owner, basis=RecordManager.Basis.SELF_REGISTERED
        )
        ListAdmin.objects.create(list=self.lst, user=self.admin)

        # Pre-existing visibility row: phone visible to parent-list members.
        self.preexisting_row = ListRecordAccess.objects.create(
            record=self.record, attribute=self.attr_phone, audience=self.parent_lst
        )
        ListRecordValue.objects.create(
            record=self.record, attribute=self.attr_phone, value="0123 original"
        )

    def test_admin_form_marks_vis_field_disabled(self):
        form = RecordEditForm(record=self.record, user=self.admin)
        self.assertFalse(form.user_can_edit_visibility)
        self.assertTrue(form.fields[f"vis_{self.attr_phone.pk}"].disabled)

    def test_owner_form_keeps_vis_field_editable(self):
        form = RecordEditForm(record=self.record, user=self.owner)
        self.assertTrue(form.user_can_edit_visibility)
        self.assertFalse(form.fields[f"vis_{self.attr_phone.pk}"].disabled)

    def test_super_admin_form_keeps_vis_field_editable(self):
        form = RecordEditForm(record=self.record, user=self.super_)
        self.assertTrue(form.user_can_edit_visibility)
        self.assertFalse(form.fields[f"vis_{self.attr_phone.pk}"].disabled)

    def test_admin_post_cannot_change_matrix_but_can_change_value(self):
        # The list-admin posts a value change AND tries to wipe the
        # phone-visibility (omits vis_phone from the payload).
        form = RecordEditForm(
            data={
                f"attr_{self.attr_phone.pk}": "0123 admin-edited",
                # vis_phone deliberately omitted ⇒ would normally delete rows.
            },
            record=self.record,
            user=self.admin,
        )
        self.assertTrue(form.is_valid(), form.errors)
        form.save()

        # Value: admin-edit went through.
        self.assertEqual(
            ListRecordValue.objects.get(
                record=self.record, attribute=self.attr_phone
            ).value,
            "0123 admin-edited",
        )
        # Matrix: pre-existing row survived untouched.
        rows = list(
            ListRecordAccess.objects.filter(record=self.record, attribute=self.attr_phone)
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].pk, self.preexisting_row.pk)
        self.assertEqual(rows[0].audience_id, self.parent_lst.pk)

    def test_admin_tampered_post_with_vis_payload_is_dropped(self):
        # Defense-in-depth: even if the admin tampers with the POST body and
        # submits vis_phone values, save() must not honour them.
        form = RecordEditForm(
            data={
                f"attr_{self.attr_phone.pk}": "0123 admin-edited",
                f"vis_{self.attr_phone.pk}": ["public"],  # tampered.
            },
            record=self.record,
            user=self.admin,
        )
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        rows = list(
            ListRecordAccess.objects.filter(record=self.record, attribute=self.attr_phone)
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].audience_id, self.parent_lst.pk)  # unchanged.

    def test_owner_can_still_change_matrix(self):
        form = RecordEditForm(
            data={
                f"attr_{self.attr_phone.pk}": "0123 owner-edit",
                f"vis_{self.attr_phone.pk}": [f"list-{self.lst.pk}"],
            },
            record=self.record,
            user=self.owner,
        )
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        rows = list(
            ListRecordAccess.objects.filter(record=self.record, attribute=self.attr_phone)
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].audience_id, self.lst.pk)


class InviteFlowTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.template = ListTemplate.objects.create(name="Schulklasse")
        self.attr = ListAttribute.objects.create(
            template=self.template, name="Name", type=ListAttribute.Type.TEXT, must_be_public=True
        )
        self.lst = List.objects.create(
            title="Elternvertreter",
            email_alias="elternvertreter",
            template=self.template,
            visibility=List.Visibility.PRIVATE,
        )
        self.admin_user = _make_user(username="adminuser")
        ListAdmin.objects.create(list=self.lst, user=self.admin_user)

        self.existing = _make_user(username="existing", given="Berta", family="S")
        self.other = _make_user(username="other", given="Carla", family="X")

    def _make_invite(self, *, target_person=None, target_email="newbie@example.test"):
        return ListInviteToken.objects.create(
            list=self.lst,
            invited_by=self.admin_user,
            target_email=target_email,
            target_person=target_person,
        )

    def test_expired_token_410(self):
        token = self._make_invite()
        token.expires_at = timezone.now() - timedelta(days=1)
        token.save(update_fields=["expires_at"])
        resp = self.client.get(reverse("lists:invite_accept", kwargs={"token": token.token}))
        self.assertEqual(resp.status_code, 410)

    def test_consumed_token_410(self):
        token = self._make_invite()
        token.consumed_at = timezone.now()
        token.save(update_fields=["consumed_at"])
        resp = self.client.get(reverse("lists:invite_accept", kwargs={"token": token.token}))
        self.assertEqual(resp.status_code, 410)

    def test_unauth_branch_a_redirects_to_register_with_email_prefill(self):
        token = self._make_invite(target_email="newbie@example.test")
        resp = self.client.get(reverse("lists:invite_accept", kwargs={"token": token.token}))
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/auth/register/", resp.url)
        self.assertIn("email=newbie%40example.test", resp.url)
        self.assertEqual(self.client.session.get("pending_invite_token"), token.token)

    def test_m7_email_with_plus_suffix_url_encoded_correctly(self):
        """M7: target_email may contain '+' (tag-suffix). Must end up as
        %2B in the query string, not as a literal '+' (which the receiving
        view would interpret as a space).
        """
        token = self._make_invite(target_email="user+tag@example.test")
        resp = self.client.get(reverse("lists:invite_accept", kwargs={"token": token.token}))
        self.assertEqual(resp.status_code, 302)
        self.assertIn("email=user%2Btag%40example.test", resp.url)
        self.assertNotIn("user+tag", resp.url)

    def test_existing_user_branch_one_click_join(self):
        token = self._make_invite(
            target_person=self.existing.person, target_email=self.existing.person.email or "x@x"
        )
        self.client.force_login(self.existing)
        resp = self.client.get(reverse("lists:invite_accept", kwargs={"token": token.token}))
        self.assertEqual(resp.status_code, 302)
        # Lands in record-edit for the newly-created record.
        record = ListRecord.objects.get(list=self.lst, subject=self.existing.person)
        self.assertIn(f"/lists/{self.lst.pk}/records/{record.pk}/edit/", resp.url)
        token.refresh_from_db()
        self.assertIsNotNone(token.consumed_at)
        # RecordManager with basis=invited is written.
        rm = RecordManager.objects.get(record=record, user=self.existing)
        self.assertEqual(rm.basis, RecordManager.Basis.INVITED)

    def test_wrong_user_branch_b_refused(self):
        token = self._make_invite(target_person=self.existing.person)
        self.client.force_login(self.other)
        resp = self.client.get(reverse("lists:invite_accept", kwargs={"token": token.token}))
        self.assertEqual(resp.status_code, 403)
        token.refresh_from_db()
        self.assertIsNone(token.consumed_at)

    def test_unauth_branch_b_redirects_to_login(self):
        token = self._make_invite(target_person=self.existing.person)
        resp = self.client.get(reverse("lists:invite_accept", kwargs={"token": token.token}))
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/auth/login/", resp.url)
        self.assertEqual(self.client.session.get("pending_invite_token"), token.token)

    def test_round_trip_for_branch_a_binds_target_person_after_login(self):
        """Simulates the not-yet-USER path: invite is issued blank, recipient
        registers (or in test: we just log in as someone), then re-hits
        invite_accept which should bind target_person to the now-logged-in
        user's Person and complete the join.
        """
        token = self._make_invite()  # target_person=NULL
        self.client.force_login(self.existing)
        resp = self.client.get(reverse("lists:invite_accept", kwargs={"token": token.token}))
        self.assertEqual(resp.status_code, 302)
        token.refresh_from_db()
        self.assertEqual(token.target_person_id, self.existing.person_id)
        self.assertIsNotNone(token.consumed_at)
        self.assertTrue(
            ListRecord.objects.filter(list=self.lst, subject=self.existing.person).exists()
        )

    def test_wrong_user_reason_text_does_not_leak_target_person_name(self):
        """B4: the 403 page must not mention the target person's name."""
        token = self._make_invite(target_person=self.existing.person)
        self.client.force_login(self.other)
        resp = self.client.get(reverse("lists:invite_accept", kwargs={"token": token.token}))
        self.assertEqual(resp.status_code, 403)
        body = resp.content.decode("utf-8")
        self.assertNotIn(self.existing.person.given_name, body)
        self.assertNotIn(self.existing.person.family_name, body)


class CriticalFindingFixesTests(TestCase):
    """Regression tests for the review findings B1, B2, B5."""

    def setUp(self):
        self.template = ListTemplate.objects.create(name="Schulklasse")
        self.attr_public = ListAttribute.objects.create(
            template=self.template, name="Name", type=ListAttribute.Type.TEXT, must_be_public=True
        )
        self.attr_private = ListAttribute.objects.create(
            template=self.template, name="Telefon", type=ListAttribute.Type.PHONE, position=1
        )
        self.lst = List.objects.create(
            title="Klasse 5a",
            email_alias="5a",
            template=self.template,
            visibility=List.Visibility.PRIVATE,
        )
        self.user = _make_user(
            username="subject_user", given="Anna", family="Müller", email="anna@example.test"
        )
        self.other = _make_user(username="other", given="Berta", family="X", email="berta@example.test")
        self.super_ = _make_super(username="b5_super")

    # --- B1 -----------------------------------------------------------------

    def test_b1_archived_record_does_not_block_new_active_record(self):
        archived = ListRecord.objects.create(list=self.lst, subject=self.user.person)
        archived.archived_at = timezone.now()
        archived.save(update_fields=["archived_at"])

        # Must not raise IntegrityError.
        new_record = ListRecord.objects.create(list=self.lst, subject=self.user.person)
        self.assertIsNotNone(new_record.pk)
        self.assertNotEqual(new_record.pk, archived.pk)

    # --- B2 -----------------------------------------------------------------

    def test_b2_subject_user_can_edit_without_record_manager_row(self):
        record = ListRecord.objects.create(list=self.lst, subject=self.user.person)
        # NO RecordManager row.
        from .permissions import can_user_edit_record

        self.assertTrue(can_user_edit_record(self.user, record))
        self.assertFalse(can_user_edit_record(self.other, record))

    def test_b2_subject_user_can_see_private_field_without_record_manager_row(self):
        record = ListRecord.objects.create(list=self.lst, subject=self.user.person)
        # NO RecordManager row, NO ListRecordAccess rows for the private attr.
        from .visibility import can_user_see_field

        self.assertTrue(can_user_see_field(self.user, record, self.attr_private))
        self.assertFalse(can_user_see_field(self.other, record, self.attr_private))

    def test_b2_archived_own_record_still_blocked_for_edit(self):
        record = ListRecord.objects.create(list=self.lst, subject=self.user.person)
        record.archived_at = timezone.now()
        record.save(update_fields=["archived_at"])
        from .permissions import can_user_edit_record

        self.assertFalse(can_user_edit_record(self.user, record))

    # --- B5 -----------------------------------------------------------------

    def test_b5_target_email_forced_to_person_email_when_person_picked(self):
        form = ListInviteForm(
            data={"target_email": "spoofed@evil.test", "target_person": self.other.person.pk},
            list_obj=self.lst,
            inviting_user=self.super_,
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["target_email"], self.other.person.email)

    def test_b5_target_email_required_when_no_person_picked(self):
        form = ListInviteForm(
            data={"target_email": ""}, list_obj=self.lst, inviting_user=self.super_
        )
        self.assertFalse(form.is_valid())

    def test_b5_person_without_email_excluded_from_queryset(self):
        no_email_person = Person.objects.create(given_name="No", family_name="Email")
        User.objects.create_user(person=no_email_person, username="no_email_user")
        form = ListInviteForm(list_obj=self.lst, inviting_user=self.super_)
        self.assertNotIn(no_email_person, form.fields["target_person"].queryset)
        self.assertIn(self.user.person, form.fields["target_person"].queryset)


class HeaderSanitizationTests(TestCase):
    """M9: list titles with embedded newlines must not produce
    BadHeaderError when used in mail Subject headers.
    """

    def setUp(self):
        self.template = ListTemplate.objects.create(name="Schulklasse")
        self.admin_user = _make_user(username="adminuser", email="admin@example.test")

    def test_clean_title_collapses_newlines_and_whitespace(self):
        form = ListCreateForm(
            data={
                "title": "Klasse 5a\r\nBcc: attacker@evil.test",
                "email_alias": "5a",
                "template": self.template.pk,
                "visibility": "private",
                "parent": "",
            },
            user=_make_super(username="m9_super"),
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["title"], "Klasse 5a Bcc: attacker@evil.test")
        self.assertNotIn("\n", form.cleaned_data["title"])
        self.assertNotIn("\r", form.cleaned_data["title"])

    def test_invite_email_with_newline_in_title_does_not_crash(self):
        """List with newline-bearing title (e.g. saved via Django admin bypassing
        the form-level clean) — the send_mail path must sanitize before
        handing off to the EmailMessage layer.
        """
        # Directly construct a List with a tainted title to simulate a
        # title that bypassed ListCreateForm.clean_title (e.g. via admin).
        lst = List.objects.create(
            title="Klasse 5a\r\nBcc: attacker@evil.test",
            email_alias="5a",
            template=self.template,
            visibility=List.Visibility.PRIVATE,
        )
        ListAdmin.objects.create(list=lst, user=self.admin_user)

        client = Client()
        client.force_login(self.admin_user)
        resp = client.post(
            reverse("lists:invite", kwargs={"pk": lst.pk}),
            data={"target_email": "newbie@example.test", "target_person": "", "mode": ""},
        )
        self.assertEqual(resp.status_code, 302, resp.content[:200])
        self.assertEqual(len(mail.outbox), 1)
        sent = mail.outbox[0]
        self.assertNotIn("\n", sent.subject)
        self.assertNotIn("\r", sent.subject)
        # Newline-bearing content collapses to a single line but stays in subject.
        self.assertIn("Bcc: attacker@evil.test", sent.subject)


class InvitePersonScopingTests(TestCase):
    """M6: candidate_invite_persons restricts the target_person dropdown
    to Persons the inviter already has business with — no full-installation
    enumeration.

    Topology used:

      Elternbeirat                           (top-level)
        ├── Klasse 5a                        (sub-list)
        │     └── Klassenfeier-Planung       (sub-sub-list)
        └── Klasse 5b                        (sub-list, inviter not in)

      Lehrerkollegium                        (separate top-level, inviter not in)

      Elternvertreter                        (the target list)
    """

    def setUp(self):
        self.template = ListTemplate.objects.create(name="Schulklasse")
        self.eb = List.objects.create(
            title="Elternbeirat",
            email_alias="eb",
            template=self.template,
            visibility=List.Visibility.PRIVATE,
        )
        self.k5a = List.objects.create(
            title="Klasse 5a",
            email_alias="5a",
            template=self.template,
            parent=self.eb,
            visibility=List.Visibility.PRIVATE,
        )
        self.k5b = List.objects.create(
            title="Klasse 5b",
            email_alias="5b",
            template=self.template,
            parent=self.eb,
            visibility=List.Visibility.PRIVATE,
        )
        self.feier = List.objects.create(
            title="Klassenfeier-Planung",
            email_alias="feier-5a",
            template=self.template,
            parent=self.k5a,
            visibility=List.Visibility.PRIVATE,
        )
        self.lehrer = List.objects.create(
            title="Lehrerkollegium",
            email_alias="lehrer",
            template=self.template,
            visibility=List.Visibility.PRIVATE,
        )
        self.elternvertreter = List.objects.create(
            title="Elternvertreter",
            email_alias="elternvertreter",
            template=self.template,
            visibility=List.Visibility.PRIVATE,
        )

        # Inviter is admin of "Klasse 5a" and member of "Elternbeirat".
        # Plus admin of the target list (Elternvertreter).
        self.inviter = _make_user(username="inviter", given="Inga", family="Vertreter", email="inga@x")
        ListAdmin.objects.create(list=self.k5a, user=self.inviter)
        ListAccess.objects.create(list=self.eb, user=self.inviter)
        ListAdmin.objects.create(list=self.elternvertreter, user=self.inviter)

        # Persons + records used as candidates.
        self.p_in_k5a = _make_user(
            username="parent_5a", given="Anna", family="K5a", email="anna@k5a.x"
        ).person
        self.p_in_k5b = _make_user(
            username="parent_5b", given="Berta", family="K5b", email="berta@k5b.x"
        ).person
        self.p_in_feier = _make_user(
            username="parent_feier", given="Carla", family="Feier", email="carla@feier.x"
        ).person
        self.p_in_eb = _make_user(
            username="member_eb", given="Dora", family="Eb", email="dora@eb.x"
        ).person
        self.p_in_lehrer = _make_user(
            username="lehrer", given="Erika", family="L", email="erika@lehrer.x"
        ).person
        self.p_unverbunden = _make_user(
            username="alone", given="Frank", family="Alone", email="frank@alone.x"
        ).person  # no Records anywhere

        ListRecord.objects.create(list=self.k5a, subject=self.p_in_k5a)
        ListRecord.objects.create(list=self.k5b, subject=self.p_in_k5b)
        ListRecord.objects.create(list=self.feier, subject=self.p_in_feier)
        ListRecord.objects.create(list=self.eb, subject=self.p_in_eb)
        ListRecord.objects.create(list=self.lehrer, subject=self.p_in_lehrer)

    def _candidate_pks(self, inviter, target):
        from .permissions import candidate_invite_persons

        return set(candidate_invite_persons(inviter, target).values_list("pk", flat=True))

    def test_inviter_sees_persons_from_visible_lists(self):
        # k5a: admin → visible. eb: member → visible. feier: child of k5a (but
        # inviter is admin of k5a, so eligible_parents_for already has k5a
        # via admin; feier is direct child of target? No, feier is child of
        # k5a, not target. So feier inclusion depends on inviter actually
        # being admin/member of feier — they're not — and feier is not parent
        # nor child of target. So feier is NOT visible from this inviter.
        pks = self._candidate_pks(self.inviter, self.elternvertreter)
        self.assertIn(self.p_in_k5a.pk, pks)
        self.assertIn(self.p_in_eb.pk, pks)

    def test_inviter_does_not_see_unrelated_lists_persons(self):
        pks = self._candidate_pks(self.inviter, self.elternvertreter)
        self.assertNotIn(self.p_in_k5b.pk, pks)
        self.assertNotIn(self.p_in_lehrer.pk, pks)
        self.assertNotIn(self.p_in_feier.pk, pks)
        self.assertNotIn(self.p_unverbunden.pk, pks)

    def test_target_subtree_widens_visibility(self):
        # If inviter is admin of Elternbeirat (target's *parent*-from-tree is
        # not modelled here, but Elternbeirat is parent of k5a so let's pick
        # Elternbeirat as the target instead). Then k5a + k5b (direct
        # children of target) become reachable.
        ListAdmin.objects.create(list=self.eb, user=self.inviter)
        pks = self._candidate_pks(self.inviter, self.eb)
        self.assertIn(self.p_in_k5a.pk, pks)
        self.assertIn(self.p_in_k5b.pk, pks)  # via target.children

    def test_super_admin_sees_all_user_persons_with_email(self):
        super_ = _make_super(username="m6_super")
        pks = self._candidate_pks(super_, self.elternvertreter)
        self.assertIn(self.p_in_k5a.pk, pks)
        self.assertIn(self.p_in_k5b.pk, pks)
        self.assertIn(self.p_in_lehrer.pk, pks)
        self.assertIn(self.p_unverbunden.pk, pks)  # super sees even unlinked

    def test_archived_record_does_not_grant_visibility(self):
        # If p_in_k5b's only record is archived, the inviter doesn't see them
        # — even if the inviter would have visibility via target.children.
        ListAdmin.objects.create(list=self.eb, user=self.inviter)
        rec = ListRecord.objects.get(list=self.k5b, subject=self.p_in_k5b)
        rec.archived_at = timezone.now()
        rec.save(update_fields=["archived_at"])
        pks = self._candidate_pks(self.inviter, self.eb)
        self.assertNotIn(self.p_in_k5b.pk, pks)

    def test_public_list_member_persons_are_visible_to_anyone(self):
        pub = List.objects.create(
            title="Förderverein",
            email_alias="fv",
            template=self.template,
            visibility=List.Visibility.PUBLIC_VISIBLE,
        )
        pub_person = _make_user(
            username="fv_member", given="Greta", family="FV", email="g@fv.x"
        ).person
        ListRecord.objects.create(list=pub, subject=pub_person)
        pks = self._candidate_pks(self.inviter, self.elternvertreter)
        self.assertIn(pub_person.pk, pks)
