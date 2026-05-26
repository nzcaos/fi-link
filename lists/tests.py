"""Phase-3a unit tests: visibility service + creation permissions.

Tests run on the deploy VM (`docker compose run --rm web python manage.py test
lists`). Lokal kein Runtime-Stack vorhanden.
"""
from __future__ import annotations

from django.test import TestCase

from accounts.models import Person, User
from .models import (
    List,
    ListAccess,
    ListAdmin,
    ListAttribute,
    ListRecord,
    ListRecordAccess,
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
