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
from . import lifecycle
from .forms import AssociateWizardForm, ListCreateForm, ListInviteForm, RecordEditForm
from .models import (
    AdminInviteToken,
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
    PendingTransfer,
    PersonRelationship,
    RecordManager,
)
from .permissions import (
    accessible_lists_for,
    can_user_admin_list,
    can_user_create_sublist_under,
    can_user_create_top_level_list,
    can_user_edit_record,
    can_user_see_list,
    eligible_parents_for,
    resolve_default_list,
)
from .visibility import (
    build_composed_rows,
    member_grid_header,
    build_visible_rows,
    can_user_see_field,
    can_user_see_subject_email,
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


class BuildVisibleRowsTests(TestCase):
    """N12: build_visible_rows must produce the same per-field/per-name result
    as the single-record helpers, but in a constant number of queries
    independent of the record count.
    """

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
        self.audience_lst = List.objects.create(
            title="Elternbeirat",
            email_alias="eb",
            template=self.template,
            visibility=List.Visibility.PRIVATE,
        )
        self.member = _make_user(username="m", given="Berta", family="S")
        self.outsider = _make_user(username="o", given="Carla", family="X")
        ListAccess.objects.create(list=self.audience_lst, user=self.member)

    def _add_record(self, idx: int, *, phone_audience=None, name_public=True):
        person = Person.objects.create(given_name=f"Kind{idx}", family_name="Z")
        record = ListRecord.objects.create(list=self.lst, subject=person)
        # ListRecord.save() auto-creates a (record, attribute=NULL, audience=NULL)
        # public name row (M8 default). Drop it when the test wants the name
        # anonymised for non-managers.
        if not name_public:
            ListRecordAccess.objects.filter(record=record, attribute=None).delete()
        ListRecordValue.objects.create(
            record=record, attribute=self.attr_phone, value=f"0170-{idx}"
        )
        if phone_audience is not None:
            ListRecordAccess.objects.create(
                record=record, attribute=self.attr_phone, audience=phone_audience
            )
        return record

    def test_rows_match_single_record_helpers(self):
        self._add_record(1, phone_audience=self.audience_lst, name_public=True)
        self._add_record(2, name_public=False)  # name anonymised, phone hidden

        for viewer in (self.member, self.outsider):
            rows = build_visible_rows(viewer, self.lst)
            self.assertEqual(len(rows), 2)
            for row in rows:
                record = row["record"]
                expected_attrs = [
                    a for a in self.template.attributes.all()
                    if can_user_see_field(viewer, record, a)
                ]
                self.assertEqual(
                    [a.pk for a, _ in row["fields"]],
                    [a.pk for a in expected_attrs],
                )
                name_visible = can_user_see_subject_name(viewer, record)
                if name_visible:
                    self.assertEqual(row["subject_display"], str(record.subject))
                else:
                    self.assertTrue(row["subject_display"].startswith("?"))

    def test_anonymisation_counter_is_sequential(self):
        self._add_record(1, name_public=False)
        self._add_record(2, name_public=False)
        rows = build_visible_rows(self.outsider, self.lst)
        self.assertEqual([r["subject_display"] for r in rows], ["?1", "?2"])

    def test_query_count_is_constant_in_record_count(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        for i in range(3):
            self._add_record(i, phone_audience=self.audience_lst)
        with CaptureQueriesContext(connection) as small:
            build_visible_rows(self.member, self.lst)
        small_count = len(small)

        for i in range(3, 15):
            self._add_record(i, phone_audience=self.audience_lst)
        with CaptureQueriesContext(connection) as large:
            build_visible_rows(self.member, self.lst)

        # 3 records vs 15 records → identical query count (no N+1).
        self.assertEqual(len(large), small_count)


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
                f"vis_{self.attr_phone.pk}": ["public"],
            },
            record=self.record,
            user=self.owner,
        )
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        rows = ListRecordAccess.objects.filter(record=self.record, attribute=self.attr_phone)
        self.assertEqual(rows.count(), 1)
        self.assertIsNone(rows.first().audience_id)

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
                f"vis_{self.attr_phone.pk}": ["public"],
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
                f"vis_{self.attr_phone.pk}": ["public"],
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
        self.assertIsNone(rows[0].audience_id)


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
        row written by the post_save signal — default = public, sentinel=name.
        No email-sentinel row (parent email is opt-in / starts hidden)."""
        rows = ListRecordAccess.objects.filter(
            record=self.record, attribute__isnull=True, audience__isnull=True
        )
        self.assertEqual(rows.count(), 1)
        self.assertEqual(rows.first().sentinel, ListRecordAccess.Sentinel.NAME)
        self.assertFalse(
            ListRecordAccess.objects.filter(
                record=self.record, sentinel=ListRecordAccess.Sentinel.EMAIL
            ).exists()
        )

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
            record=self.record,
            attribute=None,
            sentinel=ListRecordAccess.Sentinel.NAME,
            audience=self.parent_lst,
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


class SubjectEmailVisibilityTests(TestCase):
    """Multi-person row: the subject PERSON's account email as a second
    subject-level sentinel (`ListRecordAccess` rows with `attribute_id = NULL`,
    `sentinel = "email"`). Unlike the name sentinel it starts HIDDEN (opt-in,
    no default public row); owner/manager/list-admin/super-admin always see it;
    audience grants disclose it per Benutzergruppe."""

    def setUp(self):
        self.client = Client()
        self.template = ListTemplate.objects.create(name="Schulklasse")
        self.attr_phone = ListAttribute.objects.create(
            template=self.template, name="Telefon", type=ListAttribute.Type.PHONE, position=0
        )
        self.lst = List.objects.create(
            title="Klasse 5a", email_alias="5a", template=self.template,
            visibility=List.Visibility.PRIVATE,
        )
        self.parent_lst = List.objects.create(
            title="Elternbeirat", email_alias="eb", template=self.template,
            visibility=List.Visibility.PRIVATE,
        )
        self.lst.parent = self.parent_lst
        self.lst.save()

        # owner's PERSON carries an email (the addressable parent).
        self.owner = _make_user(
            username="owner", given="Anna", family="Müller", email="anna@example.org"
        )
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

    def _grant_email(self, audience):
        return ListRecordAccess.objects.create(
            record=self.record,
            attribute=None,
            sentinel=ListRecordAccess.Sentinel.EMAIL,
            audience=audience,
        )

    def test_no_default_email_row_created(self):
        self.assertFalse(
            ListRecordAccess.objects.filter(
                record=self.record, sentinel=ListRecordAccess.Sentinel.EMAIL
            ).exists()
        )

    def test_email_hidden_by_default_for_non_privileged(self):
        # No email row → only owner/manager/admin/super see it.
        self.assertTrue(can_user_see_subject_email(self.owner, self.record))
        self.assertTrue(can_user_see_subject_email(self.admin, self.record))
        self.assertTrue(can_user_see_subject_email(self.super_, self.record))
        self.assertFalse(can_user_see_subject_email(self.class_member, self.record))
        self.assertFalse(can_user_see_subject_email(self.outsider, self.record))

    def test_email_visible_to_granted_audience_only(self):
        self._grant_email(self.parent_lst)
        # parent_member is in the Elternbeirat list → sees the email.
        self.assertTrue(can_user_see_subject_email(self.parent_member, self.record))
        # class_member is only in the class list → does not.
        self.assertFalse(can_user_see_subject_email(self.class_member, self.record))

    def test_public_email_visible_to_everyone(self):
        self._grant_email(None)
        self.assertTrue(can_user_see_subject_email(self.class_member, self.record))
        self.assertTrue(can_user_see_subject_email(self.outsider, self.record))

    def test_build_visible_rows_exposes_email_only_when_granted(self):
        # Without a grant: subject_email is None for a non-privileged viewer.
        rows = build_visible_rows(self.class_member, self.lst)
        self.assertEqual(len(rows), 1)
        self.assertIsNone(rows[0]["subject_email"])
        # After a public grant: the email surfaces.
        self._grant_email(None)
        rows = build_visible_rows(self.class_member, self.lst)
        self.assertEqual(rows[0]["subject_email"], "anna@example.org")

    def test_name_and_email_sentinels_are_independent(self):
        """Regression: the two attribute=NULL sentinels must not collapse onto
        one prefetch key. Granting the email publicly while the name stays
        hidden must show the email but anonymise the name (and vice versa)."""
        # Drop the default public name row → name hidden; grant email public.
        ListRecordAccess.objects.filter(
            record=self.record, attribute__isnull=True
        ).delete()
        self._grant_email(None)
        rows = build_visible_rows(self.class_member, self.lst)
        self.assertEqual(rows[0]["subject_display"], "?1")  # name anonymised
        self.assertEqual(rows[0]["subject_email"], "anna@example.org")  # email shown
        self.assertFalse(can_user_see_subject_name(self.class_member, self.record))
        self.assertTrue(can_user_see_subject_email(self.class_member, self.record))

    def test_record_edit_form_includes_email_field_when_subject_has_email(self):
        form = RecordEditForm(record=self.record, user=self.owner)
        self.assertTrue(form.has_email_vis)
        self.assertIn("vis_email", form.fields)
        self.assertEqual(list(form.fields["vis_email"].initial), [])  # hidden by default

    def test_record_edit_form_omits_email_field_when_subject_has_no_email(self):
        child = Person.objects.create(given_name="Kind", family_name="Müller")  # no email
        child_record = ListRecord.objects.create(
            list=self.lst, subject=child, role=ListRecord.Role.MEMBER
        )
        form = RecordEditForm(record=child_record, user=self.owner)
        self.assertFalse(form.has_email_vis)
        self.assertNotIn("vis_email", form.fields)

    def test_form_save_writes_email_visibility_rows(self):
        form = RecordEditForm(
            data={
                f"attr_{self.attr_phone.pk}": "0123",
                "vis_name": ["public"],
                "vis_email": [f"list-{self.parent_lst.pk}"],
            },
            record=self.record,
            user=self.owner,
        )
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        rows = list(
            ListRecordAccess.objects.filter(
                record=self.record, sentinel=ListRecordAccess.Sentinel.EMAIL
            )
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].audience_id, self.parent_lst.pk)

    def test_form_save_drops_email_changes_for_pure_admin(self):
        # B3: a list-admin without RecordManager row cannot set email visibility.
        form = RecordEditForm(
            data={
                f"attr_{self.attr_phone.pk}": "0123",
                "vis_email": [f"list-{self.parent_lst.pk}"],  # tampered.
            },
            record=self.record,
            user=self.admin,
        )
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        self.assertFalse(
            ListRecordAccess.objects.filter(
                record=self.record, sentinel=ListRecordAccess.Sentinel.EMAIL
            ).exists()
        )


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
        # Accepting the invite joins the Benutzergruppe (see the self-join
        # paths): required to see a private list and receive its mail.
        self.assertTrue(ListAccess.objects.filter(list=self.lst, user=self.existing).exists())

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


class InviteViaAssociateRoutingTests(TestCase):
    """A LIST_INVITE_TOKEN on a `via_associate` list must route the accepting
    USER into the associate wizard (declare child = member, own associate
    record, optional 2nd parent) — NOT create a bare associate record with
    subject=invitee. Regression for the 'invitee gets list access but never
    appears in the per-child rows, and there's no way to add the child or the
    other parent' bug. Token consumption is deferred to the wizard save, so an
    abandoned wizard leaves the invite reusable and creates no phantom records.
    """

    def setUp(self):
        self.client = Client()
        self.template = ListTemplate.objects.create(
            name="Schulklasse",
            member_subject_mode=ListTemplate.MemberSubjectMode.VIA_ASSOCIATE,
            relationship_roles=["Mutter von", "Vater von"],
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
            username="parent", given="Eva", family="Mueller", email="eva@example.test"
        )

    def _invite(self, *, target_person=None, target_email="eva@example.test"):
        return ListInviteToken.objects.create(
            list=self.lst,
            invited_by=self.admin_user,
            target_email=target_email,
            target_person=target_person,
            mode=ListInviteToken.Mode.VIA_ASSOCIATE,
        )

    def _wizard_post_data(self, **overrides):
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

    def test_existing_user_invite_routes_to_wizard_without_consuming(self):
        token = self._invite(target_person=self.parent.person)
        self.client.force_login(self.parent)
        resp = self.client.post(
            reverse("lists:invite_accept", kwargs={"token": token.token})
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(
            resp.url,
            reverse("lists:record_create_associate", kwargs={"pk": self.lst.pk}),
        )
        # No bare associate record for the invitee — the bug we are fixing.
        self.assertFalse(
            ListRecord.objects.filter(
                list=self.lst, subject=self.parent.person
            ).exists()
        )
        # Token NOT consumed yet; session carries the grant + deferred token.
        token.refresh_from_db()
        self.assertIsNone(token.consumed_at)
        self.assertEqual(self.client.session.get("wizard_grant_list_id"), self.lst.pk)
        self.assertEqual(self.client.session.get("wizard_invite_token"), token.token)

    def test_round_trip_branch_a_via_associate_routes_to_wizard(self):
        """Blank-target invite (not-yet-USER path, simulated by logging in after
        the click): binds target_person, then routes to the wizard rather than
        creating a member record for the parent.
        """
        token = self._invite()  # target_person=NULL
        self.client.force_login(self.parent)
        resp = self.client.post(
            reverse("lists:invite_accept", kwargs={"token": token.token})
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(
            resp.url,
            reverse("lists:record_create_associate", kwargs={"pk": self.lst.pk}),
        )
        token.refresh_from_db()
        self.assertEqual(token.target_person_id, self.parent.person_id)
        self.assertIsNone(token.consumed_at)
        self.assertFalse(ListRecord.objects.filter(list=self.lst).exists())

    def test_wizard_save_consumes_invite_and_builds_child_row(self):
        token = self._invite(target_person=self.parent.person)
        self.client.force_login(self.parent)
        # Step 1: accept → routed to wizard, markers set.
        self.client.post(reverse("lists:invite_accept", kwargs={"token": token.token}))
        # Step 2: submit the wizard.
        resp = self.client.post(
            reverse("lists:record_create_associate", kwargs={"pk": self.lst.pk}),
            self._wizard_post_data(),
        )
        self.assertEqual(resp.status_code, 302)
        # Child member record now exists with the entered name.
        child = ListRecord.objects.get(list=self.lst, role=ListRecord.Role.MEMBER)
        self.assertEqual(child.subject.given_name, "Lina")
        # The invited parent has their own associate record, linked to the child.
        self.assertTrue(
            ListRecord.objects.filter(
                list=self.lst,
                subject=self.parent.person,
                role=ListRecord.Role.ASSOCIATE,
            ).exists()
        )
        self.assertTrue(
            PersonRelationship.objects.filter(
                subject_person=child.subject, related_person=self.parent.person
            ).exists()
        )
        # Parent joined the Benutzergruppe and is a manager of the child.
        self.assertTrue(
            ListAccess.objects.filter(list=self.lst, user=self.parent).exists()
        )
        # Token consumed on save; session markers cleared.
        token.refresh_from_db()
        self.assertIsNotNone(token.consumed_at)
        self.assertNotIn("wizard_grant_list_id", self.client.session)
        self.assertNotIn("wizard_invite_token", self.client.session)

    def test_abandoned_wizard_leaves_invite_reusable(self):
        token = self._invite(target_person=self.parent.person)
        self.client.force_login(self.parent)
        self.client.post(reverse("lists:invite_accept", kwargs={"token": token.token}))
        # The user never submits the wizard → token must stay unconsumed.
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
        # Self-registration joins the Benutzergruppe (see _join_self_mode).
        self.assertTrue(ListAccess.objects.filter(list=self.lst_self, user=self.user).exists())

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


class N14MailFailureTests(TestCase):
    """N14: an SMTP failure during a transactional invite mail must not become a
    500. The token row stays valid and the admin gets an error message with the
    link so they can relay it out-of-band.
    """

    def setUp(self):
        import smtplib
        self.SMTPException = smtplib.SMTPException
        self.template = ListTemplate.objects.create(name="Schulklasse")
        self.admin_user = _make_user(username="adminuser", email="admin@example.test")
        self.lst = List.objects.create(
            title="Klasse 5a",
            email_alias="5a",
            template=self.template,
            visibility=List.Visibility.PRIVATE,
        )
        ListAdmin.objects.create(list=self.lst, user=self.admin_user)
        self.client = Client()
        self.client.force_login(self.admin_user)

    def test_invite_smtp_failure_does_not_500_and_keeps_token(self):
        from unittest.mock import patch

        with patch(
            "lists.views.send_mail",
            side_effect=self.SMTPException("boom"),
        ):
            resp = self.client.post(
                reverse("lists:invite", kwargs={"pk": self.lst.pk}),
                data={"target_email": "newbie@example.test", "target_person": "", "mode": ""},
                follow=True,
            )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "konnte nicht")
        # Token persisted despite the mail failure.
        self.assertTrue(
            ListInviteToken.objects.filter(
                list=self.lst, target_email="newbie@example.test"
            ).exists()
        )

    def test_invite_success_path_sends_and_reports(self):
        resp = self.client.post(
            reverse("lists:invite", kwargs={"pk": self.lst.pk}),
            data={"target_email": "newbie@example.test", "target_person": "", "mode": ""},
            follow=True,
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(mail.outbox), 1)
        self.assertContains(resp, "versendet")


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
        # Joining must also add the user to the Benutzergruppe, otherwise they
        # cannot see the (private) list they just joined and get no list mail.
        self.assertTrue(ListAccess.objects.filter(list=self.lst, user=self.visitor).exists())

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


class AssociateWizardMultiPersonTests(TestCase):
    """Phase 3: the wizard splits attributes by `applies_to_role`, gives the
    registering parent their own associate record, and optionally captures a
    second parent as a separate associate record. See CLAUDE.md /
    *Family-association model / Multi-person row*."""

    def setUp(self):
        self.client = Client()
        self.template = ListTemplate.objects.create(
            name="Schulklasse",
            member_subject_mode=ListTemplate.MemberSubjectMode.VIA_ASSOCIATE,
            relationship_roles=["Mutter von", "Vater von", "Erziehungsberechtigte von"],
        )
        # member-role attribute (stays on the child).
        self.attr_notes = ListAttribute.objects.create(
            template=self.template, name="Notizen", type=ListAttribute.Type.TEXT,
            position=0, applies_to_role=ListAttribute.AppliesTo.MEMBER,
        )
        # associate-role attributes (rendered once per parent).
        self.attr_phone = ListAttribute.objects.create(
            template=self.template, name="Telefon", type=ListAttribute.Type.PHONE,
            position=1, applies_to_role=ListAttribute.AppliesTo.ASSOCIATE,
        )
        self.attr_address = ListAttribute.objects.create(
            template=self.template, name="Adresse", type=ListAttribute.Type.TEXT,
            position=2, applies_to_role=ListAttribute.AppliesTo.ASSOCIATE,
        )
        self.lst = List.objects.create(
            title="Klasse 5a", email_alias="5a", template=self.template,
            visibility=List.Visibility.PRIVATE,
        )
        self.parent = _make_user(
            username="parent", given="Eva", family="Mueller", email="eva@example.test"
        )
        ListAccess.objects.create(list=self.lst, user=self.parent)

    def _post(self, **overrides):
        data = {
            "given_name": "Lina",
            "family_name": "Mueller",
            "member_email": "",
            "role": "Mutter von",
            f"attr_{self.attr_notes.pk}": "mag Mathe",
            f"p1_attr_{self.attr_phone.pk}": "+49 111",
            f"p1_attr_{self.attr_address.pk}": "Hauptstr. 1",
        }
        data.update(overrides)
        self.client.force_login(self.parent)
        return self.client.post(
            reverse("lists:record_create_associate", kwargs={"pk": self.lst.pk}),
            data=data,
        )

    def test_member_and_associate_attributes_split_across_records(self):
        resp = self._post()
        self.assertEqual(resp.status_code, 302)
        child = Person.objects.get(given_name="Lina", family_name="Mueller")
        child_rec = ListRecord.objects.get(list=self.lst, subject=child)
        child_vals = {v.attribute_id: v.value for v in child_rec.values.all()}
        # Child carries only the member attribute.
        self.assertEqual(child_vals.get(self.attr_notes.pk), "mag Mathe")
        self.assertNotIn(self.attr_phone.pk, child_vals)
        self.assertNotIn(self.attr_address.pk, child_vals)

        # Registering parent has their own associate record with the associate
        # attributes and a self_registered manager row.
        p1_rec = ListRecord.objects.get(
            list=self.lst, subject=self.parent.person, role=ListRecord.Role.ASSOCIATE
        )
        p1_vals = {v.attribute_id: v.value for v in p1_rec.values.all()}
        self.assertEqual(p1_vals.get(self.attr_phone.pk), "+49 111")
        self.assertEqual(p1_vals.get(self.attr_address.pk), "Hauptstr. 1")
        self.assertNotIn(self.attr_notes.pk, p1_vals)
        self.assertTrue(
            RecordManager.objects.filter(
                record=p1_rec, user=self.parent,
                basis=RecordManager.Basis.SELF_REGISTERED,
            ).exists()
        )

    def test_associate_attributes_visible_to_plain_member_after_wizard(self):
        # Regression: the wizard wrote associate values but no visibility grant,
        # so a plain Benutzergruppe member (not a manager) saw none of the
        # parents' phone/address — the class list looked empty. The wizard now
        # grants every non-public attribute to the list's own audience so the
        # class list reproduces the paper list. See CLAUDE.md / *List display
        # defaults*.
        self._post()
        viewer = _make_user(username="other-parent", given="Carla", family="X")
        ListAccess.objects.create(list=self.lst, user=viewer)  # member, not manager
        rows = build_composed_rows(viewer, self.lst)
        self.assertEqual(len(rows), 1)
        parents = rows[0]["parents"]
        self.assertEqual(len(parents), 1)
        seen = dict((a.name, v) for a, v in parents[0]["fields"])
        self.assertEqual(seen.get("Telefon"), "+49 111")
        self.assertEqual(seen.get("Adresse"), "Hauptstr. 1")

    def test_second_parent_creates_separate_associate_record(self):
        resp = self._post(
            add_second_parent="on",
            p2_given_name="Olaf",
            p2_family_name="Mueller",
            p2_role="Vater von",
            p2_email="olaf@example.test",
            **{
                f"p2_attr_{self.attr_phone.pk}": "+49 222",
                f"p2_attr_{self.attr_address.pk}": "Hauptstr. 1",
            },
        )
        self.assertEqual(resp.status_code, 302)
        child = Person.objects.get(given_name="Lina")
        p2 = Person.objects.get(given_name="Olaf", family_name="Mueller")
        # Second parent is a PERSON only — no USER, no ListAccess.
        self.assertFalse(User.objects.filter(person=p2).exists())
        self.assertFalse(ListAccess.objects.filter(list=self.lst, user__person=p2).exists())
        # Own associate record with their own phone, managed by the registering
        # user as proxy creator.
        p2_rec = ListRecord.objects.get(
            list=self.lst, subject=p2, role=ListRecord.Role.ASSOCIATE
        )
        p2_vals = {v.attribute_id: v.value for v in p2_rec.values.all()}
        self.assertEqual(p2_vals.get(self.attr_phone.pk), "+49 222")
        self.assertTrue(
            RecordManager.objects.filter(
                record=p2_rec, user=self.parent, basis=RecordManager.Basis.CREATOR
            ).exists()
        )
        # Relationship child ← second parent with the chosen role.
        self.assertTrue(
            PersonRelationship.objects.filter(
                subject_person=child, related_person=p2, role="Vater von"
            ).exists()
        )

    def test_second_parent_requires_name_and_role(self):
        resp = self._post(add_second_parent="on")  # no p2_* fields
        self.assertEqual(resp.status_code, 200)  # re-rendered with errors
        self.assertFalse(
            ListRecord.objects.filter(list=self.lst).exists()
        )  # atomic: nothing created
        self.assertFalse(Person.objects.filter(given_name="Lina").exists())

    def test_sibling_reuses_parent_associate_record(self):
        self._post(given_name="Lina")
        self._post(given_name="Tom")
        # Two member (child) records, but a single associate record for the parent.
        self.assertEqual(
            ListRecord.objects.filter(list=self.lst, role=ListRecord.Role.MEMBER).count(), 2
        )
        self.assertEqual(
            ListRecord.objects.filter(
                list=self.lst, subject=self.parent.person, role=ListRecord.Role.ASSOCIATE
            ).count(),
            1,
        )

    def test_form_field_namespaces_present(self):
        self.client.force_login(self.parent)
        form = AssociateWizardForm(list_obj=self.lst, user=self.parent)
        self.assertIn(f"attr_{self.attr_notes.pk}", form.fields)
        self.assertIn(f"p1_attr_{self.attr_phone.pk}", form.fields)
        self.assertIn(f"p2_attr_{self.attr_phone.pk}", form.fields)
        # member attribute does not get parent namespaces.
        self.assertNotIn(f"p1_attr_{self.attr_notes.pk}", form.fields)


class ComposedRowTests(TestCase):
    """Phase 4: `build_composed_rows` assembles one wide row per child (member
    record) with each linked parent as its own cell group, joined via
    PersonRelationship. Each parent cell honours that parent record's own
    name/field/email visibility — consent is per parent, not inherited from the
    child. See CLAUDE.md / *Family-association model / Multi-person row*."""

    def setUp(self):
        self.template = ListTemplate.objects.create(
            name="Schulklasse",
            member_subject_mode=ListTemplate.MemberSubjectMode.VIA_ASSOCIATE,
            relationship_roles=["Mutter von", "Vater von"],
        )
        self.attr_notes = ListAttribute.objects.create(
            template=self.template, name="Notizen", type=ListAttribute.Type.TEXT,
            position=0, applies_to_role=ListAttribute.AppliesTo.MEMBER,
        )
        self.attr_phone = ListAttribute.objects.create(
            template=self.template, name="Telefon", type=ListAttribute.Type.PHONE,
            position=1, applies_to_role=ListAttribute.AppliesTo.ASSOCIATE,
        )
        self.lst = List.objects.create(
            title="Klasse 5a", email_alias="5a", template=self.template,
            visibility=List.Visibility.PRIVATE,
        )

        # Child (member) with one member-role value.
        self.child = Person.objects.create(given_name="Lina", family_name="Mueller")
        self.child_rec = ListRecord.objects.create(
            list=self.lst, subject=self.child, role=ListRecord.Role.MEMBER
        )
        ListRecordValue.objects.create(
            record=self.child_rec, attribute=self.attr_notes, value="mag Mathe"
        )

        # Registering parent (a USER) — Mutter. As in the wizard she joins the
        # Benutzergruppe, is guardian of the child and owns her own record.
        self.mom = _make_user(
            username="mom", given="Eva", family="Mueller", email="eva@example.test"
        )
        ListAccess.objects.create(list=self.lst, user=self.mom)
        RecordManager.objects.create(
            record=self.child_rec, user=self.mom, basis=RecordManager.Basis.GUARDIAN
        )
        self.mom_rec = ListRecord.objects.create(
            list=self.lst, subject=self.mom.person, role=ListRecord.Role.ASSOCIATE
        )
        ListRecordValue.objects.create(
            record=self.mom_rec, attribute=self.attr_phone, value="+49 111"
        )
        RecordManager.objects.create(
            record=self.mom_rec, user=self.mom, basis=RecordManager.Basis.SELF_REGISTERED
        )
        PersonRelationship.objects.create(
            subject_person=self.child, related_person=self.mom.person, role="Mutter von"
        )

        # Second parent — Vater, a PERSON without a USER. The wizard makes the
        # registering parent the proxy creator/manager of this record.
        self.dad = Person.objects.create(
            given_name="Olaf", family_name="Mueller", email="olaf@example.test"
        )
        self.dad_rec = ListRecord.objects.create(
            list=self.lst, subject=self.dad, role=ListRecord.Role.ASSOCIATE
        )
        ListRecordValue.objects.create(
            record=self.dad_rec, attribute=self.attr_phone, value="+49 222"
        )
        RecordManager.objects.create(
            record=self.dad_rec, user=self.mom, basis=RecordManager.Basis.CREATOR
        )
        PersonRelationship.objects.create(
            subject_person=self.child, related_person=self.dad, role="Vater von"
        )

        # A plain member of the class Benutzergruppe (sees the list, manages
        # nothing) — the consent probe.
        self.viewer = _make_user(username="viewer", given="Carla", family="X")
        ListAccess.objects.create(list=self.lst, user=self.viewer)

    def test_one_row_per_child_with_parents_grouped_by_role(self):
        rows = build_composed_rows(self.mom, self.lst)
        # One row (the child), not three flat records.
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["subject_display"], "Lina Mueller")
        self.assertEqual(dict((a.name, v) for a, v in row["fields"]), {"Notizen": "mag Mathe"})

        labels = [p["label"] for p in row["parents"]]
        self.assertEqual(labels, ["Mutter", "Vater"])  # role-derived, ordered by role
        # Each parent carries the associate attribute, not the member one.
        for parent in row["parents"]:
            names = [a.name for a, _ in parent["fields"]]
            self.assertEqual(names, ["Telefon"])

    def _add_member_grid_attrs(self):
        # Notizen stays on display_row 1; Adresse + Geburtstag on row 2 → the
        # grid is 2 columns wide (the wider row), row 1 is padded with one slot.
        addr = ListAttribute.objects.create(
            template=self.template, name="Adresse", type=ListAttribute.Type.TEXT,
            position=2, applies_to_role=ListAttribute.AppliesTo.MEMBER, display_row=2,
        )
        ListAttribute.objects.create(
            template=self.template, name="Geburtstag", type=ListAttribute.Type.TEXT,
            position=3, applies_to_role=ListAttribute.AppliesTo.MEMBER, display_row=2,
        )
        return addr

    def test_member_grid_groups_and_pads_by_display_row(self):
        addr = self._add_member_grid_attrs()
        ListRecordValue.objects.create(
            record=self.child_rec, attribute=addr, value="Hauptstr. 1"
        )
        # mom manages the child record → sees every member field.
        grid = build_composed_rows(self.mom, self.lst)[0]["member_grid"]
        # One grid line per display_row, every line padded to the column count.
        self.assertEqual([len(r) for r in grid], [2, 2])
        self.assertEqual(grid[0][0]["attr"].name, "Notizen")
        self.assertIsNone(grid[0][1])  # padding slot keeps the columns aligned
        row2 = {c["attr"].name: c["value"] for c in grid[1]}
        self.assertEqual(row2["Adresse"], "Hauptstr. 1")
        self.assertIsNone(row2["Geburtstag"])  # no value → None → "—" in the UI

    def test_member_grid_header_matches_value_grid(self):
        self._add_member_grid_attrs()
        header, col_count = member_grid_header(self.template)
        self.assertEqual(col_count, 2)
        self.assertEqual(header[0][0].name, "Notizen")
        self.assertIsNone(header[0][1])  # same padding shape as the value grid
        self.assertEqual([a.name for a in header[1]], ["Adresse", "Geburtstag"])

    def test_manager_sees_parent_emails(self):
        # mom manages both her own record and (as proxy creator) dad's record →
        # she sees both emails regardless of the opt-in email sentinel. Consent
        # gating bites only for non-managing viewers (see the viewer-based tests).
        rows = build_composed_rows(self.mom, self.lst)
        by_label = {p["label"]: p for p in rows[0]["parents"]}
        self.assertEqual(by_label["Mutter"]["subject_email"], "eva@example.test")
        self.assertEqual(by_label["Vater"]["subject_email"], "olaf@example.test")

    def test_parent_email_hidden_by_default_for_audience_member(self):
        rows = build_composed_rows(self.viewer, self.lst)
        for parent in rows[0]["parents"]:
            self.assertIsNone(parent["subject_email"])

    def test_parent_email_shown_after_per_parent_grant(self):
        # Grant only Mutter's email to the class Benutzergruppe.
        ListRecordAccess.objects.create(
            record=self.mom_rec, attribute=None,
            sentinel=ListRecordAccess.Sentinel.EMAIL, audience=self.lst,
        )
        rows = build_composed_rows(self.viewer, self.lst)
        by_label = {p["label"]: p for p in rows[0]["parents"]}
        self.assertEqual(by_label["Mutter"]["subject_email"], "eva@example.test")
        self.assertIsNone(by_label["Vater"]["subject_email"])  # not granted

    def test_anonymous_parent_name_falls_back_to_counter(self):
        # Hide dad's name (drop the default public name row for his record).
        ListRecordAccess.objects.filter(
            record=self.dad_rec, attribute__isnull=True,
            sentinel=ListRecordAccess.Sentinel.NAME,
        ).delete()
        rows = build_composed_rows(self.viewer, self.lst)
        by_label = {p["label"]: p for p in rows[0]["parents"]}
        self.assertEqual(by_label["Mutter"]["subject_display"], "Eva Mueller")
        self.assertTrue(by_label["Vater"]["subject_display"].startswith("?"))

    def test_via_associate_detail_page_renders_wide_row(self):
        client = Client()
        client.force_login(self.mom)
        resp = client.get(reverse("lists:detail", kwargs={"pk": self.lst.pk}))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Lina Mueller")
        self.assertContains(resp, "Bezugsperson")  # composed-table column header
        self.assertContains(resp, "class-list")  # wide one-row-per-parent table
        self.assertContains(resp, "Mutter")  # per-parent role label
        self.assertContains(resp, "+49 111")
        self.assertContains(resp, "+49 222")


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
        # DMARC From-munging: visible From: is on MAIL_DOMAIN (our routing
        # alias), NOT the sender's foreign address. Sender stays identifiable
        # via display name + Reply-To.
        from_header = msg.extra_headers["From"]
        self.assertIn("alias-TOK@caos.cloud", from_header)
        self.assertIn("via Klasse 5a", from_header)
        self.assertNotEqual(from_header, "sender@example.org")
        self.assertEqual(msg.extra_headers["Reply-To"], "sender@example.org")
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

    def test_build_outbound_email_anonymised_hides_sender(self):
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
        from_header = msg.extra_headers["From"]
        # …visible From: is the alias, generic display, no real address anywhere,
        # and no Reply-To that could leak it.
        self.assertIn("alias-TOK2@caos.cloud", from_header)
        self.assertNotIn("sender@example.org", from_header)
        self.assertNotIn("Reply-To", msg.extra_headers)

    def test_message_from_header_is_munged_onto_mail_domain(self):
        """Django's EmailMessage.message() keeps our munged From: and Message-ID;
        the From: domain must be MAIL_DOMAIN so DMARC aligns."""
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
        self.assertIn("alias-MID@caos.cloud", rendered["From"])
        self.assertNotIn("@y.z>", rendered["From"])  # foreign domain not in From
        self.assertEqual(rendered["Reply-To"], "x@y.z")
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
        # Two Users sharing a PERSON.email (the family-mailbox case). Distinct
        # names matter: Person.Meta.ordering would otherwise pull family_name/
        # given_name into the SELECT DISTINCT set and defeat the dedup.
        shared = "family@example.org"
        u_father = _make_user(
            username="father", given="Max", family="Vater", email=shared
        )
        u_mother = _make_user(
            username="mother", given="Eva", family="Mutter", email=shared
        )
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
        # From is munged onto MAIL_DOMAIN (DMARC); sender kept in Reply-To.
        self.assertIn("alias-TOK@caos.cloud", sent.extra_headers["From"])
        self.assertEqual(sent.extra_headers["Reply-To"], "sender@example.org")
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


# ---------------------------------------------------------------------------
# Phase 5a — Inbound mail (IMAP IDLE consumer)
# ---------------------------------------------------------------------------


from datetime import datetime, timezone as dt_timezone

from django.core.management import call_command
from django.core.management.base import CommandError

from .imap_consumer import (
    _is_new_mail_signal,
    _parse_fetch_response,
    _parse_internaldate,
)
from .inbound import extract_recipient_alias, parse_and_persist, resolve_alias
from .models import InboundMessage
from .tasks import process_inbound


def _eml(
    *,
    from_addr: str = "sender@example.org",
    to_addr: str = "5a@caos.cloud",
    subject: str = "Hallo",
    message_id: str = "abc@example.org",
    extra_headers: dict[str, str] | None = None,
    body: str = "Hello world\r\n",
) -> bytes:
    """Build a minimal RFC-822 byte blob for inbound-pipeline tests."""
    headers = [
        f"From: {from_addr}",
        f"To: {to_addr}",
        f"Subject: {subject}",
        f"Message-ID: <{message_id}>",
        "MIME-Version: 1.0",
        "Content-Type: text/plain; charset=utf-8",
    ]
    for k, v in (extra_headers or {}).items():
        headers.append(f"{k}: {v}")
    return ("\r\n".join(headers) + "\r\n\r\n" + body).encode("utf-8")


@override_settings(MAIL_DOMAIN="caos.cloud")
class InboundHeaderParsingTests(TestCase):
    def test_extract_recipient_alias_uses_first_match_on_our_domain(self):
        import email
        import email.policy

        eml = _eml(
            to_addr="someone-else@external.example, 5a@caos.cloud",
        )
        msg = email.message_from_bytes(eml, policy=email.policy.default)
        local, domain = extract_recipient_alias(msg, "caos.cloud")
        self.assertEqual(local, "5a")
        self.assertEqual(domain, "caos.cloud")

    def test_extract_recipient_alias_prefers_delivered_to(self):
        import email
        import email.policy

        eml = _eml(
            to_addr="distribution@other.example",
            extra_headers={"Delivered-To": "5a@caos.cloud"},
        )
        msg = email.message_from_bytes(eml, policy=email.policy.default)
        local, domain = extract_recipient_alias(msg, "caos.cloud")
        self.assertEqual((local, domain), ("5a", "caos.cloud"))

    def test_extract_recipient_alias_strips_plus_tag_case_preserved(self):
        """Extract preserves case (tokens are case-significant base64url);
        only plus-tag is stripped. Lowercasing happens at storage time."""
        import email
        import email.policy

        eml = _eml(to_addr="5A+Spam@CAOS.cloud")
        msg = email.message_from_bytes(eml, policy=email.policy.default)
        local, domain = extract_recipient_alias(msg, "caos.cloud")
        self.assertEqual(local, "5A")
        self.assertEqual(domain, "CAOS.cloud")

    def test_extract_recipient_alias_falls_back_when_no_domain_match(self):
        import email
        import email.policy

        eml = _eml(to_addr="elsewhere@third.example")
        msg = email.message_from_bytes(eml, policy=email.policy.default)
        local, domain = extract_recipient_alias(msg, "caos.cloud")
        # Best-effort fallback: first address, lowercased.
        self.assertEqual(local, "elsewhere")
        self.assertEqual(domain, "third.example")


class ResolveAliasTests(TestCase):
    def setUp(self):
        self.template = ListTemplate.objects.create(name="Schulklasse")
        self.lst = List.objects.create(
            title="Klasse 5a",
            email_alias="5a",
            template=self.template,
            visibility=List.Visibility.PRIVATE,
        )

    def test_matches_list_email_alias_case_insensitive(self):
        matched_list, matched_outbound = resolve_alias("5A")
        self.assertEqual(matched_list, self.lst)
        self.assertIsNone(matched_outbound)

    def test_archived_list_does_not_match(self):
        self.lst.archived_at = timezone.now()
        self.lst.save(update_fields=["archived_at"])
        matched_list, matched_outbound = resolve_alias("5a")
        self.assertIsNone(matched_list)
        self.assertIsNone(matched_outbound)

    def test_matches_bounce_token_to_outbound(self):
        om = OutboundMessage.objects.create(
            list=self.lst,
            message_id="x@y",
            alias_token="TOK123",
            from_email="s@x",
            recipient_email="r@x",
        )
        matched_list, matched_outbound = resolve_alias("bounce-TOK123")
        self.assertIsNone(matched_list)
        self.assertEqual(matched_outbound, om)

    def test_matches_alias_token_to_outbound(self):
        om = OutboundMessage.objects.create(
            list=self.lst,
            message_id="x@y",
            alias_token="REPLY1",
            from_email="s@x",
            recipient_email="r@x",
        )
        matched_list, matched_outbound = resolve_alias("alias-REPLY1")
        self.assertIsNone(matched_list)
        self.assertEqual(matched_outbound, om)

    def test_unknown_local_returns_pair_of_nones(self):
        matched_list, matched_outbound = resolve_alias("ghost")
        self.assertIsNone(matched_list)
        self.assertIsNone(matched_outbound)

    def test_bounce_prefix_with_unknown_token_yields_none(self):
        matched_list, matched_outbound = resolve_alias("bounce-doesnotexist")
        self.assertIsNone(matched_list)
        self.assertIsNone(matched_outbound)

    def test_list_wins_over_token_collision(self):
        # Pathological case: someone created a list with email_alias =
        # "bounce-TOK456". The List match wins; the bounce-token branch is
        # not consulted.
        list_with_bounce_alias = List.objects.create(
            title="X",
            email_alias="bounce-tok456",
            template=self.template,
            visibility=List.Visibility.PRIVATE,
        )
        OutboundMessage.objects.create(
            list=self.lst,
            message_id="x@y",
            alias_token="TOK456",
            from_email="s@x",
            recipient_email="r@x",
        )
        matched_list, matched_outbound = resolve_alias("bounce-TOK456")
        self.assertEqual(matched_list, list_with_bounce_alias)
        self.assertIsNone(matched_outbound)


@override_settings(MAIL_DOMAIN="caos.cloud")
class ParseAndPersistTests(TestCase):
    def setUp(self):
        self.template = ListTemplate.objects.create(name="Schulklasse")
        self.lst = List.objects.create(
            title="Klasse 5a",
            email_alias="5a",
            template=self.template,
            visibility=List.Visibility.PRIVATE,
        )

    def test_persists_basic_fields(self):
        eml = _eml(
            from_addr="parent@example.org",
            to_addr="5a@caos.cloud",
            subject="Klassenfest",
            message_id="abc@example.org",
        )
        with patch.object(process_inbound, "defer"):
            with self.captureOnCommitCallbacks(execute=True):
                row = parse_and_persist(
                    eml,
                    uidvalidity=10,
                    uid=42,
                    internaldate=datetime(
                        2026, 5, 27, 12, 0, 0, tzinfo=dt_timezone.utc
                    ),
                )
        self.assertIsNotNone(row)
        row.refresh_from_db()
        self.assertEqual(row.imap_uidvalidity, 10)
        self.assertEqual(row.imap_uid, 42)
        self.assertEqual(row.message_id, "abc@example.org")
        self.assertEqual(row.from_email, "parent@example.org")
        self.assertEqual(row.to_alias, "5a")
        self.assertEqual(row.to_domain, "caos.cloud")
        self.assertEqual(row.subject, "Klassenfest")
        self.assertEqual(row.decision, InboundMessage.Decision.PENDING)
        self.assertEqual(row.matched_list, self.lst)
        self.assertIsNone(row.matched_outbound)

    def test_persists_raw_eml_bytes(self):
        eml = _eml()
        with patch.object(process_inbound, "defer"):
            with self.captureOnCommitCallbacks(execute=True):
                row = parse_and_persist(eml, uidvalidity=1, uid=1)
        row.refresh_from_db()
        # raw_eml is a BinaryField; on read it returns bytes (or memoryview
        # depending on driver). Normalise via bytes(...).
        self.assertEqual(bytes(row.raw_eml), eml)

    def test_uid_idempotency_returns_none_on_dedup(self):
        eml = _eml(message_id="m1@x")
        with patch.object(process_inbound, "defer"):
            with self.captureOnCommitCallbacks(execute=True):
                first = parse_and_persist(eml, uidvalidity=1, uid=99)
        self.assertIsNotNone(first)
        # Second call with same UID — even with different body — must dedup.
        eml2 = _eml(message_id="m2@x", subject="different")
        with patch.object(process_inbound, "defer") as mocked:
            with self.captureOnCommitCallbacks(execute=True):
                second = parse_and_persist(eml2, uidvalidity=1, uid=99)
        self.assertIsNone(second)
        # No re-enqueue on dedup either.
        mocked.assert_not_called()
        self.assertEqual(InboundMessage.objects.filter(imap_uid=99).count(), 1)

    def test_uidvalidity_distinguishes_rows(self):
        eml = _eml()
        with patch.object(process_inbound, "defer"):
            with self.captureOnCommitCallbacks(execute=True):
                parse_and_persist(eml, uidvalidity=1, uid=1)
                parse_and_persist(eml, uidvalidity=2, uid=1)
        self.assertEqual(
            InboundMessage.objects.filter(imap_uid=1).count(), 2
        )

    def test_defers_process_inbound_on_commit(self):
        eml = _eml()
        with patch.object(process_inbound, "defer") as mocked:
            with self.captureOnCommitCallbacks(execute=True):
                row = parse_and_persist(eml, uidvalidity=1, uid=7)
        self.assertIsNotNone(row)
        mocked.assert_called_once_with(inbound_id=row.pk)

    def test_unknown_alias_leaves_matched_list_none(self):
        eml = _eml(to_addr="ghost@caos.cloud")
        with patch.object(process_inbound, "defer"):
            with self.captureOnCommitCallbacks(execute=True):
                row = parse_and_persist(eml, uidvalidity=1, uid=1)
        row.refresh_from_db()
        self.assertEqual(row.to_alias, "ghost")
        self.assertIsNone(row.matched_list)
        # Phase 5a: decision stays PENDING; Phase 5b reclassifies.
        self.assertEqual(row.decision, InboundMessage.Decision.PENDING)

    def test_bounce_alias_records_matched_outbound(self):
        om = OutboundMessage.objects.create(
            list=self.lst,
            message_id="x@y",
            alias_token="BOUNCE7",
            from_email="s@x",
            recipient_email="r@x",
        )
        eml = _eml(to_addr="bounce-BOUNCE7@caos.cloud")
        with patch.object(process_inbound, "defer"):
            with self.captureOnCommitCallbacks(execute=True):
                row = parse_and_persist(eml, uidvalidity=1, uid=1)
        row.refresh_from_db()
        self.assertEqual(row.matched_outbound, om)
        self.assertIsNone(row.matched_list)

    def test_missing_message_id_stores_empty(self):
        eml = (
            b"From: x@example.org\r\n"
            b"To: 5a@caos.cloud\r\n"
            b"Subject: no msgid\r\n"
            b"\r\n"
            b"body\r\n"
        )
        with patch.object(process_inbound, "defer"):
            with self.captureOnCommitCallbacks(execute=True):
                row = parse_and_persist(eml, uidvalidity=1, uid=1)
        row.refresh_from_db()
        self.assertEqual(row.message_id, "")

    def test_internaldate_default_uses_now(self):
        eml = _eml()
        before = timezone.now()
        with patch.object(process_inbound, "defer"):
            with self.captureOnCommitCallbacks(execute=True):
                row = parse_and_persist(eml, uidvalidity=1, uid=1)
        after = timezone.now()
        self.assertGreaterEqual(row.received_at, before)
        self.assertLessEqual(row.received_at, after)


class ImapFetchParsingTests(TestCase):
    def test_parse_internaldate_two_digit_day(self):
        out = _parse_internaldate("20-Jan-2026 12:34:56 +0000")
        self.assertEqual(
            out,
            datetime(2026, 1, 20, 12, 34, 56, tzinfo=dt_timezone.utc),
        )

    def test_parse_internaldate_one_digit_day_normalised(self):
        out = _parse_internaldate("1-Jan-2026 00:00:00 +0000")
        self.assertEqual(
            out, datetime(2026, 1, 1, 0, 0, 0, tzinfo=dt_timezone.utc)
        )

    def test_parse_internaldate_garbage_returns_none(self):
        self.assertIsNone(_parse_internaldate("not a date"))

    def test_parse_fetch_response_picks_body_after_literal_marker(self):
        data = [
            b'1 FETCH (UID 5 INTERNALDATE "20-Jan-2026 12:34:56 +0000" BODY[] {12}',
            b"Hello world\r\n",
            b")",
        ]
        raw_eml, internaldate = _parse_fetch_response(data)
        self.assertEqual(raw_eml, b"Hello world\r\n")
        self.assertEqual(
            internaldate,
            datetime(2026, 1, 20, 12, 34, 56, tzinfo=dt_timezone.utc),
        )

    def test_parse_fetch_response_no_body_returns_none(self):
        data = [b"1 FETCH (UID 5)"]
        raw_eml, internaldate = _parse_fetch_response(data)
        self.assertIsNone(raw_eml)
        self.assertIsNone(internaldate)

    def test_is_new_mail_signal_recognises_exists_and_recent(self):
        self.assertTrue(_is_new_mail_signal(b"1 EXISTS"))
        self.assertTrue(_is_new_mail_signal(b"3 RECENT"))
        self.assertTrue(_is_new_mail_signal(b"EXISTS"))
        self.assertFalse(_is_new_mail_signal(b"FLAGS (\\Seen)"))
        self.assertFalse(_is_new_mail_signal(None))


class ImapIdleCommandTests(TestCase):
    def test_command_refuses_without_imap_host(self):
        with override_settings(IMAP_HOST="", IMAP_USER="u"):
            with self.assertRaises(CommandError):
                call_command("imap_idle_daemon")

    def test_command_refuses_without_imap_user(self):
        with override_settings(IMAP_HOST="imap.example.org", IMAP_USER=""):
            with self.assertRaises(CommandError):
                call_command("imap_idle_daemon")


# ---------------------------------------------------------------------------
# Phase 5b — Inbound decision pipeline
# ---------------------------------------------------------------------------


from unittest.mock import MagicMock

from .inbound_pipeline import (
    Outcome,
    decide,
    extract_subject_and_body,
    identify_sender_users,
    looks_like_dsn,
    parse_dsn,
    parse_message,
    referenced_message_ids,
    resolve_send_permission,
    sender_is_member,
    suppression_reason,
)
from .models import ListSendPermission, MailReleaseToken
from .tasks import (
    expire_release_tokens,
    imap_expunge_processed,
    prune_mail_metadata,
    send_notification_mail,
    send_outbound_message,
)


def _dsn_eml(to_addr: str = "bounce-TOK@caos.cloud") -> bytes:
    """A minimal RFC-3464 multipart/report delivery-status notification."""
    return (
        "From: MAILER-DAEMON@provider.example\r\n"
        f"To: {to_addr}\r\n"
        "Subject: Undelivered Mail Returned to Sender\r\n"
        "Return-Path: <>\r\n"
        'Content-Type: multipart/report; report-type=delivery-status; boundary="B"\r\n'
        "MIME-Version: 1.0\r\n"
        "\r\n"
        "--B\r\n"
        "Content-Type: text/plain\r\n\r\n"
        "Delivery to the following recipient failed.\r\n"
        "--B\r\n"
        "Content-Type: message/delivery-status\r\n\r\n"
        "Reporting-MTA: dns; provider.example\r\n\r\n"
        "Final-Recipient: rfc822; dead@example.org\r\n"
        "Action: failed\r\n"
        "Status: 5.1.1\r\n"
        "Diagnostic-Code: smtp; 550 5.1.1 user unknown\r\n"
        "--B--\r\n"
    ).encode("utf-8")


class SuppressionTests(TestCase):
    def _msg(self, **headers):
        return parse_message(_eml(extra_headers=headers))

    def test_auto_submitted_suppresses(self):
        msg = self._msg(**{"Auto-Submitted": "auto-replied"})
        self.assertIsNotNone(suppression_reason(msg, check_in_reply_to=False))

    def test_auto_submitted_no_passes(self):
        msg = self._msg(**{"Auto-Submitted": "no"})
        self.assertIsNone(suppression_reason(msg, check_in_reply_to=False))

    def test_empty_return_path_suppresses(self):
        msg = self._msg(**{"Return-Path": "<>"})
        self.assertIsNotNone(suppression_reason(msg, check_in_reply_to=False))

    def test_precedence_bulk_suppresses(self):
        msg = self._msg(Precedence="bulk")
        self.assertIsNotNone(suppression_reason(msg, check_in_reply_to=False))

    def test_clean_message_passes(self):
        self.assertIsNone(suppression_reason(self._msg(), check_in_reply_to=True))

    def test_referenced_message_ids_parsed(self):
        msg = self._msg(**{
            "In-Reply-To": "<a@x>",
            "References": "<b@x> <c@x>",
        })
        self.assertEqual(referenced_message_ids(msg), {"a@x", "b@x", "c@x"})

    def test_in_reply_to_matching_outbound_suppressed_only_when_checked(self):
        template = ListTemplate.objects.create(name="T")
        lst = List.objects.create(title="L", email_alias="l", template=template)
        OutboundMessage.objects.create(
            list=lst,
            message_id="out1@caos.cloud",
            alias_token="A1",
            from_email="s@x",
            recipient_email="r@x",
        )
        msg = self._msg(**{"In-Reply-To": "<out1@caos.cloud>"})
        # List path checks In-Reply-To → suppressed.
        self.assertIsNotNone(suppression_reason(msg, check_in_reply_to=True))
        # Alias-reply path must NOT check it (always matches by construction).
        self.assertIsNone(suppression_reason(msg, check_in_reply_to=False))


class SenderIdentificationTests(TestCase):
    def test_resolves_by_email_case_insensitive(self):
        u = _make_user(username="a", email="parent@example.org")
        self.assertEqual(list(identify_sender_users("PARENT@example.org")), [u])

    def test_shared_mailbox_returns_all_users(self):
        u1 = _make_user(username="a", email="fam@example.org")
        u2 = _make_user(username="b", email="fam@example.org")
        self.assertEqual(set(identify_sender_users("fam@example.org")), {u1, u2})

    def test_inactive_user_excluded(self):
        _make_user(username="a", email="x@example.org", is_active=False)
        self.assertEqual(list(identify_sender_users("x@example.org")), [])

    def test_empty_email_returns_none(self):
        self.assertEqual(list(identify_sender_users("")), [])


class DecisionAlgorithmTests(TestCase):
    def setUp(self):
        self.template = ListTemplate.objects.create(name="Schulklasse")
        self.parent = List.objects.create(
            title="Elternbeirat", email_alias="eb", template=self.template
        )
        self.child = List.objects.create(
            title="5a", email_alias="5a", template=self.template, parent=self.parent
        )
        self.other = List.objects.create(
            title="Lehrerkollegium", email_alias="lk", template=self.template
        )
        self.granter = _make_super("granter")

    def _member_of(self, lst, username, email):
        u = _make_user(username=username, email=email)
        ListAccess.objects.create(list=lst, user=u)
        return u

    def test_member_gets_release(self):
        u = self._member_of(self.child, "m", "m@x.org")
        self.assertEqual(decide(self.child, [u]).outcome, Outcome.MEMBER_RELEASE)
        self.assertTrue(sender_is_member(self.child, [u]))

    def test_unknown_sender_admin_approval(self):
        self.assertEqual(decide(self.child, []).outcome, Outcome.ADMIN_APPROVAL)

    def test_parent_member_implicit_forward(self):
        u = self._member_of(self.parent, "p", "p@x.org")
        self.assertEqual(decide(self.child, [u]).outcome, Outcome.FORWARD)

    def test_unrelated_member_admin_approval(self):
        u = self._member_of(self.other, "o", "o@x.org")
        self.assertEqual(decide(self.child, [u]).outcome, Outcome.ADMIN_APPROVAL)

    def test_explicit_grant_with_click(self):
        u = self._member_of(self.other, "o", "o@x.org")
        ListSendPermission.objects.create(
            target_list=self.child,
            granted_to_list=self.other,
            granted_by=self.granter,
            requires_release_click=True,
        )
        self.assertEqual(decide(self.child, [u]).outcome, Outcome.PERMITTED_RELEASE)

    def test_explicit_grant_without_click(self):
        u = self._member_of(self.other, "o", "o@x.org")
        ListSendPermission.objects.create(
            target_list=self.child,
            granted_to_list=self.other,
            granted_by=self.granter,
            requires_release_click=False,
        )
        self.assertEqual(decide(self.child, [u]).outcome, Outcome.FORWARD)

    def test_explicit_parent_row_tightens_implicit_grant(self):
        u = self._member_of(self.parent, "p", "p@x.org")
        ListSendPermission.objects.create(
            target_list=self.child,
            granted_to_list=self.parent,
            granted_by=self.granter,
            requires_release_click=True,
        )
        self.assertEqual(decide(self.child, [u]).outcome, Outcome.PERMITTED_RELEASE)

    def test_transitive_grant_from_ancestor(self):
        grandparent = List.objects.create(
            title="Schule", email_alias="schule", template=self.template
        )
        self.parent.parent = grandparent
        self.parent.save(update_fields=["parent"])
        u = self._member_of(self.other, "o", "o@x.org")
        ListSendPermission.objects.create(
            target_list=grandparent,
            granted_to_list=self.other,
            granted_by=self.granter,
            transitive=True,
            requires_release_click=False,
        )
        self.assertEqual(decide(self.child, [u]).outcome, Outcome.FORWARD)

    def test_non_transitive_ancestor_grant_does_not_reach_child(self):
        grandparent = List.objects.create(
            title="Schule", email_alias="schule", template=self.template
        )
        self.parent.parent = grandparent
        self.parent.save(update_fields=["parent"])
        u = self._member_of(self.other, "o", "o@x.org")
        ListSendPermission.objects.create(
            target_list=grandparent,
            granted_to_list=self.other,
            granted_by=self.granter,
            transitive=False,
        )
        # grandparent grant is NOT transitive → child not reached → admin.
        self.assertEqual(decide(self.child, [u]).outcome, Outcome.ADMIN_APPROVAL)

    def test_resolve_send_permission_empty_membership(self):
        self.assertEqual(resolve_send_permission(self.child, set()), (False, False))


class DsnParseTests(TestCase):
    def test_looks_like_dsn_and_parse(self):
        msg = parse_message(_dsn_eml())
        self.assertTrue(looks_like_dsn(msg))
        status, diagnostic = parse_dsn(msg)
        self.assertIn("5.1.1", status)
        self.assertIn("user unknown", diagnostic)

    def test_plain_mail_not_dsn(self):
        msg = parse_message(_eml())
        self.assertFalse(looks_like_dsn(msg))

    def test_extract_subject_and_body(self):
        msg = parse_message(_eml(subject="Hallo", body="Inhalt\r\n"))
        subject, body = extract_subject_and_body(msg)
        self.assertEqual(subject, "Hallo")
        self.assertIn("Inhalt", body)


@override_settings(
    MAIL_DOMAIN="caos.cloud",
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    DEFAULT_FROM_EMAIL="Fichtelink <noreply@caos.cloud>",
    RP_ORIGIN="https://fichtelink.caos.cloud",
)
class ProcessInboundTests(TestCase):
    def setUp(self):
        self.template = ListTemplate.objects.create(name="Schulklasse")
        self.lst = List.objects.create(
            title="Klasse 5a",
            email_alias="5a",
            template=self.template,
            visibility=List.Visibility.PRIVATE,
        )
        self._uid = 0

    def _inbound(
        self,
        *,
        from_email="sender@example.org",
        to_alias="5a",
        subject="Hallo",
        raw=None,
        matched_list="__list__",
        matched_outbound=None,
    ) -> InboundMessage:
        self._uid += 1
        if matched_list == "__list__":
            matched_list = self.lst
        if raw is None:
            raw = _eml(
                from_addr=from_email,
                to_addr=f"{to_alias}@caos.cloud",
                subject=subject,
            )
        return InboundMessage.objects.create(
            imap_uidvalidity=1,
            imap_uid=self._uid,
            message_id=f"in{self._uid}@example.org",
            from_email=from_email,
            to_alias=to_alias,
            to_domain="caos.cloud",
            subject=subject,
            raw_eml=raw,
            received_at=timezone.now(),
            matched_list=matched_list,
            matched_outbound=matched_outbound,
        )

    def test_member_creates_release_token_and_notifies_sender(self):
        member = _make_user(username="m", email="m@x.org")
        ListAccess.objects.create(list=self.lst, user=member)
        inbound = self._inbound(from_email="m@x.org")
        with patch.object(send_notification_mail, "defer") as notify:
            with self.captureOnCommitCallbacks(execute=True):
                process_inbound.func(inbound_id=inbound.pk)
        inbound.refresh_from_db()
        self.assertEqual(inbound.decision, InboundMessage.Decision.PENDING_APPROVAL)
        tok = MailReleaseToken.objects.get(inbound=inbound)
        self.assertEqual(tok.kind, MailReleaseToken.Kind.MEMBER)
        self.assertTrue(tok.offer_anonymize)
        notify.assert_called_once()
        self.assertEqual(notify.call_args.kwargs["recipients"], ["m@x.org"])
        self.assertIn(tok.token, notify.call_args.kwargs["body"])

    def test_permitted_parent_member_forwards_directly(self):
        parent = List.objects.create(
            title="Elternbeirat", email_alias="eb", template=self.template
        )
        self.lst.parent = parent
        self.lst.save(update_fields=["parent"])
        sender = _make_user(username="p", email="p@x.org")
        ListAccess.objects.create(list=parent, user=sender)
        recipient = _make_user(username="c", email="c@x.org")
        ListAccess.objects.create(list=self.lst, user=recipient)
        inbound = self._inbound(from_email="p@x.org")
        with patch.object(send_outbound_message, "defer") as fanout:
            with self.captureOnCommitCallbacks(execute=True):
                process_inbound.func(inbound_id=inbound.pk)
        inbound.refresh_from_db()
        self.assertEqual(inbound.decision, InboundMessage.Decision.FORWARDED)
        self.assertFalse(MailReleaseToken.objects.filter(inbound=inbound).exists())
        om = OutboundMessage.objects.get(list=self.lst)
        self.assertEqual(om.recipient_email, "c@x.org")
        self.assertEqual(om.from_email, "p@x.org")
        self.assertFalse(om.anonymized_from)
        fanout.assert_called_once()

    def test_non_member_routes_to_admin_approval(self):
        admin = _make_user(username="adm", email="adm@x.org")
        ListAdmin.objects.create(list=self.lst, user=admin)
        inbound = self._inbound(from_email="stranger@external.example")
        with patch.object(send_notification_mail, "defer") as notify:
            with self.captureOnCommitCallbacks(execute=True):
                process_inbound.func(inbound_id=inbound.pk)
        inbound.refresh_from_db()
        self.assertEqual(inbound.decision, InboundMessage.Decision.PENDING_APPROVAL)
        tok = MailReleaseToken.objects.get(inbound=inbound)
        self.assertEqual(tok.kind, MailReleaseToken.Kind.ADMIN)
        self.assertFalse(tok.offer_anonymize)
        notify.assert_called_once()
        self.assertEqual(notify.call_args.kwargs["recipients"], ["adm@x.org"])

    def test_auto_submitted_list_mail_suppressed(self):
        raw = _eml(
            to_addr="5a@caos.cloud",
            extra_headers={"Auto-Submitted": "auto-replied"},
        )
        inbound = self._inbound(raw=raw)
        with self.captureOnCommitCallbacks(execute=True):
            process_inbound.func(inbound_id=inbound.pk)
        inbound.refresh_from_db()
        self.assertEqual(inbound.decision, InboundMessage.Decision.SUPPRESSED)
        self.assertFalse(MailReleaseToken.objects.filter(inbound=inbound).exists())

    def test_unknown_alias(self):
        inbound = self._inbound(to_alias="ghost", matched_list=None)
        process_inbound.func(inbound_id=inbound.pk)
        inbound.refresh_from_db()
        self.assertEqual(inbound.decision, InboundMessage.Decision.UNKNOWN_ALIAS)

    def test_archived_list_rejected(self):
        self.lst.archived_at = timezone.now()
        self.lst.save(update_fields=["archived_at"])
        inbound = self._inbound()
        process_inbound.func(inbound_id=inbound.pk)
        inbound.refresh_from_db()
        self.assertEqual(inbound.decision, InboundMessage.Decision.REJECTED)

    def test_idempotent_second_run_is_noop(self):
        inbound = self._inbound()
        inbound.decision = InboundMessage.Decision.FORWARDED
        inbound.save(update_fields=["decision"])
        with patch.object(send_notification_mail, "defer") as notify:
            process_inbound.func(inbound_id=inbound.pk)
        notify.assert_not_called()
        self.assertEqual(MailReleaseToken.objects.filter(inbound=inbound).count(), 0)

    def test_bounce_correlates_outbound(self):
        om = OutboundMessage.objects.create(
            list=self.lst,
            message_id="o@x",
            alias_token="TOKB",
            from_email="orig@x.org",
            recipient_email="dead@example.org",
            status=OutboundMessage.Status.SENT,
        )
        inbound = self._inbound(
            from_email="MAILER-DAEMON@provider.example",
            to_alias="bounce-tokb",
            matched_list=None,
            matched_outbound=om,
            raw=_dsn_eml("bounce-TOKB@caos.cloud"),
        )
        process_inbound.func(inbound_id=inbound.pk)
        inbound.refresh_from_db()
        om.refresh_from_db()
        self.assertEqual(inbound.decision, InboundMessage.Decision.BOUNCE)
        self.assertEqual(om.status, OutboundMessage.Status.BOUNCED)
        self.assertIn("5.1.1", om.last_error)

    def test_reply_to_alias_routes_to_original_sender(self):
        om = OutboundMessage.objects.create(
            list=self.lst,
            message_id="fwd@x",
            alias_token="TOKR",
            from_email="anon-original@x.org",
            recipient_email="member@x.org",
            anonymized_from=True,
            status=OutboundMessage.Status.SENT,
        )
        reply = _eml(
            from_addr="member@x.org",
            to_addr="alias-TOKR@caos.cloud",
            subject="Re: Klassenfest",
            body="Bin dabei!\r\n",
        )
        inbound = self._inbound(
            from_email="member@x.org",
            to_alias="alias-tokr",
            matched_list=None,
            matched_outbound=om,
            raw=reply,
        )
        with patch.object(send_outbound_message, "defer") as defer:
            with self.captureOnCommitCallbacks(execute=True):
                process_inbound.func(inbound_id=inbound.pk)
        inbound.refresh_from_db()
        self.assertEqual(inbound.decision, InboundMessage.Decision.FORWARDED)
        routed = OutboundMessage.objects.exclude(pk=om.pk).get()
        self.assertEqual(routed.recipient_email, "anon-original@x.org")
        self.assertEqual(routed.from_email, "member@x.org")
        self.assertFalse(routed.anonymized_from)
        defer.assert_called_once()
        self.assertEqual(defer.call_args.kwargs["outbound_id"], routed.pk)

    def test_auto_reply_to_alias_suppressed(self):
        om = OutboundMessage.objects.create(
            list=self.lst,
            message_id="fwd2@x",
            alias_token="TOKO",
            from_email="anon@x.org",
            recipient_email="member@x.org",
            anonymized_from=True,
        )
        raw = _eml(
            from_addr="member@x.org",
            to_addr="alias-TOKO@caos.cloud",
            extra_headers={"Auto-Submitted": "auto-replied"},
        )
        inbound = self._inbound(
            from_email="member@x.org",
            to_alias="alias-toko",
            matched_list=None,
            matched_outbound=om,
            raw=raw,
        )
        process_inbound.func(inbound_id=inbound.pk)
        inbound.refresh_from_db()
        self.assertEqual(inbound.decision, InboundMessage.Decision.SUPPRESSED)
        # No reply routed.
        self.assertEqual(OutboundMessage.objects.exclude(pk=om.pk).count(), 0)


@override_settings(
    MAIL_DOMAIN="caos.cloud",
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    DEFAULT_FROM_EMAIL="Fichtelink <noreply@caos.cloud>",
    RP_ORIGIN="https://fichtelink.caos.cloud",
)
class MailReleaseViewTests(TestCase):
    def setUp(self):
        self.template = ListTemplate.objects.create(name="Schulklasse")
        self.lst = List.objects.create(
            title="Klasse 5a",
            email_alias="5a",
            template=self.template,
            visibility=List.Visibility.PRIVATE,
        )
        self.recipient = _make_user(username="r", email="r@x.org")
        ListAccess.objects.create(list=self.lst, user=self.recipient)
        self.inbound = InboundMessage.objects.create(
            imap_uidvalidity=1,
            imap_uid=1,
            message_id="in@x",
            from_email="sender@example.org",
            to_alias="5a",
            to_domain="caos.cloud",
            subject="Klassenfest",
            raw_eml=_eml(subject="Klassenfest", body="Wann?\r\n"),
            received_at=timezone.now(),
            decision=InboundMessage.Decision.PENDING_APPROVAL,
            matched_list=self.lst,
        )
        self.token = MailReleaseToken.objects.create(
            inbound=self.inbound,
            list=self.lst,
            kind=MailReleaseToken.Kind.MEMBER,
            offer_anonymize=True,
        )
        self.client = Client()

    def _url(self, token=None):
        return reverse(
            "lists:mail_release", kwargs={"token": token or self.token.token}
        )

    def test_get_is_side_effect_free(self):
        resp = self.client.get(self._url())
        self.assertEqual(resp.status_code, 200)
        self.token.refresh_from_db()
        self.inbound.refresh_from_db()
        self.assertIsNone(self.token.consumed_at)
        self.assertEqual(
            self.inbound.decision, InboundMessage.Decision.PENDING_APPROVAL
        )

    def test_post_approve_forwards_and_consumes(self):
        with patch.object(send_outbound_message, "defer"):
            with self.captureOnCommitCallbacks(execute=True):
                resp = self.client.post(self._url(), data={"action": "approve"})
        self.assertEqual(resp.status_code, 200)
        self.token.refresh_from_db()
        self.inbound.refresh_from_db()
        self.assertIsNotNone(self.token.consumed_at)
        self.assertEqual(self.token.resolution, MailReleaseToken.Resolution.FORWARDED)
        self.assertEqual(self.inbound.decision, InboundMessage.Decision.FORWARDED)
        om = OutboundMessage.objects.get(list=self.lst)
        self.assertEqual(om.recipient_email, "r@x.org")
        self.assertEqual(om.from_email, "sender@example.org")
        # No anonymize field posted → real address.
        self.assertFalse(om.anonymized_from)

    def test_post_approve_with_anonymize(self):
        with patch.object(send_outbound_message, "defer"):
            with self.captureOnCommitCallbacks(execute=True):
                self.client.post(
                    self._url(), data={"action": "approve", "anonymize": "1"}
                )
        om = OutboundMessage.objects.get(list=self.lst)
        self.assertTrue(om.anonymized_from)

    def test_post_reject(self):
        resp = self.client.post(self._url(), data={"action": "reject"})
        self.assertEqual(resp.status_code, 200)
        self.token.refresh_from_db()
        self.inbound.refresh_from_db()
        self.assertEqual(self.token.resolution, MailReleaseToken.Resolution.REJECTED)
        self.assertEqual(self.inbound.decision, InboundMessage.Decision.REJECTED)
        self.assertEqual(OutboundMessage.objects.count(), 0)

    def test_consumed_token_410(self):
        self.token.consumed_at = timezone.now()
        self.token.save(update_fields=["consumed_at"])
        self.assertEqual(self.client.get(self._url()).status_code, 410)

    def test_expired_token_410(self):
        self.token.expires_at = timezone.now() - timedelta(days=1)
        self.token.save(update_fields=["expires_at"])
        self.assertEqual(self.client.get(self._url()).status_code, 410)

    def test_admin_token_does_not_offer_anonymize(self):
        self.token.kind = MailReleaseToken.Kind.ADMIN
        self.token.offer_anonymize = False
        self.token.save(update_fields=["kind", "offer_anonymize"])
        resp = self.client.get(self._url())
        self.assertNotContains(resp, 'name="anonymize"')


class PeriodicMaintenanceTests(TestCase):
    def setUp(self):
        self.template = ListTemplate.objects.create(name="T")
        self.lst = List.objects.create(title="L", email_alias="l", template=self.template)

    def _inbound(self, uid, received_at):
        return InboundMessage.objects.create(
            imap_uidvalidity=1,
            imap_uid=uid,
            from_email="x@x.org",
            to_alias="l",
            to_domain="caos.cloud",
            raw_eml=b"x",
            received_at=received_at,
        )

    @override_settings(MAIL_METADATA_RETENTION_DAYS=30)
    def test_prune_removes_old_metadata(self):
        old = self._inbound(1, timezone.now() - timedelta(days=40))
        fresh = self._inbound(2, timezone.now())
        old_out = OutboundMessage.objects.create(
            list=self.lst, message_id="o1@x", alias_token="P1",
            from_email="s@x", recipient_email="r@x",
        )
        OutboundMessage.objects.filter(pk=old_out.pk).update(
            created_at=timezone.now() - timedelta(days=40)
        )
        fresh_out = OutboundMessage.objects.create(
            list=self.lst, message_id="o2@x", alias_token="P2",
            from_email="s@x", recipient_email="r@x",
        )
        prune_mail_metadata.func(timestamp=0)
        self.assertFalse(InboundMessage.objects.filter(pk=old.pk).exists())
        self.assertTrue(InboundMessage.objects.filter(pk=fresh.pk).exists())
        self.assertFalse(OutboundMessage.objects.filter(pk=old_out.pk).exists())
        self.assertTrue(OutboundMessage.objects.filter(pk=fresh_out.pk).exists())

    def test_prune_cascades_release_tokens(self):
        old = self._inbound(1, timezone.now() - timedelta(days=200))
        tok = MailReleaseToken.objects.create(
            inbound=old, list=self.lst, kind=MailReleaseToken.Kind.MEMBER
        )
        prune_mail_metadata.func(timestamp=0)
        self.assertFalse(MailReleaseToken.objects.filter(pk=tok.pk).exists())

    def test_expire_release_tokens(self):
        inbound = self._inbound(1, timezone.now())
        expired = MailReleaseToken.objects.create(
            inbound=inbound, list=self.lst, kind=MailReleaseToken.Kind.MEMBER
        )
        MailReleaseToken.objects.filter(pk=expired.pk).update(
            expires_at=timezone.now() - timedelta(days=1)
        )
        live = MailReleaseToken.objects.create(
            inbound=inbound, list=self.lst, kind=MailReleaseToken.Kind.MEMBER
        )
        expire_release_tokens.func(timestamp=0)
        self.assertFalse(MailReleaseToken.objects.filter(pk=expired.pk).exists())
        self.assertTrue(MailReleaseToken.objects.filter(pk=live.pk).exists())

    @override_settings(IMAP_HOST="", IMAP_USER="")
    def test_imap_expunge_noop_without_config(self):
        # Must not raise or attempt a connection.
        imap_expunge_processed.func(timestamp=0)

    @override_settings(
        IMAP_HOST="imap.example",
        IMAP_USER="u",
        IMAP_PASS="p",
        IMAP_PORT=993,
        IMAP_USE_SSL=True,
        IMAP_EXPUNGE_GRACE_DAYS=7,
    )
    def test_imap_expunge_marks_and_expunges(self):
        fake = MagicMock()
        fake.search.return_value = ("OK", [b"1 2 3"])
        with patch("imaplib.IMAP4_SSL", return_value=fake):
            imap_expunge_processed.func(timestamp=0)
        fake.login.assert_called_once_with("u", "p")
        fake.select.assert_called_once_with("INBOX")
        self.assertEqual(fake.store.call_count, 3)
        fake.expunge.assert_called_once()


# ---------------------------------------------------------------------------
# Phase 6: school-class lifecycle
# ---------------------------------------------------------------------------


class RolloverPlanTests(TestCase):
    def setUp(self):
        self.t = ListTemplate.objects.create(name="Schulklasse")

    def _mk(self, alias, grade, track, cur, **extra):
        return List.objects.create(
            title=alias,
            email_alias=alias,
            template=self.t,
            visibility=List.Visibility.PRIVATE,
            cohort_grade=grade,
            cohort_track=track,
            curriculum_track=cur,
            **extra,
        )

    def test_g8_classification(self):
        c9 = self._mk("9a", 9, "a", "G8")
        c10a = self._mk("10a", 10, "a", "G8")
        c10b = self._mk("10b", 10, "b", "G8")
        k1 = self._mk("k1", 11, None, "G8")
        k2 = self._mk("k2", 12, None, "G8")
        plan = lifecycle.plan_rollover()
        kinds = {a.list_id: a.kind for a in plan.actions}
        self.assertEqual(kinds[c9.id], "advance")
        self.assertEqual(kinds[k1.id], "k1_to_k2")
        self.assertEqual(kinds[k2.id], "k2_archive")
        self.assertNotIn(c10a.id, kinds)
        self.assertNotIn(c10b.id, kinds)
        self.assertEqual(len(plan.merges), 1)
        self.assertEqual(set(plan.merges[0].source_list_ids), {c10a.id, c10b.id})
        self.assertEqual(plan.merges[0].new_grade, 11)

    def test_g9_last_lettered_is_grade_11(self):
        self._mk("11a", 11, "a", "G9")
        plan = lifecycle.plan_rollover()
        self.assertEqual([a.kind for a in plan.actions], [])
        self.assertEqual(len(plan.merges), 1)
        self.assertEqual(plan.merges[0].new_grade, 12)

    def test_non_school_lists_skipped(self):
        List.objects.create(
            title="Förderverein",
            email_alias="fv",
            template=self.t,
            visibility=List.Visibility.PRIVATE,
        )
        self.assertTrue(lifecycle.plan_rollover().is_empty)


class RolloverExecuteTests(TestCase):
    def setUp(self):
        self.t = ListTemplate.objects.create(name="Schulklasse")
        self.parent = List.objects.create(
            title="Elternbeirat", email_alias="eb", template=self.t
        )
        self.admin = _make_user(username="rolladmin")

    def _mk(self, alias, grade, track, cur):
        return List.objects.create(
            title=alias,
            email_alias=alias,
            template=self.t,
            visibility=List.Visibility.PRIVATE,
            parent=self.parent,
            cohort_grade=grade,
            cohort_track=track,
            curriculum_track=cur,
        )

    def test_full_g8_rollover(self):
        c9 = self._mk("9a", 9, "a", "G8")
        c10a = self._mk("10a", 10, "a", "G8")
        c10b = self._mk("10b", 10, "b", "G8")
        k1 = self._mk("k1", 11, None, "G8")
        k2 = self._mk("k2", 12, None, "G8")
        ListAdmin.objects.create(list=c10a, user=self.admin)

        child = Person.objects.create(given_name="Kind", family_name="Test")
        parent_user = _make_user(username="parent1", email="p@x.de")
        rec_child = ListRecord.objects.create(
            list=c10a, subject=child, role=ListRecord.Role.MEMBER
        )
        rec_parent = ListRecord.objects.create(
            list=c10a, subject=parent_user.person, role=ListRecord.Role.ASSOCIATE
        )
        RecordManager.objects.create(
            record=rec_child, user=parent_user, basis=RecordManager.Basis.GUARDIAN
        )

        plan = lifecycle.plan_rollover()
        result = lifecycle.execute_rollover(plan)

        for obj in (c9, c10a, c10b, k1, k2):
            obj.refresh_from_db()

        # 9a → 10a (mutated in place).
        self.assertEqual((c9.cohort_grade, c9.cohort_track), (10, "a"))
        self.assertEqual(c9.email_alias, "10a")
        self.assertIsNone(c9.archived_at)

        # K1 → K2.
        self.assertEqual((k1.cohort_grade, k1.cohort_track), (12, None))
        self.assertEqual(k1.email_alias, "k2")

        # K2 archived, alias retired (no longer resolvable).
        self.assertIsNotNone(k2.archived_at)
        self.assertNotEqual(k2.email_alias, "k2")

        # 10a / 10b merged → archived.
        self.assertIsNotNone(c10a.archived_at)
        self.assertIsNotNone(c10b.archived_at)

        # New K1 list.
        new_k1 = List.objects.get(id=result["merged_into"][0])
        self.assertEqual((new_k1.cohort_grade, new_k1.cohort_track), (11, None))
        self.assertEqual(new_k1.email_alias, "k1")
        self.assertIsNone(new_k1.archived_at)

        # Records re-parented onto the new K1.
        rec_child.refresh_from_db()
        rec_parent.refresh_from_db()
        self.assertEqual(rec_child.list_id, new_k1.id)
        self.assertEqual(rec_parent.list_id, new_k1.id)

        # Source admin + manager access carried onto K1.
        self.assertTrue(ListAdmin.objects.filter(list=new_k1, user=self.admin).exists())
        self.assertTrue(ListAccess.objects.filter(list=new_k1, user=parent_user).exists())

    def test_alias_swap_no_collision_on_advance_chain(self):
        # 5a..7a all advance; aliases must shuffle up without a unique clash.
        c5 = self._mk("5a", 5, "a", "G8")
        c6 = self._mk("6a", 6, "a", "G8")
        c7 = self._mk("7a", 7, "a", "G8")
        lifecycle.execute_rollover(lifecycle.plan_rollover())
        for obj in (c5, c6, c7):
            obj.refresh_from_db()
        self.assertEqual(c5.email_alias, "6a")
        self.assertEqual(c6.email_alias, "7a")
        self.assertEqual(c7.email_alias, "8a")


class TransferFlowTests(TestCase):
    def setUp(self):
        self.t = ListTemplate.objects.create(name="Schulklasse")
        self.attr = ListAttribute.objects.create(
            template=self.t, name="Tel", type=ListAttribute.Type.PHONE
        )
        self.src = List.objects.create(
            title="8a", email_alias="8a", template=self.t,
            visibility=List.Visibility.PRIVATE,
        )
        self.dest = List.objects.create(
            title="9b", email_alias="9b", template=self.t,
            visibility=List.Visibility.PRIVATE,
        )
        self.dest_admin = _make_user(username="destadmin")
        ListAdmin.objects.create(list=self.dest, user=self.dest_admin)
        self.parent = _make_user(username="parent", email="m@x.de")
        self.child = Person.objects.create(given_name="Kind", family_name="Müller")
        self.rec_child = ListRecord.objects.create(
            list=self.src, subject=self.child, role=ListRecord.Role.MEMBER
        )
        self.rec_parent = ListRecord.objects.create(
            list=self.src, subject=self.parent.person, role=ListRecord.Role.ASSOCIATE
        )
        RecordManager.objects.create(
            record=self.rec_child, user=self.parent, basis=RecordManager.Basis.GUARDIAN
        )
        PersonRelationship.objects.create(
            subject_person=self.child, related_person=self.parent.person, role="Mutter von"
        )
        ListRecordValue.objects.create(
            record=self.rec_child, attribute=self.attr, value="0151"
        )
        self.client = Client()

    def test_initiate_creates_pending(self):
        self.client.force_login(self.parent)
        resp = self.client.post(
            reverse("lists:transfer_initiate", kwargs={"pk": self.src.pk, "record_pk": self.rec_child.pk}),
            {"to_list": self.dest.pk},
        )
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(
            PendingTransfer.objects.filter(
                person=self.child, from_list=self.src, to_list=self.dest, status="pending"
            ).exists()
        )

    def test_accept_migrates_member_and_associate(self):
        transfer = PendingTransfer.objects.create(
            from_list=self.src, to_list=self.dest, person=self.child, requested_by=self.parent
        )
        self.client.force_login(self.dest_admin)
        resp = self.client.post(
            reverse("lists:transfer_decide", kwargs={"transfer_pk": transfer.pk}),
            {"action": "accept"},
        )
        self.assertEqual(resp.status_code, 302)
        transfer.refresh_from_db()
        self.assertEqual(transfer.status, "accepted")

        self.rec_child.refresh_from_db()
        self.rec_parent.refresh_from_db()
        self.assertIsNotNone(self.rec_child.archived_at)
        self.assertIsNotNone(self.rec_parent.archived_at)

        new_child = ListRecord.objects.get(
            list=self.dest, subject=self.child, archived_at__isnull=True
        )
        self.assertTrue(
            ListRecord.objects.filter(
                list=self.dest, subject=self.parent.person, archived_at__isnull=True
            ).exists()
        )
        self.assertEqual(
            ListRecordValue.objects.get(record=new_child, attribute=self.attr).value, "0151"
        )
        self.assertTrue(RecordManager.objects.filter(record=new_child, user=self.parent).exists())
        self.assertTrue(ListAccess.objects.filter(list=self.dest, user=self.parent).exists())

    def test_reject_keeps_source(self):
        transfer = PendingTransfer.objects.create(
            from_list=self.src, to_list=self.dest, person=self.child, requested_by=self.parent
        )
        self.client.force_login(self.dest_admin)
        self.client.post(
            reverse("lists:transfer_decide", kwargs={"transfer_pk": transfer.pk}),
            {"action": "reject"},
        )
        transfer.refresh_from_db()
        self.assertEqual(transfer.status, "rejected")
        self.rec_child.refresh_from_db()
        self.assertIsNone(self.rec_child.archived_at)

    def test_non_destination_admin_cannot_decide(self):
        transfer = PendingTransfer.objects.create(
            from_list=self.src, to_list=self.dest, person=self.child, requested_by=self.parent
        )
        outsider = _make_user(username="outsider")
        self.client.force_login(outsider)
        resp = self.client.post(
            reverse("lists:transfer_decide", kwargs={"transfer_pk": transfer.pk}),
            {"action": "accept"},
        )
        self.assertEqual(resp.status_code, 403)


class SelfRemovalTests(TestCase):
    def setUp(self):
        self.t = ListTemplate.objects.create(name="Schulklasse")
        self.lst = List.objects.create(
            title="5a", email_alias="5a", template=self.t,
            visibility=List.Visibility.PRIVATE,
        )
        self.user = _make_user(username="u1", email="u1@x.de")
        self.rec = ListRecord.objects.create(
            list=self.lst, subject=self.user.person, role=ListRecord.Role.MEMBER
        )
        RecordManager.objects.create(
            record=self.rec, user=self.user, basis=RecordManager.Basis.SELF_REGISTERED
        )
        ListAccess.objects.create(list=self.lst, user=self.user)
        self.client = Client()

    def test_remove_own_record_when_not_admin(self):
        self.client.force_login(self.user)
        resp = self.client.post(
            reverse("lists:record_remove", kwargs={"pk": self.lst.pk, "record_pk": self.rec.pk})
        )
        self.assertEqual(resp.status_code, 302)
        self.rec.refresh_from_db()
        self.assertIsNotNone(self.rec.archived_at)
        self.assertFalse(ListAccess.objects.filter(list=self.lst, user=self.user).exists())

    def test_sole_admin_self_removal_blocked(self):
        ListAdmin.objects.create(list=self.lst, user=self.user)
        self.client.force_login(self.user)
        self.client.post(
            reverse("lists:record_remove", kwargs={"pk": self.lst.pk, "record_pk": self.rec.pk})
        )
        self.rec.refresh_from_db()
        self.assertIsNone(self.rec.archived_at)
        self.assertTrue(ListAdmin.objects.filter(list=self.lst, user=self.user).exists())

    def test_self_removal_allowed_with_second_admin(self):
        ListAdmin.objects.create(list=self.lst, user=self.user)
        ListAdmin.objects.create(list=self.lst, user=_make_user(username="admin2"))
        self.client.force_login(self.user)
        self.client.post(
            reverse("lists:record_remove", kwargs={"pk": self.lst.pk, "record_pk": self.rec.pk})
        )
        self.rec.refresh_from_db()
        self.assertIsNotNone(self.rec.archived_at)
        self.assertFalse(ListAdmin.objects.filter(list=self.lst, user=self.user).exists())

    def test_guardian_removes_managed_child(self):
        child = Person.objects.create(given_name="Kind", family_name="X")
        child_rec = ListRecord.objects.create(
            list=self.lst, subject=child, role=ListRecord.Role.MEMBER
        )
        RecordManager.objects.create(
            record=child_rec, user=self.user, basis=RecordManager.Basis.GUARDIAN
        )
        self.client.force_login(self.user)
        self.client.post(
            reverse("lists:record_remove", kwargs={"pk": self.lst.pk, "record_pk": child_rec.pk})
        )
        child_rec.refresh_from_db()
        self.assertIsNotNone(child_rec.archived_at)
        self.assertTrue(ListAccess.objects.filter(list=self.lst, user=self.user).exists())


class AdminHandoverTests(TestCase):
    def setUp(self):
        self.t = ListTemplate.objects.create(name="Schulklasse")
        self.lst = List.objects.create(
            title="5a", email_alias="5a", template=self.t,
            visibility=List.Visibility.PRIVATE,
        )
        self.admin_a = _make_user(username="adminA", email="a@x.de")
        ListAdmin.objects.create(list=self.lst, user=self.admin_a)
        self.client = Client()

    def test_admin_manage_creates_token_and_mail(self):
        self.client.force_login(self.admin_a)
        resp = self.client.post(
            reverse("lists:admin_manage", kwargs={"pk": self.lst.pk}),
            {"to_email": "succ@x.de", "mode": "add"},
        )
        self.assertEqual(resp.status_code, 302)
        tok = AdminInviteToken.objects.get(list=self.lst, to_email="succ@x.de")
        self.assertEqual(tok.mode, "add")
        self.assertEqual(tok.from_user, self.admin_a)
        self.assertEqual(len(mail.outbox), 1)

    def test_add_mode_adds_admin(self):
        b = _make_user(username="adminB", email="b@x.de")
        tok = AdminInviteToken.objects.create(
            list=self.lst, from_user=self.admin_a, to_email="b@x.de",
            mode=AdminInviteToken.Mode.ADD,
        )
        self.client.force_login(b)
        resp = self.client.post(
            reverse("lists:admin_invite_accept", kwargs={"token": tok.token})
        )
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(ListAdmin.objects.filter(list=self.lst, user=b).exists())
        self.assertTrue(ListAdmin.objects.filter(list=self.lst, user=self.admin_a).exists())
        tok.refresh_from_db()
        self.assertIsNotNone(tok.consumed_at)

    def test_handover_removes_initiator(self):
        c = _make_user(username="adminC", email="c@x.de")
        tok = AdminInviteToken.objects.create(
            list=self.lst, from_user=self.admin_a, to_email="c@x.de",
            mode=AdminInviteToken.Mode.HANDOVER,
        )
        self.client.force_login(c)
        self.client.post(
            reverse("lists:admin_invite_accept", kwargs={"token": tok.token})
        )
        self.assertTrue(ListAdmin.objects.filter(list=self.lst, user=c).exists())
        self.assertFalse(ListAdmin.objects.filter(list=self.lst, user=self.admin_a).exists())

    def test_wrong_user_cannot_consume(self):
        # A logged-in USER whose email differs from to_email must NOT be able to
        # claim admin by possessing the link (privilege-escalation guard).
        intruder = _make_user(username="intruder", email="evil@x.de")
        tok = AdminInviteToken.objects.create(
            list=self.lst, from_user=self.admin_a, to_email="b@x.de",
            mode=AdminInviteToken.Mode.ADD,
        )
        self.client.force_login(intruder)
        resp = self.client.post(
            reverse("lists:admin_invite_accept", kwargs={"token": tok.token})
        )
        self.assertEqual(resp.status_code, 403)
        self.assertFalse(ListAdmin.objects.filter(list=self.lst, user=intruder).exists())
        tok.refresh_from_db()
        self.assertIsNone(tok.consumed_at)

    def test_unauth_post_does_not_consume(self):
        tok = AdminInviteToken.objects.create(
            list=self.lst, from_user=self.admin_a, to_email="new@x.de",
            mode=AdminInviteToken.Mode.ADD,
        )
        resp = self.client.post(
            reverse("lists:admin_invite_accept", kwargs={"token": tok.token})
        )
        self.assertEqual(resp.status_code, 302)
        tok.refresh_from_db()
        self.assertIsNone(tok.consumed_at)
        self.assertEqual(self.client.session.get("pending_admin_invite_token"), tok.token)

    def test_get_is_side_effect_free(self):
        b = _make_user(username="adminB", email="b@x.de")
        tok = AdminInviteToken.objects.create(
            list=self.lst, from_user=self.admin_a, to_email="b@x.de",
            mode=AdminInviteToken.Mode.ADD,
        )
        self.client.force_login(b)
        resp = self.client.get(
            reverse("lists:admin_invite_accept", kwargs={"token": tok.token})
        )
        self.assertEqual(resp.status_code, 200)
        tok.refresh_from_db()
        self.assertIsNone(tok.consumed_at)
        self.assertFalse(ListAdmin.objects.filter(list=self.lst, user=b).exists())

    def test_non_admin_cannot_manage(self):
        self.client.force_login(_make_user(username="outsider"))
        resp = self.client.get(reverse("lists:admin_manage", kwargs={"pk": self.lst.pk}))
        self.assertEqual(resp.status_code, 403)


# ---------------------------------------------------------------------------
# Phase 7a — Aggregate aliases
# ---------------------------------------------------------------------------


from django.core.exceptions import ValidationError  # noqa: E402
from django.db import IntegrityError, transaction  # noqa: E402

from .aggregates import (  # noqa: E402
    AggregateOutcome,
    aggregate_recipient_emails,
    aggregate_target_list_ids,
    decide_aggregate,
    subtree_list_ids,
)
from .models import AggregateAlias  # noqa: E402
from .tasks import enqueue_aggregate_fanout  # noqa: E402


def _person(given, family, email=None):
    return Person.objects.create(given_name=given, family_name=family, email=email)


@override_settings(
    MAIL_DOMAIN="caos.cloud",
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    DEFAULT_FROM_EMAIL="Fichtelink <noreply@caos.cloud>",
    RP_ORIGIN="https://fichtelink.caos.cloud",
)
class AggregateAliasBase(TestCase):
    """Shared school hierarchy: Elternbeirat → {5a, 5b}, children as member
    records, parents linked via PersonRelationship. `eltern` aggregates the
    parents of the whole Elternbeirat subtree by role.
    """

    def setUp(self):
        self.template = ListTemplate.objects.create(
            name="Schulklasse",
            relationship_roles=["Mutter von", "Vater von", "Erziehungsberechtigte von"],
        )
        self.eb = List.objects.create(
            title="Elternbeirat", email_alias="eb", template=self.template
        )
        self.c5a = List.objects.create(
            title="Klasse 5a", email_alias="5a", template=self.template, parent=self.eb
        )
        self.c5b = List.objects.create(
            title="Klasse 5b", email_alias="5b", template=self.template, parent=self.eb
        )

        # 5a: child with a mother, a father, and a (role-excluded) guardian.
        child_a = _person("Kind", "A")
        ListRecord.objects.create(
            list=self.c5a, subject=child_a, role=ListRecord.Role.MEMBER
        )
        self._link("Mutter von", child_a, "Mutter", "A", "m_a@x.org")
        self._link("Vater von", child_a, "Vater", "A", "f_a@x.org")
        # Role not in included_roles → must be excluded from the eltern alias.
        self._link("Erziehungsberechtigte von", child_a, "Oma", "A", "g_a@x.org")
        # A parent without email → never deliverable, must be excluded.
        self._link("Mutter von", child_a, "Stief", "A", None)

        # 5b: one child with a mother.
        child_b = _person("Kind", "B")
        ListRecord.objects.create(
            list=self.c5b, subject=child_b, role=ListRecord.Role.MEMBER
        )
        self._link("Mutter von", child_b, "Mutter", "B", "m_b@x.org")

        self.superu = _make_user(
            username="root", email="root@x.org", is_superuser=True, is_staff=True
        )
        self.agg = AggregateAlias.objects.create(
            email_alias="eltern",
            title="Eltern",
            scope_list=self.eb,
            included_roles=["Mutter von", "Vater von"],
            created_by=self.superu,
        )

    def _link(self, role, child, given, family, email):
        """Create a parent Person(+User) and relate them to `child`."""
        parent = _person(given, family, email)
        User.objects.create_user(
            person=parent, username=f"{given}{family}".lower()
        )
        PersonRelationship.objects.create(
            subject_person=child, related_person=parent, role=role
        )
        return parent


class AggregateResolutionTests(AggregateAliasBase):
    def test_subtree_includes_root_and_children(self):
        self.assertEqual(
            subtree_list_ids(self.eb.pk), {self.eb.pk, self.c5a.pk, self.c5b.pk}
        )

    def test_subtree_excludes_archived(self):
        self.c5b.archived_at = timezone.now()
        self.c5b.save(update_fields=["archived_at"])
        self.assertEqual(subtree_list_ids(self.eb.pk), {self.eb.pk, self.c5a.pk})

    def test_subtree_none_scope_is_whole_install(self):
        self.assertEqual(
            subtree_list_ids(None), {self.eb.pk, self.c5a.pk, self.c5b.pk}
        )

    def test_recipients_filtered_by_role_and_email(self):
        # Mutter/Vater of 5a + Mutter of 5b; guardian (wrong role) and the
        # email-less parent are excluded.
        self.assertEqual(
            set(aggregate_recipient_emails(self.agg)),
            {"m_a@x.org", "f_a@x.org", "m_b@x.org"},
        )

    def test_recipients_deduped_per_address(self):
        # A shared family mailbox: a second child's parent reuses an address.
        child_c = _person("Kind", "C")
        ListRecord.objects.create(
            list=self.c5a, subject=child_c, role=ListRecord.Role.MEMBER
        )
        self._link("Mutter von", child_c, "Mutter", "C", "m_a@x.org")
        emails = aggregate_recipient_emails(self.agg)
        self.assertEqual(emails.count("m_a@x.org"), 1)

    def test_recipients_respect_scope_subtree(self):
        # A class outside the Elternbeirat subtree contributes no recipients.
        other = List.objects.create(
            title="VHS", email_alias="vhs", template=self.template
        )
        child_x = _person("Kind", "X")
        ListRecord.objects.create(
            list=other, subject=child_x, role=ListRecord.Role.MEMBER
        )
        self._link("Mutter von", child_x, "Mutter", "X", "m_x@x.org")
        self.assertNotIn("m_x@x.org", aggregate_recipient_emails(self.agg))

    def test_target_list_ids_are_contributing_classes(self):
        # Only the classes that actually hold member-children with the roles —
        # the Elternbeirat itself has no such records.
        self.assertEqual(
            aggregate_target_list_ids(self.agg), {self.c5a.pk, self.c5b.pk}
        )


class AggregateDecisionTests(AggregateAliasBase):
    def test_elternbeirat_member_forwards_directly(self):
        # Vorsitz is a member of the Elternbeirat, the parent of every class →
        # implicit parent send grant on each → permitted, no click → FORWARD.
        vorsitz = _make_user(username="vorsitz", email="v@x.org")
        ListAccess.objects.create(list=self.eb, user=vorsitz)
        decision = decide_aggregate(self.agg, [vorsitz])
        self.assertEqual(decision.outcome, AggregateOutcome.FORWARD)
        self.assertEqual(decision.target_list_ids, frozenset({self.c5a.pk, self.c5b.pk}))

    def test_single_class_ev_routes_to_super_admin(self):
        # An ordinary Elternvertreter is a member of one class only and holds no
        # send permission on the others → super-admin approval.
        ev = _make_user(username="ev", email="ev@x.org")
        ListAccess.objects.create(list=self.c5a, user=ev)
        decision = decide_aggregate(self.agg, [ev])
        self.assertEqual(decision.outcome, AggregateOutcome.SUPER_ADMIN_APPROVAL)

    def test_unknown_sender_routes_to_super_admin(self):
        decision = decide_aggregate(self.agg, [])
        self.assertEqual(decision.outcome, AggregateOutcome.SUPER_ADMIN_APPROVAL)

    def test_release_click_required_when_any_target_demands_it(self):
        # Vorsitz is permitted everywhere, but an explicit grant on 5a tightens
        # the implicit parent default to require a click → PERMITTED_RELEASE,
        # because the aggregate takes the strictest per-list value.
        vorsitz = _make_user(username="vorsitz", email="v@x.org")
        ListAccess.objects.create(list=self.eb, user=vorsitz)
        ListSendPermission.objects.create(
            target_list=self.c5a,
            granted_to_list=self.eb,
            granted_by=self.superu,
            requires_release_click=True,
        )
        decision = decide_aggregate(self.agg, [vorsitz])
        self.assertEqual(decision.outcome, AggregateOutcome.PERMITTED_RELEASE)


class AggregateOutboundEmailTests(AggregateAliasBase):
    def test_build_outbound_munges_from_and_list_headers(self):
        om = OutboundMessage.objects.create(
            aggregate=self.agg,
            message_id="agg@caos.cloud",
            alias_token="A1",
            from_email="v@x.org",
            recipient_email="m_a@x.org",
            subject="Hallo Eltern",
        )
        msg = build_outbound_email(om, body="Text")
        # Envelope-from is the bounce alias; visible From: is munged onto our
        # domain (DMARC) using the aggregate's alias, sender kept in Reply-To.
        self.assertEqual(msg.from_email, "bounce-A1@caos.cloud")
        self.assertIn("alias-A1@caos.cloud", msg.extra_headers["From"])
        self.assertIn("via Eltern", msg.extra_headers["From"])
        self.assertEqual(msg.extra_headers["Reply-To"], "v@x.org")
        self.assertEqual(msg.extra_headers["List-Id"], "<eltern.caos.cloud>")
        self.assertEqual(msg.extra_headers["List-Post"], "<mailto:eltern@caos.cloud>")
        # No per-recipient detail page for an aggregate → site root.
        self.assertEqual(
            msg.extra_headers["List-Unsubscribe"], "<https://fichtelink.caos.cloud/>"
        )

    def test_enqueue_aggregate_fanout_tags_rows_with_aggregate(self):
        with patch.object(send_outbound_message, "defer") as defer:
            with self.captureOnCommitCallbacks(execute=True):
                created = enqueue_aggregate_fanout(
                    aggregate=self.agg,
                    recipients=["a@x.org", "b@x.org"],
                    from_email="v@x.org",
                    subject="S",
                    body="B",
                )
        self.assertEqual(len(created), 2)
        self.assertTrue(all(om.aggregate_id == self.agg.pk for om in created))
        self.assertTrue(all(om.list_id is None for om in created))
        self.assertEqual(defer.call_count, 2)


class AggregateProcessInboundTests(AggregateAliasBase):
    def _inbound(self, *, from_email, to_alias="eltern"):
        raw = _eml(from_addr=from_email, to_addr=f"{to_alias}@caos.cloud", subject="Hallo")
        return InboundMessage.objects.create(
            imap_uidvalidity=1,
            imap_uid=InboundMessage.objects.count() + 1,
            message_id=f"in-{from_email}@x.org",
            from_email=from_email,
            to_alias=to_alias,
            to_domain="caos.cloud",
            subject="Hallo",
            raw_eml=raw,
            received_at=timezone.now(),
            matched_list=None,
        )

    def test_permitted_sender_forwards_to_resolved_recipients(self):
        vorsitz = _make_user(username="vorsitz", email="v@x.org")
        ListAccess.objects.create(list=self.eb, user=vorsitz)
        inbound = self._inbound(from_email="v@x.org")
        with patch.object(send_outbound_message, "defer") as defer:
            with self.captureOnCommitCallbacks(execute=True):
                process_inbound.func(inbound_id=inbound.pk)
        inbound.refresh_from_db()
        self.assertEqual(inbound.decision, InboundMessage.Decision.FORWARDED)
        self.assertFalse(MailReleaseToken.objects.filter(inbound=inbound).exists())
        rows = OutboundMessage.objects.filter(aggregate=self.agg)
        self.assertEqual(
            set(rows.values_list("recipient_email", flat=True)),
            {"m_a@x.org", "f_a@x.org", "m_b@x.org"},
        )
        self.assertEqual(defer.call_count, 3)

    def test_unpermitted_sender_routes_to_super_admin_approval(self):
        ev = _make_user(username="ev", email="ev@x.org")
        ListAccess.objects.create(list=self.c5a, user=ev)
        inbound = self._inbound(from_email="ev@x.org")
        with patch.object(send_notification_mail, "defer") as notify:
            with self.captureOnCommitCallbacks(execute=True):
                process_inbound.func(inbound_id=inbound.pk)
        inbound.refresh_from_db()
        self.assertEqual(inbound.decision, InboundMessage.Decision.PENDING_APPROVAL)
        tok = MailReleaseToken.objects.get(inbound=inbound)
        self.assertEqual(tok.kind, MailReleaseToken.Kind.ADMIN)
        self.assertEqual(tok.aggregate_id, self.agg.pk)
        self.assertIsNone(tok.list_id)
        # Approval audience is the super-admin, not a list admin.
        self.assertEqual(notify.call_args.kwargs["recipients"], ["root@x.org"])

    def test_aggregate_mail_suppressed_on_auto_submitted(self):
        raw = _eml(
            from_addr="v@x.org",
            to_addr="eltern@caos.cloud",
            extra_headers={"Auto-Submitted": "auto-replied"},
        )
        inbound = InboundMessage.objects.create(
            imap_uidvalidity=1, imap_uid=99, message_id="s@x.org",
            from_email="v@x.org", to_alias="eltern", to_domain="caos.cloud",
            subject="x", raw_eml=raw, received_at=timezone.now(), matched_list=None,
        )
        with self.captureOnCommitCallbacks(execute=True):
            process_inbound.func(inbound_id=inbound.pk)
        inbound.refresh_from_db()
        self.assertEqual(inbound.decision, InboundMessage.Decision.SUPPRESSED)

    def test_real_unknown_alias_still_unknown(self):
        inbound = self._inbound(from_email="v@x.org", to_alias="ghost")
        process_inbound.func(inbound_id=inbound.pk)
        inbound.refresh_from_db()
        self.assertEqual(inbound.decision, InboundMessage.Decision.UNKNOWN_ALIAS)


@override_settings(
    MAIL_DOMAIN="caos.cloud",
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    DEFAULT_FROM_EMAIL="Fichtelink <noreply@caos.cloud>",
    RP_ORIGIN="https://fichtelink.caos.cloud",
)
class AggregateReleaseClickTests(AggregateAliasBase):
    def test_super_admin_approval_click_fans_out(self):
        inbound = InboundMessage.objects.create(
            imap_uidvalidity=1, imap_uid=1, message_id="rc@x.org",
            from_email="ev@x.org", to_alias="eltern", to_domain="caos.cloud",
            subject="Bitte", raw_eml=_eml(to_addr="eltern@caos.cloud"),
            received_at=timezone.now(), matched_list=None,
        )
        rel = MailReleaseToken.objects.create(
            inbound=inbound,
            aggregate=self.agg,
            kind=MailReleaseToken.Kind.ADMIN,
        )
        client = Client()
        with patch.object(send_outbound_message, "defer") as defer:
            with self.captureOnCommitCallbacks(execute=True):
                resp = client.post(
                    reverse("lists:mail_release", kwargs={"token": rel.token}),
                    {"action": "approve"},
                )
        self.assertEqual(resp.status_code, 200)
        rel.refresh_from_db()
        inbound.refresh_from_db()
        self.assertEqual(rel.resolution, MailReleaseToken.Resolution.FORWARDED)
        self.assertEqual(inbound.decision, InboundMessage.Decision.FORWARDED)
        self.assertEqual(
            set(
                OutboundMessage.objects.filter(aggregate=self.agg).values_list(
                    "recipient_email", flat=True
                )
            ),
            {"m_a@x.org", "f_a@x.org", "m_b@x.org"},
        )
        self.assertEqual(defer.call_count, 3)


class AggregateModelConstraintTests(AggregateAliasBase):
    def test_outbound_requires_exactly_one_source(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                OutboundMessage.objects.create(
                    message_id="bad@x", alias_token="BAD",
                    from_email="a@x.org", recipient_email="b@x.org",
                )

    def test_alias_collision_with_list_rejected(self):
        a = AggregateAlias(
            email_alias="5a", title="Kollision", created_by=self.superu
        )
        with self.assertRaises(ValidationError):
            a.full_clean()

    def test_reserved_prefix_rejected(self):
        a = AggregateAlias(
            email_alias="bounce-foo", title="Reserviert", created_by=self.superu
        )
        with self.assertRaises(ValidationError):
            a.full_clean()

    def test_clean_normalises_to_lowercase(self):
        a = AggregateAlias(
            email_alias="  Eltern2  ", title="Norm", created_by=self.superu
        )
        a.full_clean()
        self.assertEqual(a.email_alias, "eltern2")


class PostLoginLandingTests(TestCase):
    """The post-login landing resolution (CLAUDE.md / *Post-login landing*):
    last-selected list, else first accessible, else the index.
    """

    def setUp(self):
        self.template = ListTemplate.objects.create(name="Schulklasse")
        self.list_a = List.objects.create(
            title="Aaa-Liste",
            email_alias="aaa",
            template=self.template,
            visibility=List.Visibility.PRIVATE,
        )
        self.list_b = List.objects.create(
            title="Bbb-Liste",
            email_alias="bbb",
            template=self.template,
            visibility=List.Visibility.PRIVATE,
        )
        self.user = _make_user(username="u", given="Uschi", family="User", email="u@example.org")
        self.client = Client()

    def _join(self, lst):
        ListAccess.objects.create(list=lst, user=self.user)

    # --- helper ------------------------------------------------------------

    def test_no_lists_resolves_to_none(self):
        self.assertIsNone(resolve_default_list(self.user))

    def test_single_list_resolves_to_it(self):
        self._join(self.list_b)
        self.assertEqual(resolve_default_list(self.user), self.list_b)

    def test_first_accessible_when_no_last_selected(self):
        self._join(self.list_a)
        self._join(self.list_b)
        # Ordered by title → Aaa wins.
        self.assertEqual(resolve_default_list(self.user), self.list_a)

    def test_last_selected_takes_precedence(self):
        self._join(self.list_a)
        self._join(self.list_b)
        self.user.last_selected_list = self.list_b
        self.user.save(update_fields=["last_selected_list"])
        self.assertEqual(resolve_default_list(self.user), self.list_b)

    def test_archived_last_selected_falls_through(self):
        self._join(self.list_a)
        self._join(self.list_b)
        self.list_b.archived_at = timezone.now()
        self.list_b.save(update_fields=["archived_at"])
        self.user.last_selected_list = self.list_b
        self.user.save(update_fields=["last_selected_list"])
        # Archived → skip (b), fall through to first accessible non-archived (Aaa).
        self.assertEqual(resolve_default_list(self.user), self.list_a)

    def test_unseeable_last_selected_falls_through(self):
        # User was a member of B, that became their last_selected, then access
        # was revoked. Resolution must not return a list they can no longer see.
        self._join(self.list_a)
        self.user.last_selected_list = self.list_b
        self.user.save(update_fields=["last_selected_list"])
        self.assertEqual(resolve_default_list(self.user), self.list_a)

    def test_accessible_excludes_public_non_membership(self):
        public = List.objects.create(
            title="Öffentlich",
            email_alias="oeff",
            template=self.template,
            visibility=List.Visibility.PUBLIC_VISIBLE,
        )
        # Not a member of `public` → it must not count as accessible.
        self.assertNotIn(public, list(accessible_lists_for(self.user)))
        self.assertIsNone(resolve_default_list(self.user))

    # --- views -------------------------------------------------------------

    def test_home_redirects_to_default_list(self):
        self._join(self.list_b)
        self.client.force_login(self.user)
        resp = self.client.get(reverse("lists:home"))
        self.assertRedirects(
            resp,
            reverse("lists:detail", kwargs={"pk": self.list_b.pk}),
            fetch_redirect_response=False,
        )

    def test_home_redirects_to_index_when_no_lists(self):
        self.client.force_login(self.user)
        resp = self.client.get(reverse("lists:home"))
        self.assertRedirects(resp, reverse("lists:index"), fetch_redirect_response=False)

    def test_detail_records_last_selected(self):
        self._join(self.list_b)
        self.client.force_login(self.user)
        self.client.get(reverse("lists:detail", kwargs={"pk": self.list_b.pk}))
        self.user.refresh_from_db()
        self.assertEqual(self.user.last_selected_list_id, self.list_b.pk)

    def test_welcome_redirects_authenticated_user(self):
        self.client.force_login(self.user)
        resp = self.client.get("/")
        self.assertRedirects(resp, reverse("lists:home"), fetch_redirect_response=False)


class RecordSubjectNameEditTests(TestCase):
    """A RecordManager may edit the name of a subject who has no own USER
    (entered by someone else); once the subject self-registers it is read-only
    here and managed via "Mein Profil"."""

    def setUp(self):
        self.template = ListTemplate.objects.create(
            name="Schulklasse",
            member_subject_mode=ListTemplate.MemberSubjectMode.VIA_ASSOCIATE,
        )
        self.lst = List.objects.create(title="5a", email_alias="5a", template=self.template)
        mp = Person.objects.create(given_name="Eva", family_name="Mueller", email="eva@example.invalid")
        self.manager = User.objects.create_user(person=mp, username="eva")
        # Child: a PERSON without a USER, entered by the managing parent.
        self.child = Person.objects.create(given_name="Lina", family_name="Mueler")  # typo on purpose
        self.child_rec = ListRecord.objects.create(
            list=self.lst, subject=self.child, role=ListRecord.Role.MEMBER
        )
        RecordManager.objects.create(
            record=self.child_rec, user=self.manager, basis=RecordManager.Basis.GUARDIAN
        )

    def test_manager_can_edit_userless_subject_name(self):
        form = RecordEditForm(record=self.child_rec, user=self.manager)
        self.assertTrue(form.name_editable)
        form = RecordEditForm(
            {"subject_given_name": "Lina", "subject_family_name": "Mueller", "vis_name": []},
            record=self.child_rec, user=self.manager,
        )
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        self.child.refresh_from_db()
        self.assertEqual((self.child.given_name, self.child.family_name), ("Lina", "Mueller"))

    def test_name_read_only_once_subject_has_own_user(self):
        own_rec = ListRecord.objects.create(
            list=self.lst, subject=self.manager.person, role=ListRecord.Role.ASSOCIATE
        )
        RecordManager.objects.create(
            record=own_rec, user=self.manager, basis=RecordManager.Basis.SELF_REGISTERED
        )
        form = RecordEditForm(record=own_rec, user=self.manager)
        self.assertFalse(form.name_editable)
        self.assertNotIn("subject_given_name", form.fields)
        self.assertNotIn("subject_email", form.fields)

    def test_manager_can_set_userless_subject_email(self):
        form = RecordEditForm(
            {"subject_given_name": "Lina", "subject_family_name": "Mueller",
             "subject_email": "lina@example.invalid", "vis_name": []},
            record=self.child_rec, user=self.manager,
        )
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        self.child.refresh_from_db()
        self.assertEqual(self.child.email, "lina@example.invalid")

    def test_blank_subject_email_stored_as_null(self):
        self.child.email = "old@example.invalid"
        self.child.save(update_fields=["email"])
        form = RecordEditForm(
            {"subject_given_name": "Lina", "subject_family_name": "Mueller",
             "subject_email": "", "vis_name": []},
            record=self.child_rec, user=self.manager,
        )
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        self.child.refresh_from_db()
        self.assertIsNone(self.child.email)

    def test_tampered_name_post_ignored_when_not_editable(self):
        own_rec = ListRecord.objects.create(
            list=self.lst, subject=self.manager.person, role=ListRecord.Role.ASSOCIATE
        )
        RecordManager.objects.create(
            record=own_rec, user=self.manager, basis=RecordManager.Basis.SELF_REGISTERED
        )
        form = RecordEditForm(
            {"subject_given_name": "Hacked", "subject_family_name": "Name", "vis_name": []},
            record=own_rec, user=self.manager,
        )
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        self.manager.person.refresh_from_db()
        self.assertEqual(self.manager.person.given_name, "Eva")  # unchanged
