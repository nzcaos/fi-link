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
    ListJoinToken,
    ListRecord,
    ListRecordAccess,
    ListRecordValue,
    ListTemplate,
    PersonRelationship,
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
from .visibility import (
    can_user_see_field,
    can_user_see_subject_name,
    visible_attributes_for,
)


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

    def test_n13_save_acquires_row_lock_on_record(self):
        """N13: save() must run inside a tx that holds a row-level lock on
        the record, otherwise two parallel POSTs race on the delete+insert
        of LIST_RECORD_ACCESS. We verify the lock indirectly by capturing
        the issued SQL — `SELECT ... FOR UPDATE` against lists_listrecord
        must be there.
        """
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        form = RecordEditForm(
            data={
                f"attr_{self.attr_name.pk}": "Anna",
                f"attr_{self.attr_phone.pk}": "0",
                f"vis_{self.attr_phone.pk}": [f"list-{self.lst.pk}"],
            },
            record=self.record,
            user=self.owner,
        )
        self.assertTrue(form.is_valid(), form.errors)
        with CaptureQueriesContext(connection) as ctx:
            form.save()
        locking = [
            q["sql"]
            for q in ctx.captured_queries
            if "lists_listrecord" in q["sql"].lower() and "for update" in q["sql"].lower()
        ]
        self.assertTrue(
            locking,
            "expected a SELECT ... FOR UPDATE on lists_listrecord in save()",
        )


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


class M8SubjectNameVisibilityTests(TestCase):
    """M8: subject-name as a second axis of the visibility matrix
    (`ListRecordAccess` rows with `attribute_id = NULL`). Default is public;
    list-admin and super-admin always see the real name; non-managers see
    `?N` when not in any granted audience."""

    def setUp(self):
        self.client = Client()
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

        self.owner = _make_user(username="owner", given="Anna", family="Müller")
        self.parent_member = _make_user(username="parentmember", given="Paul", family="Beirat")
        self.class_member = _make_user(username="classmember", given="Berta", family="Schmid")
        self.outsider = _make_user(username="outsider", given="Carla", family="X")
        self.admin = _make_user(username="adminuser", given="Dora", family="Y")
        self.super_ = _make_super()

        ListAccess.objects.create(list=self.parent_lst, user=self.parent_member)
        ListAccess.objects.create(list=self.lst, user=self.class_member)
        ListAdmin.objects.create(list=self.lst, user=self.admin)

        self.record = ListRecord.objects.create(list=self.lst, subject=self.owner.person)
        RecordManager.objects.create(
            record=self.record, user=self.owner, basis=RecordManager.Basis.SELF_REGISTERED
        )

    def test_default_name_visibility_row_created_by_signal(self):
        """Every newly created ListRecord should have one (NULL, NULL) access
        row written by the post_save signal — default = public."""
        rows = ListRecordAccess.objects.filter(
            record=self.record, attribute__isnull=True, audience__isnull=True
        )
        self.assertEqual(rows.count(), 1)

    def test_default_makes_name_visible_to_everyone(self):
        for u in (self.class_member, self.parent_member, self.outsider, self.admin, self.super_):
            self.assertTrue(
                can_user_see_subject_name(u, self.record),
                f"{u.username} should see the name by default",
            )

    def test_owner_always_sees_own_name(self):
        # Wipe all visibility rows — owner still sees the name (subject-own).
        ListRecordAccess.objects.filter(record=self.record).delete()
        self.assertTrue(can_user_see_subject_name(self.owner, self.record))

    def test_admin_and_super_override_matrix(self):
        # Remove the public default — admin and super-admin still see the name.
        ListRecordAccess.objects.filter(
            record=self.record, attribute__isnull=True
        ).delete()
        self.assertTrue(can_user_see_subject_name(self.admin, self.record))
        self.assertTrue(can_user_see_subject_name(self.super_, self.record))
        # Class-member without any audience-row no longer sees it.
        self.assertFalse(can_user_see_subject_name(self.class_member, self.record))

    def test_audience_restricted_name_visibility(self):
        # Owner sets name-visible to parent-list only.
        ListRecordAccess.objects.filter(
            record=self.record, attribute__isnull=True
        ).delete()
        ListRecordAccess.objects.create(
            record=self.record, attribute=None, audience=self.parent_lst
        )
        # parent_member is in the Elternbeirat list → sees real name.
        self.assertTrue(can_user_see_subject_name(self.parent_member, self.record))
        # class_member is only in the class list, not Elternbeirat → no.
        self.assertFalse(can_user_see_subject_name(self.class_member, self.record))

    def test_archived_record_hides_name_from_non_admins(self):
        ListRecordAccess.objects.filter(
            record=self.record, attribute__isnull=True
        ).delete()
        self.record.archived_at = timezone.now()
        self.record.save(update_fields=["archived_at"])
        # Admin still sees (override before archive check).
        self.assertTrue(can_user_see_subject_name(self.admin, self.record))
        # Subject's own user still sees their own.
        self.assertTrue(can_user_see_subject_name(self.owner, self.record))
        # Non-manager class-member sees nothing.
        self.assertFalse(can_user_see_subject_name(self.class_member, self.record))

    def test_record_edit_form_includes_name_visibility_field(self):
        form = RecordEditForm(record=self.record, user=self.owner)
        self.assertIn("vis_name", form.fields)
        # Default-public is reflected in initial = ["public"].
        self.assertEqual(list(form.fields["vis_name"].initial), ["public"])
        self.assertFalse(form.fields["vis_name"].disabled)

    def test_record_edit_form_disables_name_visibility_for_pure_admin(self):
        form = RecordEditForm(record=self.record, user=self.admin)
        self.assertTrue(form.fields["vis_name"].disabled)

    def test_form_save_writes_name_visibility_rows(self):
        form = RecordEditForm(
            data={
                f"attr_{self.attr_phone.pk}": "0123",
                "vis_name": [f"list-{self.parent_lst.pk}"],
                # vis_phone empty.
            },
            record=self.record,
            user=self.owner,
        )
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        rows = list(
            ListRecordAccess.objects.filter(record=self.record, attribute__isnull=True)
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].audience_id, self.parent_lst.pk)

    def test_form_save_drops_name_changes_for_pure_admin(self):
        form = RecordEditForm(
            data={
                f"attr_{self.attr_phone.pk}": "0123",
                "vis_name": [f"list-{self.parent_lst.pk}"],  # tampered.
            },
            record=self.record,
            user=self.admin,
        )
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        # The original (NULL, NULL) default-public row stays unchanged.
        rows = list(
            ListRecordAccess.objects.filter(record=self.record, attribute__isnull=True)
        )
        self.assertEqual(len(rows), 1)
        self.assertIsNone(rows[0].audience_id)

    def test_list_detail_renders_real_name_when_visible(self):
        # class_member sees the public-default name.
        self.client.force_login(self.class_member)
        resp = self.client.get(reverse("lists:detail", kwargs={"pk": self.lst.pk}))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Anna Müller")
        self.assertNotContains(resp, "?1")

    def test_list_detail_renders_anon_when_not_visible(self):
        ListRecordAccess.objects.filter(
            record=self.record, attribute__isnull=True
        ).delete()
        self.client.force_login(self.class_member)
        resp = self.client.get(reverse("lists:detail", kwargs={"pk": self.lst.pk}))
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, "Anna Müller")
        self.assertContains(resp, "?1")

    def test_list_detail_admin_still_sees_real_name_after_anon(self):
        ListRecordAccess.objects.filter(
            record=self.record, attribute__isnull=True
        ).delete()
        self.client.force_login(self.admin)
        resp = self.client.get(reverse("lists:detail", kwargs={"pk": self.lst.pk}))
        self.assertContains(resp, "Anna Müller")
        self.assertNotContains(resp, "?1")

    def test_list_detail_numbers_multiple_anon_rows(self):
        # Create a second record with another subject; anonymise both.
        other = _make_user(username="other_subject", given="Bruno", family="Beispiel")
        other_record = ListRecord.objects.create(list=self.lst, subject=other.person)
        ListRecordAccess.objects.filter(attribute__isnull=True).delete()

        self.client.force_login(self.class_member)
        resp = self.client.get(reverse("lists:detail", kwargs={"pk": self.lst.pk}))
        self.assertEqual(resp.status_code, 200)
        # Both names hidden.
        self.assertNotContains(resp, "Anna Müller")
        self.assertNotContains(resp, "Bruno Beispiel")
        # Both ?-counters present.
        self.assertContains(resp, "?1")
        self.assertContains(resp, "?2")
        # The pair survived the loop with distinct identifiers.
        del other_record  # silence linter; the record's existence is enough.


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

        # Family names must be distinctive enough that assertNotIn against
        # rendered HTML is meaningful — single letters like "S" appear in
        # base.html stylesheet/nav text and produce false-positive leaks.
        self.existing = _make_user(username="existing", given="Berta", family="Schneeberger")
        self.other = _make_user(username="other", given="Carla", family="Xanderlein")

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
        resp = self.client.post(reverse("lists:invite_accept", kwargs={"token": token.token}))
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
        resp = self.client.post(reverse("lists:invite_accept", kwargs={"token": token.token}))
        self.assertEqual(resp.status_code, 302)
        self.assertIn("email=user%2Btag%40example.test", resp.url)
        self.assertNotIn("user+tag", resp.url)

    def test_existing_user_branch_one_click_join(self):
        token = self._make_invite(
            target_person=self.existing.person, target_email=self.existing.person.email or "x@x"
        )
        self.client.force_login(self.existing)
        resp = self.client.post(reverse("lists:invite_accept", kwargs={"token": token.token}))
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
        resp = self.client.post(reverse("lists:invite_accept", kwargs={"token": token.token}))
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
        resp = self.client.post(reverse("lists:invite_accept", kwargs={"token": token.token}))
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

    # --- M10: GET must be side-effect-free (auto-preview safety) -------------

    def test_m10_get_does_not_consume_token_when_authenticated_match(self):
        """M10: An auto-preview-fetcher hitting the URL with the recipient's
        cookies (rare but possible) must NOT consume the token. GET shows
        a confirmation page; consumption requires an explicit POST.
        """
        token = self._make_invite(
            target_person=self.existing.person,
            target_email=self.existing.person.email or "x@x",
        )
        self.client.force_login(self.existing)
        resp = self.client.get(reverse("lists:invite_accept", kwargs={"token": token.token}))
        self.assertEqual(resp.status_code, 200)
        token.refresh_from_db()
        self.assertIsNone(token.consumed_at)
        self.assertFalse(
            ListRecord.objects.filter(list=self.lst, subject=self.existing.person).exists()
        )

    def test_m10_get_does_not_bind_target_person_for_blank_invite(self):
        """M10: For a blank-target invite, GET while authenticated must NOT
        rebind target_person — otherwise a preview-fetcher in an authenticated
        browser would silently take over the invite.
        """
        token = self._make_invite()  # target_person=NULL
        self.client.force_login(self.existing)
        resp = self.client.get(reverse("lists:invite_accept", kwargs={"token": token.token}))
        self.assertEqual(resp.status_code, 200)
        token.refresh_from_db()
        self.assertIsNone(token.target_person_id)
        self.assertIsNone(token.consumed_at)

    def test_m10_get_does_not_write_session_for_unauth(self):
        """M10: GET by an unauthenticated visitor must not seed
        `pending_invite_token` in the session. That happens only on POST.
        """
        token = self._make_invite(target_person=self.existing.person)
        resp = self.client.get(reverse("lists:invite_accept", kwargs={"token": token.token}))
        self.assertEqual(resp.status_code, 200)
        self.assertIsNone(self.client.session.get("pending_invite_token"))

    def test_m10_get_confirm_page_for_blank_invite_unauth(self):
        token = self._make_invite(target_email="newbie@example.test")
        resp = self.client.get(reverse("lists:invite_accept", kwargs={"token": token.token}))
        self.assertEqual(resp.status_code, 200)
        # Page advertises register-and-join; no DB write performed.
        self.assertContains(resp, "registrieren")
        token.refresh_from_db()
        self.assertIsNone(token.consumed_at)

    def test_m10_get_410_for_expired_no_side_effects(self):
        """Expired/consumed tokens still render the problem page; GET safety
        is the priority — the POST should not be reachable via the same URL.
        """
        token = self._make_invite()
        token.expires_at = timezone.now() - timedelta(days=1)
        token.save(update_fields=["expires_at"])
        # GET → 410
        self.assertEqual(
            self.client.get(reverse("lists:invite_accept", kwargs={"token": token.token})).status_code,
            410,
        )
        # POST → also 410, no consumption
        self.assertEqual(
            self.client.post(reverse("lists:invite_accept", kwargs={"token": token.token})).status_code,
            410,
        )
        token.refresh_from_db()
        self.assertIsNone(token.consumed_at)


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


class M11RecordCreateModeCheckTests(TestCase):
    """M11: record_create_self must refuse on `via_associate` templates —
    the endpoint's "subject = request.user.person" assumption only holds
    for `self`-mode lists. The full associate-wizard lands in Phase 3b-2.
    """

    def setUp(self):
        self.client = Client()
        self.tmpl_self = ListTemplate.objects.create(
            name="VHS-Kurs",
            member_subject_mode=ListTemplate.MemberSubjectMode.SELF,
        )
        self.tmpl_associate = ListTemplate.objects.create(
            name="Schulklasse",
            member_subject_mode=ListTemplate.MemberSubjectMode.VIA_ASSOCIATE,
        )
        self.lst_self = List.objects.create(
            title="Töpfern-Kurs",
            email_alias="toepfern",
            template=self.tmpl_self,
            visibility=List.Visibility.PUBLIC_EDITABLE,
        )
        self.lst_associate = List.objects.create(
            title="Klasse 5a",
            email_alias="5a",
            template=self.tmpl_associate,
            visibility=List.Visibility.PUBLIC_EDITABLE,
        )
        self.user = _make_user(username="parent", given="Eva", family="P")

    def test_self_mode_creates_record_and_redirects_to_edit(self):
        self.client.force_login(self.user)
        resp = self.client.get(
            reverse("lists:record_create_self", kwargs={"pk": self.lst_self.pk})
        )
        self.assertEqual(resp.status_code, 302)
        record = ListRecord.objects.get(list=self.lst_self, subject=self.user.person)
        self.assertEqual(record.role, ListRecord.Role.MEMBER)
        self.assertIn(f"/lists/{self.lst_self.pk}/records/{record.pk}/edit/", resp.url)

    def test_via_associate_mode_refuses(self):
        self.client.force_login(self.user)
        resp = self.client.get(
            reverse("lists:record_create_self", kwargs={"pk": self.lst_associate.pk})
        )
        self.assertEqual(resp.status_code, 403)
        self.assertFalse(
            ListRecord.objects.filter(
                list=self.lst_associate, subject=self.user.person
            ).exists()
        )

    def test_via_associate_detail_page_hides_self_create_button(self):
        """Defense-in-depth at the UI layer: the 'Mich eintragen' button must
        not appear for via_associate lists, so users do not hit the 403 by
        following a button they should never have seen.
        """
        self.client.force_login(self.user)
        resp = self.client.get(
            reverse("lists:detail", kwargs={"pk": self.lst_associate.pk})
        )
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, "Mich eintragen")

    def test_self_detail_page_still_shows_self_create_button(self):
        self.client.force_login(self.user)
        resp = self.client.get(
            reverse("lists:detail", kwargs={"pk": self.lst_self.pk})
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Mich eintragen")


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


class QRJoinTokenAdminTests(TestCase):
    """Phase 3b-2 / QR-Code-Onboarding: admin-side token management."""

    def setUp(self):
        self.client = Client()
        self.template = ListTemplate.objects.create(
            name="VHS-Kurs",
            member_subject_mode=ListTemplate.MemberSubjectMode.SELF,
        )
        self.lst = List.objects.create(
            title="Töpfern-Kurs",
            email_alias="toepfern",
            template=self.template,
            visibility=List.Visibility.PRIVATE,
        )
        self.admin_user = _make_user(username="admin")
        ListAdmin.objects.create(list=self.lst, user=self.admin_user)
        self.member = _make_user(username="member")
        ListAccess.objects.create(list=self.lst, user=self.member)
        self.outsider = _make_user(username="outsider")

    def test_admin_can_create_token(self):
        self.client.force_login(self.admin_user)
        resp = self.client.post(
            reverse("lists:join_tokens", kwargs={"pk": self.lst.pk})
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(ListJoinToken.objects.filter(list=self.lst).count(), 1)

    def test_non_admin_cannot_view_token_page(self):
        self.client.force_login(self.member)
        resp = self.client.get(
            reverse("lists:join_tokens", kwargs={"pk": self.lst.pk})
        )
        self.assertEqual(resp.status_code, 403)

    def test_non_admin_cannot_create_token(self):
        self.client.force_login(self.outsider)
        resp = self.client.post(
            reverse("lists:join_tokens", kwargs={"pk": self.lst.pk})
        )
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(ListJoinToken.objects.count(), 0)

    def test_listing_shows_qr_url(self):
        ListJoinToken.objects.create(list=self.lst, created_by=self.admin_user)
        self.client.force_login(self.admin_user)
        resp = self.client.get(
            reverse("lists:join_tokens", kwargs={"pk": self.lst.pk})
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "/lists/join/")

    def test_revoke_sets_revoked_at(self):
        tok = ListJoinToken.objects.create(list=self.lst, created_by=self.admin_user)
        self.client.force_login(self.admin_user)
        resp = self.client.post(
            reverse(
                "lists:revoke_join_token",
                kwargs={"pk": self.lst.pk, "token": tok.token},
            )
        )
        self.assertEqual(resp.status_code, 302)
        tok.refresh_from_db()
        self.assertIsNotNone(tok.revoked_at)
        self.assertTrue(tok.is_revoked)

    def test_revoke_get_refused(self):
        tok = ListJoinToken.objects.create(list=self.lst, created_by=self.admin_user)
        self.client.force_login(self.admin_user)
        resp = self.client.get(
            reverse(
                "lists:revoke_join_token",
                kwargs={"pk": self.lst.pk, "token": tok.token},
            )
        )
        self.assertEqual(resp.status_code, 403)
        tok.refresh_from_db()
        self.assertIsNone(tok.revoked_at)

    def test_revoke_by_non_admin_refused(self):
        tok = ListJoinToken.objects.create(list=self.lst, created_by=self.admin_user)
        self.client.force_login(self.outsider)
        resp = self.client.post(
            reverse(
                "lists:revoke_join_token",
                kwargs={"pk": self.lst.pk, "token": tok.token},
            )
        )
        self.assertEqual(resp.status_code, 403)
        tok.refresh_from_db()
        self.assertIsNone(tok.revoked_at)


class QRJoinClickFlowTests(TestCase):
    """Phase 3b-2 / QR-Code-Onboarding: click-handler side. Self-mode only —
    via_associate path lands in sub-phase B once the wizard URL exists.
    """

    def setUp(self):
        self.client = Client()
        self.template = ListTemplate.objects.create(
            name="VHS-Kurs",
            member_subject_mode=ListTemplate.MemberSubjectMode.SELF,
        )
        self.lst = List.objects.create(
            title="Töpfern-Kurs",
            email_alias="toepfern",
            template=self.template,
            visibility=List.Visibility.PRIVATE,
        )
        self.admin_user = _make_user(username="admin")
        ListAdmin.objects.create(list=self.lst, user=self.admin_user)
        self.visitor = _make_user(username="visitor", given="Veit", family="V")
        self.tok = ListJoinToken.objects.create(
            list=self.lst, created_by=self.admin_user
        )

    # --- GET side-effect-freeness (same M10 pattern as invite_accept) -------

    def test_get_unauth_shows_confirm_page_no_session_write(self):
        resp = self.client.get(
            reverse("lists:join_via_token", kwargs={"token": self.tok.token})
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "registrieren")
        self.assertIsNone(self.client.session.get("pending_join_token"))

    def test_get_auth_shows_confirm_page_no_record_yet(self):
        self.client.force_login(self.visitor)
        resp = self.client.get(
            reverse("lists:join_via_token", kwargs={"token": self.tok.token})
        )
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(
            ListRecord.objects.filter(list=self.lst, subject=self.visitor.person).exists()
        )

    # --- 410 paths ----------------------------------------------------------

    def test_revoked_token_410(self):
        self.tok.revoked_at = timezone.now()
        self.tok.save(update_fields=["revoked_at"])
        resp = self.client.get(
            reverse("lists:join_via_token", kwargs={"token": self.tok.token})
        )
        self.assertEqual(resp.status_code, 410)

    def test_expired_token_410(self):
        self.tok.expires_at = timezone.now() - timedelta(hours=1)
        self.tok.save(update_fields=["expires_at"])
        resp = self.client.get(
            reverse("lists:join_via_token", kwargs={"token": self.tok.token})
        )
        self.assertEqual(resp.status_code, 410)

    def test_archived_list_410(self):
        self.lst.archived_at = timezone.now()
        self.lst.save(update_fields=["archived_at"])
        resp = self.client.get(
            reverse("lists:join_via_token", kwargs={"token": self.tok.token})
        )
        self.assertEqual(resp.status_code, 410)

    # --- POST commit paths --------------------------------------------------

    def test_post_unauth_stashes_session_and_redirects_to_register(self):
        resp = self.client.post(
            reverse("lists:join_via_token", kwargs={"token": self.tok.token})
        )
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/auth/register/", resp.url)
        self.assertEqual(
            self.client.session.get("pending_join_token"), self.tok.token
        )

    def test_post_auth_self_mode_creates_record_and_record_manager(self):
        self.client.force_login(self.visitor)
        resp = self.client.post(
            reverse("lists:join_via_token", kwargs={"token": self.tok.token})
        )
        self.assertEqual(resp.status_code, 302)
        record = ListRecord.objects.get(list=self.lst, subject=self.visitor.person)
        self.assertEqual(record.role, ListRecord.Role.MEMBER)
        self.assertIn(f"/records/{record.pk}/edit/", resp.url)
        rm = RecordManager.objects.get(record=record, user=self.visitor)
        self.assertEqual(rm.basis, RecordManager.Basis.SELF_REGISTERED)

    def test_post_auth_self_mode_idempotent(self):
        """Scanning the QR twice yields the same record, no duplicates."""
        self.client.force_login(self.visitor)
        first = self.client.post(
            reverse("lists:join_via_token", kwargs={"token": self.tok.token})
        )
        second = self.client.post(
            reverse("lists:join_via_token", kwargs={"token": self.tok.token})
        )
        self.assertEqual(first.status_code, 302)
        self.assertEqual(second.status_code, 302)
        self.assertEqual(
            ListRecord.objects.filter(
                list=self.lst, subject=self.visitor.person, archived_at__isnull=True
            ).count(),
            1,
        )
        # Same target record either way.
        self.assertEqual(first.url, second.url)

    def test_token_remains_usable_after_a_join(self):
        """Multi-use semantics: one user joining must NOT consume the token."""
        self.client.force_login(self.visitor)
        self.client.post(
            reverse("lists:join_via_token", kwargs={"token": self.tok.token})
        )
        self.tok.refresh_from_db()
        self.assertTrue(self.tok.is_usable)
        self.assertIsNone(self.tok.revoked_at)


class AssociateWizardTests(TestCase):
    """Phase 3b-2 / via_associate-Wizard: single-page form covering Person,
    ListRecord, PersonRelationship, RecordManager and ListAccess in one POST.
    """

    def setUp(self):
        self.client = Client()
        self.template = ListTemplate.objects.create(
            name="Schulklasse",
            member_subject_mode=ListTemplate.MemberSubjectMode.VIA_ASSOCIATE,
            relationship_roles=["Mutter von", "Vater von", "Erziehungsberechtigte von"],
        )
        self.attr_name = ListAttribute.objects.create(
            template=self.template,
            name="Klassen-Name",
            type=ListAttribute.Type.TEXT,
            must_be_public=True,
        )
        self.attr_phone = ListAttribute.objects.create(
            template=self.template,
            name="Telefon",
            type=ListAttribute.Type.PHONE,
            position=1,
        )
        self.lst = List.objects.create(
            title="Klasse 5a",
            email_alias="5a",
            template=self.template,
            visibility=List.Visibility.PRIVATE,
        )
        self.admin_user = _make_user(username="admin")
        ListAdmin.objects.create(list=self.lst, user=self.admin_user)
        self.parent = _make_user(
            username="parent",
            given="Eva",
            family="Mueller",
            email="eva@example.test",
        )
        ListAccess.objects.create(list=self.lst, user=self.parent)
        self.outsider = _make_user(username="outsider")

    def _valid_post_data(self, **overrides):
        data = {
            "given_name": "Lina",
            "family_name": "Mueller",
            "member_email": "",
            "role": "Mutter von",
            f"attr_{self.attr_name.pk}": "5a",
            f"attr_{self.attr_phone.pk}": "+49 123 456",
        }
        data.update(overrides)
        return data

    # --- Permission gates ---------------------------------------------------

    def test_refused_on_self_mode_list(self):
        self_tmpl = ListTemplate.objects.create(
            name="Self-Tmpl",
            member_subject_mode=ListTemplate.MemberSubjectMode.SELF,
        )
        self_lst = List.objects.create(
            title="Self", email_alias="self", template=self_tmpl
        )
        self.client.force_login(self.parent)
        resp = self.client.get(
            reverse("lists:record_create_associate", kwargs={"pk": self_lst.pk})
        )
        self.assertEqual(resp.status_code, 403)

    def test_refused_on_archived_list(self):
        self.lst.archived_at = timezone.now()
        self.lst.save(update_fields=["archived_at"])
        self.client.force_login(self.parent)
        resp = self.client.get(
            reverse("lists:record_create_associate", kwargs={"pk": self.lst.pk})
        )
        self.assertEqual(resp.status_code, 403)

    def test_refused_for_user_without_list_access(self):
        self.client.force_login(self.outsider)
        resp = self.client.get(
            reverse("lists:record_create_associate", kwargs={"pk": self.lst.pk})
        )
        self.assertEqual(resp.status_code, 403)

    def test_allowed_with_wizard_grant_session_marker(self):
        """Fresh QR-visitor on a private list: no ListAccess yet, but the
        QR-handler stashed `wizard_grant_list_id` in the session.
        """
        self.client.force_login(self.outsider)
        session = self.client.session
        session["wizard_grant_list_id"] = self.lst.pk
        session.save()
        resp = self.client.get(
            reverse("lists:record_create_associate", kwargs={"pk": self.lst.pk})
        )
        self.assertEqual(resp.status_code, 200)

    # --- Form behaviour -----------------------------------------------------

    def test_role_choices_come_from_template(self):
        self.client.force_login(self.parent)
        resp = self.client.get(
            reverse("lists:record_create_associate", kwargs={"pk": self.lst.pk})
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Mutter von")
        self.assertContains(resp, "Vater von")
        self.assertContains(resp, "Erziehungsberechtigte von")

    def test_post_creates_full_associate_record_atomic(self):
        self.client.force_login(self.parent)
        resp = self.client.post(
            reverse("lists:record_create_associate", kwargs={"pk": self.lst.pk}),
            data=self._valid_post_data(),
        )
        self.assertEqual(resp.status_code, 302)

        # Child PERSON exists, NO USER linked.
        child = Person.objects.get(given_name="Lina", family_name="Mueller")
        self.assertFalse(User.objects.filter(person=child).exists())
        # Record exists, role=member, subject=child.
        record = ListRecord.objects.get(list=self.lst, subject=child)
        self.assertEqual(record.role, ListRecord.Role.MEMBER)
        # PersonRelationship: subject=child, related=parent.person, role chosen.
        rel = PersonRelationship.objects.get(
            subject_person=child, related_person=self.parent.person
        )
        self.assertEqual(rel.role, "Mutter von")
        # RecordManager: basis=guardian on the parent USER.
        rm = RecordManager.objects.get(record=record, user=self.parent)
        self.assertEqual(rm.basis, RecordManager.Basis.GUARDIAN)
        # ListRecordValue rows for both attributes.
        values = {
            v.attribute_id: v.value
            for v in ListRecordValue.objects.filter(record=record)
        }
        self.assertEqual(values[self.attr_name.pk], "5a")
        self.assertEqual(values[self.attr_phone.pk], "+49 123 456")
        # Redirect lands in record-edit for refinement.
        self.assertIn(f"/records/{record.pk}/edit/", resp.url)

    def test_session_marker_consumed_after_save(self):
        self.client.force_login(self.outsider)
        session = self.client.session
        session["wizard_grant_list_id"] = self.lst.pk
        session.save()
        self.client.post(
            reverse("lists:record_create_associate", kwargs={"pk": self.lst.pk}),
            data=self._valid_post_data(given_name="Tom"),
        )
        # Outsider also receives ListAccess as a side-effect of the save.
        self.assertTrue(
            ListAccess.objects.filter(list=self.lst, user=self.outsider).exists()
        )
        # Session marker is consumed so a second visit must re-qualify.
        self.assertNotIn("wizard_grant_list_id", self.client.session)

    def test_associate_can_add_multiple_children(self):
        """A parent with two kids in the same class runs the wizard twice."""
        self.client.force_login(self.parent)
        self.client.post(
            reverse("lists:record_create_associate", kwargs={"pk": self.lst.pk}),
            data=self._valid_post_data(given_name="Lina"),
        )
        self.client.post(
            reverse("lists:record_create_associate", kwargs={"pk": self.lst.pk}),
            data=self._valid_post_data(given_name="Tom", role="Mutter von"),
        )
        self.assertEqual(
            ListRecord.objects.filter(list=self.lst, role=ListRecord.Role.MEMBER).count(),
            2,
        )
        # One ListAccess row total (get_or_create is idempotent).
        self.assertEqual(
            ListAccess.objects.filter(list=self.lst, user=self.parent).count(),
            1,
        )

    def test_validation_error_does_not_create_anything(self):
        self.client.force_login(self.parent)
        data = self._valid_post_data(given_name="")  # required field missing
        resp = self.client.post(
            reverse("lists:record_create_associate", kwargs={"pk": self.lst.pk}),
            data=data,
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(Person.objects.filter(family_name="Mueller").count(), 1)  # only the parent
        self.assertFalse(ListRecord.objects.filter(list=self.lst).exists())
        self.assertFalse(PersonRelationship.objects.exists())


class Phase3b2E2ETests(TestCase):
    """Phase 3b-2 / End-to-end Smoke: drive a visitor through more than one
    endpoint to validate that the wiring between QR-click, the wizard
    redirect, and the eventual record-edit landing actually fits together.

    These overlap intentionally with QRJoinClickFlowTests and
    AssociateWizardTests — those validate the units in isolation, this
    validates that the units compose.
    """

    def setUp(self):
        self.client = Client()
        self.self_tmpl = ListTemplate.objects.create(
            name="VHS-Kurs",
            member_subject_mode=ListTemplate.MemberSubjectMode.SELF,
        )
        self.assoc_tmpl = ListTemplate.objects.create(
            name="Schulklasse",
            member_subject_mode=ListTemplate.MemberSubjectMode.VIA_ASSOCIATE,
            relationship_roles=["Mutter von", "Vater von"],
        )
        ListAttribute.objects.create(
            template=self.assoc_tmpl,
            name="Klassen-Name",
            type=ListAttribute.Type.TEXT,
            must_be_public=True,
        )
        self.self_list = List.objects.create(
            title="Töpfern-Kurs",
            email_alias="toepfern",
            template=self.self_tmpl,
            visibility=List.Visibility.PRIVATE,
        )
        self.assoc_list = List.objects.create(
            title="Klasse 5a",
            email_alias="5a",
            template=self.assoc_tmpl,
            visibility=List.Visibility.PRIVATE,
        )
        self.admin_user = _make_user(username="admin")
        ListAdmin.objects.create(list=self.self_list, user=self.admin_user)
        ListAdmin.objects.create(list=self.assoc_list, user=self.admin_user)
        self.visitor = _make_user(
            username="visitor", given="Veit", family="Vogel", email="v@example.test"
        )

    # --- Self-mode QR end-to-end --------------------------------------------

    def test_self_qr_full_flow_unauth_to_record(self):
        """Unauth visitor scans → register-roundtrip → auth POST → record."""
        tok = ListJoinToken.objects.create(
            list=self.self_list, created_by=self.admin_user
        )
        join_url = reverse("lists:join_via_token", kwargs={"token": tok.token})

        # 1) Unauth POST stashes pending and bounces to register.
        resp = self.client.post(join_url)
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/auth/register/", resp.url)
        self.assertEqual(self.client.session.get("pending_join_token"), tok.token)

        # 2) After successful auth, accounts._next_url_after_auth would route
        # back to join_url and pop the session marker. The accounts-layer
        # plumbing is exercised by the Phase 2 / accounts test suite — here
        # we simulate the outcome (user is now logged in, on the join URL)
        # without driving the full WebAuthn ceremony.
        self.client.force_login(self.visitor)

        # 3) Authenticated POST commits the join.
        resp = self.client.post(join_url)
        self.assertEqual(resp.status_code, 302)
        record = ListRecord.objects.get(
            list=self.self_list, subject=self.visitor.person
        )
        self.assertIn(f"/records/{record.pk}/edit/", resp.url)
        self.assertEqual(record.role, ListRecord.Role.MEMBER)
        self.assertTrue(
            RecordManager.objects.filter(record=record, user=self.visitor).exists()
        )

    # --- via_associate QR end-to-end ----------------------------------------

    def test_associate_qr_full_flow_lands_in_wizard(self):
        """Auth visitor scans QR on via_associate list → POST → redirect to
        wizard with session marker set → wizard renders → wizard POST writes
        full triade (Person + Record + Relationship + Manager + Access).
        """
        tok = ListJoinToken.objects.create(
            list=self.assoc_list, created_by=self.admin_user
        )
        join_url = reverse("lists:join_via_token", kwargs={"token": tok.token})
        wizard_url = reverse(
            "lists:record_create_associate", kwargs={"pk": self.assoc_list.pk}
        )

        self.client.force_login(self.visitor)
        # 1) Auth POST on the join handler → redirect to the wizard.
        resp = self.client.post(join_url)
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, wizard_url)
        self.assertEqual(
            self.client.session.get("wizard_grant_list_id"), self.assoc_list.pk
        )

        # 2) Wizard GET renders for the visitor even though they have no
        # ListAccess yet (the session marker grants access).
        resp = self.client.get(wizard_url)
        self.assertEqual(resp.status_code, 200)

        # 3) Wizard POST writes the full triade.
        attr_pk = ListAttribute.objects.get(template=self.assoc_tmpl).pk
        resp = self.client.post(
            wizard_url,
            data={
                "given_name": "Kim",
                "family_name": "Vogel",
                "member_email": "",
                "role": "Mutter von",
                f"attr_{attr_pk}": "5a",
            },
        )
        self.assertEqual(resp.status_code, 302)
        child = Person.objects.get(given_name="Kim", family_name="Vogel")
        record = ListRecord.objects.get(list=self.assoc_list, subject=child)
        self.assertEqual(record.role, ListRecord.Role.MEMBER)
        self.assertTrue(
            PersonRelationship.objects.filter(
                subject_person=child,
                related_person=self.visitor.person,
                role="Mutter von",
            ).exists()
        )
        rm = RecordManager.objects.get(record=record, user=self.visitor)
        self.assertEqual(rm.basis, RecordManager.Basis.GUARDIAN)
        self.assertTrue(
            ListAccess.objects.filter(list=self.assoc_list, user=self.visitor).exists()
        )
        # Session marker consumed.
        self.assertNotIn("wizard_grant_list_id", self.client.session)
        # And the QR remains usable for the next visitor.
        tok.refresh_from_db()
        self.assertTrue(tok.is_usable)

    def test_associate_qr_revoked_token_blocks_full_flow(self):
        tok = ListJoinToken.objects.create(
            list=self.assoc_list, created_by=self.admin_user
        )
        tok.revoked_at = timezone.now()
        tok.save(update_fields=["revoked_at"])
        self.client.force_login(self.visitor)
        resp = self.client.post(
            reverse("lists:join_via_token", kwargs={"token": tok.token})
        )
        self.assertEqual(resp.status_code, 410)
        self.assertNotIn("wizard_grant_list_id", self.client.session)
        self.assertFalse(PersonRelationship.objects.exists())

    # --- accounts._next_url_after_auth handoff ------------------------------

    def test_next_url_after_auth_routes_pending_join_token(self):
        """Verifies the accounts-side handoff added in this sub-phase:
        a pending_join_token in the session routes back to join_via_token
        after a successful register/login, and is popped in the process.
        Pending_invite_token takes precedence when both are present.
        """
        from types import SimpleNamespace

        from accounts.views import _next_url_after_auth

        # `_next_url_after_auth` only calls `request.session.pop(...)` so a
        # plain dict is a sufficient mock; this avoids the (fragile) dance
        # of grafting a real Django SessionStore onto a RequestFactory req.
        def _req(**session):
            return SimpleNamespace(session=dict(session))

        # Case 1: only pending_join_token → routes to join_via_token.
        req = _req(pending_join_token="jointoken123")
        url = _next_url_after_auth(req, default="/lists/")
        self.assertEqual(
            url, reverse("lists:join_via_token", kwargs={"token": "jointoken123"})
        )
        self.assertNotIn("pending_join_token", req.session)

        # Case 2: only pending_invite_token → routes to invite_accept.
        req = _req(pending_invite_token="invitetoken456")
        url = _next_url_after_auth(req, default="/lists/")
        self.assertEqual(
            url, reverse("lists:invite_accept", kwargs={"token": "invitetoken456"})
        )
        self.assertNotIn("pending_invite_token", req.session)

        # Case 3: nothing → default.
        req = _req()
        url = _next_url_after_auth(req, default="/lists/")
        self.assertEqual(url, "/lists/")

        # Case 4: both present → invite wins, join is left behind for the
        # next ceremony (invite owns this round).
        req = _req(pending_invite_token="I", pending_join_token="J")
        url = _next_url_after_auth(req, default="/lists/")
        self.assertIn("invite/I", url)
        self.assertNotIn("pending_invite_token", req.session)
        self.assertEqual(req.session.get("pending_join_token"), "J")


# ---------------------------------------------------------------------------
# Phase 4 — Outbound mail
# ---------------------------------------------------------------------------


from smtplib import SMTPException
from unittest.mock import patch

from django.test import override_settings

from .forms import TestSendForm
from .models import OutboundMessage
from .tasks import (
    MAX_SEND_ATTEMPTS,
    alias_address,
    build_outbound_email,
    enqueue_list_fanout,
    list_address,
    list_recipient_emails,
    send_outbound_message,
)


@override_settings(
    MAIL_DOMAIN="caos.cloud",
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    DEFAULT_FROM_EMAIL="Fichtelink <noreply@caos.cloud>",
    RP_ORIGIN="https://fichtelink.caos.cloud",
)
class OutboundHelpersTests(TestCase):
    """Pure-helper tests: alias address shape, list address, header builder."""

    def setUp(self):
        self.template = ListTemplate.objects.create(name="Schulklasse")
        self.lst = List.objects.create(
            title="Klasse 5a",
            email_alias="5a",
            template=self.template,
            visibility=List.Visibility.PRIVATE,
        )

    def test_alias_address_shape(self):
        self.assertEqual(
            alias_address("bounce", "TOK"), "bounce-TOK@caos.cloud"
        )
        self.assertEqual(alias_address("alias", "TOK"), "alias-TOK@caos.cloud")

    def test_alias_address_rejects_unknown_kind(self):
        with self.assertRaises(ValueError):
            alias_address("evil", "T")

    def test_list_address_uses_email_alias_and_domain(self):
        self.assertEqual(list_address(self.lst), "5a@caos.cloud")

    def test_build_outbound_email_sets_envelope_and_headers(self):
        om = OutboundMessage.objects.create(
            list=self.lst,
            message_id="abc@caos.cloud",
            alias_token="TOK",
            from_email="sender@example.org",
            recipient_email="member@example.org",
            subject="Hallo Liste",
            anonymized_from=False,
        )
        msg = build_outbound_email(om, body="Inhalt")
        # Envelope-from goes to the bounce alias so DSNs route back to a token.
        self.assertEqual(msg.from_email, "bounce-TOK@caos.cloud")
        # Visible From: header is the original sender for non-anonymised mail.
        self.assertEqual(msg.extra_headers["From"], "sender@example.org")
        self.assertEqual(msg.to, ["member@example.org"])
        self.assertEqual(msg.extra_headers["Message-ID"], "<abc@caos.cloud>")
        self.assertEqual(msg.extra_headers["List-Id"], "<5a.caos.cloud>")
        self.assertEqual(msg.extra_headers["List-Post"], "<mailto:5a@caos.cloud>")
        self.assertEqual(
            msg.extra_headers["List-Unsubscribe"],
            f"<https://fichtelink.caos.cloud/lists/{self.lst.pk}/>",
        )
        self.assertEqual(msg.extra_headers["Auto-Submitted"], "auto-generated")
        self.assertEqual(msg.extra_headers["Precedence"], "list")
        self.assertEqual(msg.body, "Inhalt")
        self.assertEqual(msg.subject, "Hallo Liste")

    def test_build_outbound_email_anonymised_swaps_visible_from(self):
        om = OutboundMessage.objects.create(
            list=self.lst,
            message_id="abc@caos.cloud",
            alias_token="TOK2",
            from_email="sender@example.org",
            recipient_email="r@example.org",
            subject="Anon",
            anonymized_from=True,
        )
        msg = build_outbound_email(om, body="")
        # Envelope still goes to the bounce alias…
        self.assertEqual(msg.from_email, "bounce-TOK2@caos.cloud")
        # …but the visible From: is the alias too — original sender hidden.
        self.assertEqual(msg.extra_headers["From"], "alias-TOK2@caos.cloud")

    def test_message_message_id_header_round_trips_via_django(self):
        """Django's EmailMessage.message() must not strip our Message-ID."""
        om = OutboundMessage.objects.create(
            list=self.lst,
            message_id="m1@caos.cloud",
            alias_token="MID",
            from_email="x@y.z",
            recipient_email="r@y.z",
            subject="s",
        )
        msg = build_outbound_email(om, body="b")
        rendered = msg.message()
        self.assertEqual(rendered["From"], "x@y.z")
        self.assertEqual(rendered["Message-ID"], "<m1@caos.cloud>")
        self.assertEqual(rendered["List-Id"], "<5a.caos.cloud>")


@override_settings(
    MAIL_DOMAIN="caos.cloud",
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    DEFAULT_FROM_EMAIL="Fichtelink <noreply@caos.cloud>",
    RP_ORIGIN="https://fichtelink.caos.cloud",
)
class OutboundFanoutTests(TestCase):
    """Recipient resolution + transactional defer behaviour."""

    def setUp(self):
        self.template = ListTemplate.objects.create(name="Schulklasse")
        self.lst = List.objects.create(
            title="Klasse 5a",
            email_alias="5a",
            template=self.template,
            visibility=List.Visibility.PRIVATE,
        )
        # Two members with email, one without, one admin-only (no ListAccess).
        self.m1 = _make_user(username="m1", email="m1@example.org")
        self.m2 = _make_user(username="m2", email="m2@example.org")
        self.m_no_email = _make_user(username="m3", email=None)
        self.admin_only = _make_user(username="adm", email="adm@example.org")
        ListAccess.objects.create(list=self.lst, user=self.m1)
        ListAccess.objects.create(list=self.lst, user=self.m2)
        ListAccess.objects.create(list=self.lst, user=self.m_no_email)
        ListAdmin.objects.create(list=self.lst, user=self.admin_only)

    def test_recipients_excludes_persons_without_email(self):
        emails = set(list_recipient_emails(self.lst))
        self.assertEqual(emails, {"m1@example.org", "m2@example.org"})

    def test_recipients_does_not_include_admin_only_users(self):
        # admin_only is a ListAdmin but NOT in ListAccess — must not receive.
        emails = list_recipient_emails(self.lst)
        self.assertNotIn("adm@example.org", emails)

    def test_recipients_dedup_on_shared_family_email(self):
        # Two Users sharing a PERSON.email (the family-mailbox case).
        shared = "family@example.org"
        u_father = _make_user(username="father", email=shared)
        u_mother = _make_user(username="mother", email=shared)
        ListAccess.objects.create(list=self.lst, user=u_father)
        ListAccess.objects.create(list=self.lst, user=u_mother)
        emails = list_recipient_emails(self.lst)
        # PERSON.email is shared; distinct() keeps a single delivery target.
        self.assertEqual(emails.count(shared), 1)

    def test_enqueue_fanout_creates_one_row_per_recipient(self):
        with patch.object(send_outbound_message, "defer") as mocked:
            with self.captureOnCommitCallbacks(execute=True):
                created = enqueue_list_fanout(
                    list_obj=self.lst,
                    from_email="sender@example.org",
                    subject="Hi",
                    body="Inhalt",
                )
        self.assertEqual(len(created), 2)
        self.assertEqual(OutboundMessage.objects.filter(list=self.lst).count(), 2)
        # One defer per row, body forwarded verbatim.
        self.assertEqual(mocked.call_count, 2)
        deferred_ids = {call.kwargs["outbound_id"] for call in mocked.call_args_list}
        self.assertEqual(deferred_ids, {om.pk for om in created})
        for call in mocked.call_args_list:
            self.assertEqual(call.kwargs["body"], "Inhalt")

    def test_enqueue_fanout_defers_only_on_commit(self):
        """A rolled-back enqueue must NOT defer tasks, otherwise the worker
        would chase OutboundMessage rows that don't exist.

        Wrapped in `captureOnCommitCallbacks(execute=True)` so the test
        actually exercises the on-commit path — without the wrapper the outer
        TestCase transaction would suppress on_commit unconditionally and the
        assertion would pass for the wrong reason.
        """
        from django.db import transaction

        with patch.object(send_outbound_message, "defer") as mocked:
            with self.captureOnCommitCallbacks(execute=True):
                try:
                    with transaction.atomic():
                        enqueue_list_fanout(
                            list_obj=self.lst,
                            from_email="sender@example.org",
                            subject="Hi",
                            body="Inhalt",
                        )
                        raise RuntimeError("force rollback")
                except RuntimeError:
                    pass
        mocked.assert_not_called()
        # Rows are rolled back too.
        self.assertEqual(OutboundMessage.objects.filter(list=self.lst).count(), 0)

    def test_enqueue_fanout_sanitises_subject_and_from(self):
        with patch.object(send_outbound_message, "defer"):
            with self.captureOnCommitCallbacks(execute=True):
                created = enqueue_list_fanout(
                    list_obj=self.lst,
                    from_email="sender@example.org",
                    subject="line one\r\nline two",
                    body="body",
                )
        for om in created:
            self.assertNotIn("\n", om.subject)
            self.assertNotIn("\r", om.subject)
            # Whitespace collapsed → single-line value.
            self.assertEqual(om.subject, "line one line two")

    def test_enqueue_fanout_empty_recipients_does_not_defer(self):
        empty_list = List.objects.create(
            title="leer", email_alias="leer", template=self.template,
            visibility=List.Visibility.PRIVATE,
        )
        with patch.object(send_outbound_message, "defer") as mocked:
            with self.captureOnCommitCallbacks(execute=True):
                created = enqueue_list_fanout(
                    list_obj=empty_list,
                    from_email="x@y.z",
                    subject="s",
                    body="b",
                )
        self.assertEqual(created, [])
        mocked.assert_not_called()


@override_settings(
    MAIL_DOMAIN="caos.cloud",
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    DEFAULT_FROM_EMAIL="Fichtelink <noreply@caos.cloud>",
    RP_ORIGIN="https://fichtelink.caos.cloud",
)
class SendOutboundMessageTests(TestCase):
    """The synchronous task body: SMTP send, status transitions, retries."""

    def setUp(self):
        self.template = ListTemplate.objects.create(name="Schulklasse")
        self.lst = List.objects.create(
            title="Klasse 5a",
            email_alias="5a",
            template=self.template,
            visibility=List.Visibility.PRIVATE,
        )
        self.om = OutboundMessage.objects.create(
            list=self.lst,
            message_id="m1@caos.cloud",
            alias_token="TOK",
            from_email="sender@example.org",
            recipient_email="recipient@example.org",
            subject="Test",
        )

    def _run_task(self, om_pk: int, body: str = "body") -> None:
        """Invoke the task body synchronously, skipping the procrastinate queue."""
        send_outbound_message.func(outbound_id=om_pk, body=body)

    def test_success_marks_sent_and_writes_outbox(self):
        mail.outbox = []
        self._run_task(self.om.pk, body="Hallo")
        self.om.refresh_from_db()
        self.assertEqual(self.om.status, OutboundMessage.Status.SENT)
        self.assertEqual(self.om.attempts, 1)
        self.assertIsNotNone(self.om.sent_at)
        self.assertEqual(self.om.last_error, "")
        self.assertEqual(len(mail.outbox), 1)
        sent = mail.outbox[0]
        self.assertEqual(sent.from_email, "bounce-TOK@caos.cloud")
        self.assertEqual(sent.extra_headers["From"], "sender@example.org")
        self.assertEqual(sent.to, ["recipient@example.org"])
        self.assertEqual(sent.subject, "Test")
        self.assertEqual(sent.body, "Hallo")

    def test_idempotent_on_already_sent(self):
        self.om.status = OutboundMessage.Status.SENT
        self.om.save(update_fields=["status"])
        mail.outbox = []
        self._run_task(self.om.pk)
        # Nothing sent, no attempt counter bump.
        self.assertEqual(len(mail.outbox), 0)
        self.om.refresh_from_db()
        self.assertEqual(self.om.attempts, 0)

    def test_smtp_failure_keeps_pending_and_records_error(self):
        mail.outbox = []
        with patch(
            "lists.tasks.EmailMessage.send",
            side_effect=SMTPException("server temporarily unavailable"),
        ):
            with self.assertRaises(SMTPException):
                self._run_task(self.om.pk)
        self.om.refresh_from_db()
        self.assertEqual(self.om.status, OutboundMessage.Status.PENDING)
        self.assertEqual(self.om.attempts, 1)
        self.assertIn("server temporarily unavailable", self.om.last_error)
        self.assertIsNone(self.om.sent_at)
        self.assertEqual(len(mail.outbox), 0)

    def test_final_attempt_failure_marks_failed(self):
        # Pre-set attempts so this call is the last one allowed.
        self.om.attempts = MAX_SEND_ATTEMPTS - 1
        self.om.save(update_fields=["attempts"])
        with patch(
            "lists.tasks.EmailMessage.send",
            side_effect=SMTPException("permanent"),
        ):
            with self.assertRaises(SMTPException):
                self._run_task(self.om.pk)
        self.om.refresh_from_db()
        self.assertEqual(self.om.status, OutboundMessage.Status.FAILED)
        self.assertEqual(self.om.attempts, MAX_SEND_ATTEMPTS)

    def test_retries_then_succeeds(self):
        mail.outbox = []
        # First call fails with SMTPException, second succeeds.
        call_count = {"n": 0}
        real_send = mail.EmailMessage.send

        def flaky_send(self_msg, *a, **kw):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise SMTPException("transient")
            return real_send(self_msg, *a, **kw)

        with patch("lists.tasks.EmailMessage.send", flaky_send):
            with self.assertRaises(SMTPException):
                self._run_task(self.om.pk)
            # Procrastinate would re-invoke later; simulate that directly.
            self._run_task(self.om.pk)

        self.om.refresh_from_db()
        self.assertEqual(self.om.status, OutboundMessage.Status.SENT)
        self.assertEqual(self.om.attempts, 2)
        # Error field is cleared on success.
        self.assertEqual(self.om.last_error, "")
        self.assertEqual(len(mail.outbox), 1)


@override_settings(
    MAIL_DOMAIN="caos.cloud",
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    DEFAULT_FROM_EMAIL="Fichtelink <noreply@caos.cloud>",
    RP_ORIGIN="https://fichtelink.caos.cloud",
)
class TestSendRouteTests(TestCase):
    """The /lists/<id>/send-test/ super-admin endpoint."""

    def setUp(self):
        self.template = ListTemplate.objects.create(name="Schulklasse")
        self.lst = List.objects.create(
            title="Klasse 5a",
            email_alias="5a",
            template=self.template,
            visibility=List.Visibility.PRIVATE,
        )
        self.member = _make_user(username="m1", email="m1@example.org")
        ListAccess.objects.create(list=self.lst, user=self.member)
        self.regular_admin = _make_user(username="adm", email="adm@example.org")
        ListAdmin.objects.create(list=self.lst, user=self.regular_admin)
        self.super_ = _make_super()
        self.client = Client()

    def _url(self, pk: int | None = None) -> str:
        return reverse("lists:send_test", kwargs={"pk": pk or self.lst.pk})

    def test_anonymous_redirected_to_login(self):
        resp = self.client.get(self._url())
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/auth/login/", resp.url)

    def test_regular_member_forbidden(self):
        self.client.force_login(self.member)
        resp = self.client.get(self._url())
        self.assertEqual(resp.status_code, 403)

    def test_list_admin_forbidden(self):
        """A list-admin who is not super-admin is still blocked — test-send
        bypasses the inbound anti-spoof gate, so only super-admin may use it."""
        self.client.force_login(self.regular_admin)
        resp = self.client.get(self._url())
        self.assertEqual(resp.status_code, 403)

    def test_super_admin_gets_form(self):
        self.client.force_login(self.super_)
        resp = self.client.get(self._url())
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Betreff")
        # Recipient preview includes the one member with email.
        self.assertContains(resp, "m1@example.org")

    def test_post_creates_row_per_recipient_and_dispatches(self):
        self.client.force_login(self.super_)
        with patch.object(send_outbound_message, "defer") as mocked:
            with self.captureOnCommitCallbacks(execute=True):
                resp = self.client.post(
                    self._url(),
                    data={"subject": "Test", "body": "Hallo Klasse"},
                )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(
            resp.url, reverse("lists:detail", kwargs={"pk": self.lst.pk})
        )
        rows = OutboundMessage.objects.filter(list=self.lst)
        self.assertEqual(rows.count(), 1)
        om = rows.first()
        self.assertEqual(om.recipient_email, "m1@example.org")
        self.assertEqual(om.subject, "Test")
        mocked.assert_called_once()
        self.assertEqual(mocked.call_args.kwargs["outbound_id"], om.pk)
        self.assertEqual(mocked.call_args.kwargs["body"], "Hallo Klasse")

    def test_post_archived_list_forbidden(self):
        self.lst.archived_at = timezone.now()
        self.lst.save(update_fields=["archived_at"])
        self.client.force_login(self.super_)
        resp = self.client.post(
            self._url(),
            data={"subject": "Test", "body": "x"},
        )
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(OutboundMessage.objects.count(), 0)

    def test_form_rejects_empty_subject(self):
        form = TestSendForm(data={"subject": "   ", "body": "x"})
        self.assertFalse(form.is_valid())
        self.assertIn("subject", form.errors)

    def test_form_rejects_empty_body(self):
        form = TestSendForm(data={"subject": "s", "body": "   "})
        self.assertFalse(form.is_valid())
        self.assertIn("body", form.errors)
