"""Phase 2 auth tests — focus on the bits we own (not py_webauthn itself).

py_webauthn's correctness is its own problem; we test the seams: timing
floor, shared-email resolution, challenge lifecycle, activation token,
deterministic decoys.
"""
from __future__ import annotations

import json
import time
from datetime import timedelta

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import ActivationToken, Passkey, Person, User, WebAuthnChallenge
from .webauthn_service import (
    LOGIN_FLOOR_SECONDS,
    _consume_challenge,
    _fake_credentials,
    _store_challenge,
    begin_authentication,
    random_username,
)


def _make_user(email: str, given: str = "Anna", family: str = "Müller") -> User:
    person = Person.objects.create(given_name=given, family_name=family, email=email)
    return User.objects.create_user(person=person, username=random_username())


def _grant_registration(client, *, is_open: bool = True):
    """Open the registration gate for a test client by stashing a usable
    invitation in the session — registration is invitation-only (see
    `accounts.views._registration_grant`). A form's broadcast token is the
    cheapest of the four accepted grants to construct.
    """
    from forms.models import Form

    owner = _make_user(email="owner@example.org", given="Olga", family="Owner")
    form = Form.objects.create(
        title="Sommerfest",
        created_by=owner,
        share_token="share-token-for-tests",
        share_expires_on=timezone.localdate() + timedelta(days=30),
        is_open=is_open,
    )
    session = client.session
    session["pending_form_token"] = form.share_token
    session.save()
    return form


def _make_passkey(user: User, label: str = "test") -> Passkey:
    # Bytes don't need to be a real key — we never invoke py_webauthn in these tests.
    return Passkey.objects.create(
        user=user,
        credential_id=label.encode("utf-8") + b"-cred-id-32-bytes-of-data-padding"[:32],
        public_key=b"\x00" * 65,
        sign_count=0,
        label=label,
    )


@override_settings(RP_ID="test.local", RP_ORIGIN="https://test.local", RP_NAME="T")
class LoginBeginTimingTests(TestCase):
    def test_login_begin_enforces_floor_for_unknown_email(self):
        start = time.monotonic()
        begin_authentication(email="nobody@example.org", started_at=start)
        elapsed = time.monotonic() - start
        self.assertGreaterEqual(elapsed, LOGIN_FLOOR_SECONDS)

    def test_login_begin_enforces_floor_for_known_email(self):
        user = _make_user(email="known@example.org")
        _make_passkey(user, label="iphone")
        start = time.monotonic()
        begin_authentication(email="known@example.org", started_at=start)
        elapsed = time.monotonic() - start
        self.assertGreaterEqual(elapsed, LOGIN_FLOOR_SECONDS)


@override_settings(RP_ID="test.local", RP_ORIGIN="https://test.local", RP_NAME="T")
class LoginBeginSharedEmailTests(TestCase):
    def test_shared_family_email_returns_all_passkeys(self):
        # Two USERs, same PERSON.email — the parent-mailbox case.
        mother = _make_user(email="family@example.org", given="Anna")
        father = _make_user(email="family@example.org", given="Bernd")
        _make_passkey(mother, label="mom-iphone")
        _make_passkey(father, label="dad-laptop")

        opts = begin_authentication(email="family@example.org", started_at=time.monotonic())
        options = json.loads(opts.options_json)
        cred_ids = {c["id"] for c in options.get("allowCredentials", [])}
        self.assertEqual(len(cred_ids), 2, options)

    def test_unknown_email_returns_fake_credentials(self):
        opts = begin_authentication(email="nobody@example.org", started_at=time.monotonic())
        options = json.loads(opts.options_json)
        self.assertEqual(len(options.get("allowCredentials", [])), 2)

    def test_fake_credentials_are_deterministic_per_email(self):
        a = _fake_credentials("user@example.org")
        b = _fake_credentials("user@example.org")
        c = _fake_credentials("other@example.org")
        self.assertEqual([d.id for d in a], [d.id for d in b])
        self.assertNotEqual([d.id for d in a], [d.id for d in c])


class ChallengeLifecycleTests(TestCase):
    def test_consume_challenge_is_single_use(self):
        challenge = b"\x01" * 32
        _store_challenge(challenge, WebAuthnChallenge.Purpose.LOGIN)
        first = _consume_challenge(challenge, WebAuthnChallenge.Purpose.LOGIN)
        self.assertIsNotNone(first)
        second = _consume_challenge(challenge, WebAuthnChallenge.Purpose.LOGIN)
        self.assertIsNone(second)

    def test_expired_challenge_is_not_returned(self):
        challenge = b"\x02" * 32
        WebAuthnChallenge.objects.create(
            challenge=challenge,
            purpose=WebAuthnChallenge.Purpose.LOGIN,
            expires_at=timezone.now() - timedelta(seconds=1),
        )
        result = _consume_challenge(challenge, WebAuthnChallenge.Purpose.LOGIN)
        self.assertIsNone(result)
        # Still removed from the table to keep it tidy.
        self.assertFalse(WebAuthnChallenge.objects.filter(challenge=challenge).exists())

    def test_purpose_mismatch_does_not_consume(self):
        challenge = b"\x03" * 32
        _store_challenge(challenge, WebAuthnChallenge.Purpose.LOGIN)
        wrong = _consume_challenge(challenge, WebAuthnChallenge.Purpose.REGISTER)
        self.assertIsNone(wrong)
        # Row is still there because purpose didn't match.
        self.assertTrue(WebAuthnChallenge.objects.filter(challenge=challenge).exists())


class ActivationTokenTests(TestCase):
    def test_fresh_token_is_valid(self):
        person = Person.objects.create(given_name="A", family_name="B", email="x@y.de")
        tok = ActivationToken.issue(person=person, email="x@y.de")
        self.assertTrue(tok.is_valid())

    def test_consumed_token_invalid(self):
        person = Person.objects.create(given_name="A", family_name="B", email="x@y.de")
        tok = ActivationToken.issue(person=person, email="x@y.de")
        tok.consumed_at = timezone.now()
        tok.save()
        self.assertFalse(tok.is_valid())

    def test_expired_token_invalid(self):
        person = Person.objects.create(given_name="A", family_name="B", email="x@y.de")
        tok = ActivationToken.issue(person=person, email="x@y.de", ttl=timedelta(seconds=-1))
        self.assertFalse(tok.is_valid())


class RegisterStartViewTests(TestCase):
    def setUp(self):
        # Registration is invitation-only; these tests exercise what happens
        # *after* the gate, so every client starts with a valid grant.
        _grant_registration(self.client)

    def test_get_renders_form(self):
        resp = self.client.get("/auth/register/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Konto anlegen")

    def test_post_creates_stub_and_sends_link(self):
        from django.core import mail

        resp = self.client.post(
            "/auth/register/",
            {"email": "new@example.org", "given_name": "Anna", "family_name": "Müller", "privacy_consent": "on"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "E-Mail-Postfach prüfen")
        self.assertTrue(Person.objects.filter(email="new@example.org").exists())
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("aktivieren", mail.outbox[0].body.lower())

    def test_existing_email_shows_continue_prompt_not_silent_overwrite(self):
        _make_user(email="parent@example.org")
        resp = self.client.post(
            "/auth/register/",
            {"email": "parent@example.org", "given_name": "Bernd", "family_name": "Müller", "privacy_consent": "on"},
        )
        self.assertEqual(resp.status_code, 200)
        # The form should offer to continue under the shared address (family-mailbox case).
        self.assertContains(resp, "weiteres Konto")

    def test_retry_for_pending_stub_resends_link_instead_of_new_stub(self):
        # First attempt created the stub but the mail never arrived (SMTP
        # outage). Re-submitting the same data must re-mail, not fork a stub.
        from django.core import mail

        data = {"email": "retry@example.org", "given_name": "Anna", "family_name": "Müller", "privacy_consent": "on"}
        self.client.post("/auth/register/", data)
        mail.outbox.clear()

        resp = self.client.post("/auth/register/", {**data, "email": "Retry@Example.org", "given_name": "anna"})
        self.assertContains(resp, "E-Mail-Postfach prüfen")
        self.assertEqual(Person.objects.filter(email__iexact="retry@example.org").count(), 1)
        user = User.objects.get(person__email="retry@example.org")
        self.assertFalse(user.is_active)
        self.assertEqual(ActivationToken.objects.filter(user=user).count(), 2)
        newest = ActivationToken.objects.filter(user=user).order_by("-pk").first()
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn(newest.token, mail.outbox[0].body)

    def test_retry_keeps_the_stubs_own_invitation(self):
        # The resent link carries the invitation of the stub's first link, not
        # whatever grant the (possibly different) submitter holds.
        person = Person.objects.create(given_name="Vera", family_name="V", email="vera@example.org")
        stub = User.objects.create_user(person=person, username=random_username(), is_active=False)
        ActivationToken.issue(
            person=person, email="vera@example.org", user=stub,
            pending_key="pending_join_token", pending_token="orig-5a",
        )
        self.client.post(
            "/auth/register/",
            {"email": "vera@example.org", "given_name": "Vera", "family_name": "V", "privacy_consent": "on"},
        )
        newest = ActivationToken.objects.filter(user=stub).order_by("-pk").first()
        self.assertEqual((newest.pending_key, newest.pending_token), ("pending_join_token", "orig-5a"))
        self.assertIsNone(newest.consumed_at)

    def test_deactivated_account_gets_no_new_link(self):
        from django.core import mail
        from django.utils import timezone as tz

        user = _make_user(email="gesperrt@example.org", given="Anna", family="Müller")
        User.objects.filter(pk=user.pk).update(is_active=False, last_login=tz.now())
        mail.outbox.clear()
        resp = self.client.post(
            "/auth/register/",
            {"email": "gesperrt@example.org", "given_name": "Anna", "family_name": "Müller", "privacy_consent": "on"},
        )
        self.assertContains(resp, "weiteres Konto")
        self.assertFalse(ActivationToken.objects.filter(user=user).exists())

    def test_pending_stub_with_other_name_keeps_continue_prompt(self):
        # Same mailbox, different person (second parent) → not a retry.
        data = {"email": "family@example.org", "given_name": "Anna", "family_name": "Müller", "privacy_consent": "on"}
        self.client.post("/auth/register/", data)
        resp = self.client.post("/auth/register/", {**data, "given_name": "Bernd"})
        self.assertContains(resp, "weiteres Konto")
        self.assertEqual(Person.objects.filter(email="family@example.org").count(), 1)

    def test_activated_account_is_not_resent(self):
        from django.core import mail

        _make_user(email="active@example.org", given="Anna", family="Müller")
        resp = self.client.post(
            "/auth/register/",
            {"email": "active@example.org", "given_name": "Anna", "family_name": "Müller", "privacy_consent": "on"},
        )
        self.assertContains(resp, "weiteres Konto")
        self.assertEqual(len(mail.outbox), 0)


class N15RegisterRateLimitTests(TestCase):
    """N15: register_start and register_force must throttle per-IP so a
    script cannot pump Person rows or mailings to arbitrary addresses.
    """

    def setUp(self):
        # The throttle bucket lives in Django's default cache; clear it
        # between tests so the per-IP counter starts fresh.
        from django.core.cache import cache

        cache.clear()
        _grant_registration(self.client)

    def _post_register(self, email: str, **kwargs):
        return self.client.post(
            "/auth/register/",
            {
                "email": email,
                "given_name": "Anna",
                "family_name": "Müller",
                "privacy_consent": "on",
            },
            **kwargs,
        )

    def _post_force(self, email: str, **kwargs):
        return self.client.post(
            "/auth/register/force/",
            {
                "email": email,
                "given_name": "Anna",
                "family_name": "Müller",
                "privacy_consent": "on",
            },
            **kwargs,
        )

    def test_register_start_blocks_after_limit(self):
        """The 11th request from the same IP within the window is rejected.
        The limit constant is 10; we don't pin the exact number here, just
        the shape: many requests in a row eventually 429.
        """
        from accounts.views import _REG_RATE_LIMIT

        # First _REG_RATE_LIMIT requests succeed.
        for i in range(_REG_RATE_LIMIT):
            resp = self._post_register(f"user{i}@example.org")
            self.assertEqual(resp.status_code, 200, f"request {i+1} unexpectedly 429")
            self.assertNotContains(resp, "Zu viele Registrierungsversuche")

        # The next one gets throttled.
        resp = self._post_register("over@example.org")
        self.assertEqual(resp.status_code, 429)
        self.assertContains(resp, "Zu viele Registrierungsversuche", status_code=429)
        # And critically: no Person row from the rejected attempt.
        self.assertFalse(Person.objects.filter(email="over@example.org").exists())

    def test_register_force_also_throttled(self):
        from accounts.views import _REG_RATE_LIMIT

        for i in range(_REG_RATE_LIMIT):
            resp = self._post_force(f"force{i}@example.org")
            self.assertEqual(resp.status_code, 200)
        resp = self._post_force("over@example.org")
        self.assertEqual(resp.status_code, 429)
        self.assertFalse(Person.objects.filter(email="over@example.org").exists())

    def test_buckets_are_per_ip(self):
        """Different REMOTE_ADDR values do not share the counter."""
        from accounts.views import _REG_RATE_LIMIT

        # Exhaust the budget from IP A.
        for i in range(_REG_RATE_LIMIT):
            resp = self._post_register(f"a{i}@example.org", REMOTE_ADDR="10.0.0.1")
            self.assertEqual(resp.status_code, 200)
        resp = self._post_register("a-blocked@example.org", REMOTE_ADDR="10.0.0.1")
        self.assertEqual(resp.status_code, 429)

        # IP B still has a fresh budget.
        resp = self._post_register("b@example.org", REMOTE_ADDR="10.0.0.2")
        self.assertEqual(resp.status_code, 200)

    def test_xff_header_respected_for_throttle_key(self):
        """When XFF is present, the left-most hop is the throttle key."""
        from accounts.views import _REG_RATE_LIMIT

        # Exhaust budget for XFF client 203.0.113.5 through proxy 10.0.0.1.
        for i in range(_REG_RATE_LIMIT):
            resp = self._post_register(
                f"xff{i}@example.org",
                REMOTE_ADDR="10.0.0.1",
                HTTP_X_FORWARDED_FOR="203.0.113.5",
            )
            self.assertEqual(resp.status_code, 200)
        # A request from a different XFF client through the same proxy must
        # still pass (key is the XFF client, not the proxy).
        resp = self._post_register(
            "different-client@example.org",
            REMOTE_ADDR="10.0.0.1",
            HTTP_X_FORWARDED_FOR="203.0.113.99",
        )
        self.assertEqual(resp.status_code, 200)


class RegistrationGateTests(TestCase):
    """Registration is invitation-only (CLAUDE.md / "Registration requires an
    invitation"). Without a usable token no account is created and no mail is
    sent — that combination is what the 2026-09-17 abuse exploited.
    """

    def setUp(self):
        from django.core.cache import cache

        cache.clear()

    def _payload(self, email="stranger@example.org"):
        return {"email": email, "given_name": "Anna", "family_name": "Müller", "privacy_consent": "on"}

    def _stash(self, key: str, value: str):
        session = self.client.session
        session[key] = value
        session.save()

    def _list(self):
        from lists.models import List, ListTemplate

        template = ListTemplate.objects.create(name="Schulklasse")
        return List.objects.create(
            title="Klasse 5a", email_alias="5a-gate", template=template
        )

    # --- closed ----------------------------------------------------------

    def test_get_without_invitation_is_blocked(self):
        resp = self.client.get("/auth/register/")
        self.assertEqual(resp.status_code, 403)
        self.assertContains(resp, "Registrierung nur mit Einladung", status_code=403)

    def test_post_without_invitation_creates_nothing_and_sends_no_mail(self):
        from django.core import mail

        resp = self.client.post("/auth/register/", self._payload())
        self.assertEqual(resp.status_code, 403)
        self.assertFalse(Person.objects.filter(email="stranger@example.org").exists())
        self.assertEqual(len(mail.outbox), 0)

    def test_register_force_without_invitation_is_blocked(self):
        from django.core import mail

        resp = self.client.post("/auth/register/force/", self._payload())
        self.assertEqual(resp.status_code, 403)
        self.assertFalse(Person.objects.filter(email="stranger@example.org").exists())
        self.assertEqual(len(mail.outbox), 0)

    def test_unknown_token_value_does_not_open_gate(self):
        """A guessed/stale session value must be checked against the DB, not
        merely be present."""
        self._stash("pending_invite_token", "does-not-exist")
        self.assertEqual(self.client.get("/auth/register/").status_code, 403)

    def test_expired_invite_does_not_open_gate(self):
        from lists.models import ListInviteToken

        inviter = _make_user(email="inviter@example.org")
        invite = ListInviteToken.objects.create(
            list=self._list(),
            invited_by=inviter,
            target_email="guest@example.org",
            expires_at=timezone.now() - timedelta(days=1),
        )
        self._stash("pending_invite_token", invite.token)
        self.assertEqual(self.client.get("/auth/register/").status_code, 403)

    def test_consumed_invite_does_not_open_gate(self):
        from lists.models import ListInviteToken

        inviter = _make_user(email="inviter@example.org")
        invite = ListInviteToken.objects.create(
            list=self._list(),
            invited_by=inviter,
            target_email="guest@example.org",
            consumed_at=timezone.now(),
        )
        self._stash("pending_invite_token", invite.token)
        self.assertEqual(self.client.get("/auth/register/").status_code, 403)

    def test_closed_form_does_not_open_gate(self):
        form = _grant_registration(self.client, is_open=False)
        self.assertFalse(form.is_open)
        self.assertEqual(self.client.get("/auth/register/").status_code, 403)

    # --- open ------------------------------------------------------------

    def test_valid_invite_opens_gate(self):
        from lists.models import ListInviteToken

        inviter = _make_user(email="inviter@example.org")
        invite = ListInviteToken.objects.create(
            list=self._list(),
            invited_by=inviter,
            target_email="guest@example.org",
        )
        self._stash("pending_invite_token", invite.token)
        resp = self.client.get("/auth/register/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Konto anlegen")

    def test_valid_qr_join_token_opens_gate_and_allows_signup(self):
        from django.core import mail
        from lists.models import ListJoinToken

        creator = _make_user(email="creator@example.org")
        join = ListJoinToken.objects.create(list=self._list(), created_by=creator)
        self._stash("pending_join_token", join.token)
        resp = self.client.post("/auth/register/", self._payload("qr@example.org"))
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(Person.objects.filter(email="qr@example.org").exists())
        self.assertEqual(len(mail.outbox), 1)

    def test_revoked_qr_join_token_does_not_open_gate(self):
        from lists.models import ListJoinToken

        creator = _make_user(email="creator@example.org")
        join = ListJoinToken.objects.create(
            list=self._list(), created_by=creator, revoked_at=timezone.now()
        )
        self._stash("pending_join_token", join.token)
        self.assertEqual(self.client.get("/auth/register/").status_code, 403)

    def test_form_share_token_opens_gate(self):
        _grant_registration(self.client)
        resp = self.client.get("/auth/register/")
        self.assertEqual(resp.status_code, 200)

    def test_gate_does_not_consume_the_session_key(self):
        """`_next_url_after_auth` needs the key after the passkey ceremony to
        finish the join — the gate must read it, not pop it."""
        form = _grant_registration(self.client)
        self.client.get("/auth/register/")
        self.assertEqual(
            self.client.session.get("pending_form_token"), form.share_token
        )


class RegisterTargetHintTests(TestCase):
    """After a QR scan into a via_associate list (school class), the
    registration form must say the data is the parent's own, not the child's."""

    def _stash_join(self, mode):
        from lists.models import List, ListJoinToken, ListTemplate

        owner = _make_user(email="admin@example.org", given="Ada", family="Admin")
        tpl = ListTemplate.objects.create(name=f"T-{mode}", member_subject_mode=mode)
        lst = List.objects.create(title="Klasse 5a", email_alias=f"k-{mode}", template=tpl)
        join = ListJoinToken.objects.create(list=lst, created_by=owner)
        session = self.client.session
        session["pending_join_token"] = join.token
        session.save()

    def test_via_associate_says_own_data_not_child(self):
        from lists.models import ListTemplate

        self._stash_join(ListTemplate.MemberSubjectMode.VIA_ASSOCIATE)
        resp = self.client.get("/auth/register/")
        self.assertContains(resp, "nicht</strong> die Ihres Kindes")
        self.assertContains(resp, "Klasse 5a")

    def test_self_mode_has_no_child_hint(self):
        from lists.models import ListTemplate

        self._stash_join(ListTemplate.MemberSubjectMode.SELF)
        resp = self.client.get("/auth/register/")
        self.assertNotContains(resp, "Ihres Kindes")
        self.assertContains(resp, "Sie tragen sich selbst")


class ActivationResumesInvitationTests(TestCase):
    """The QR join used to live only in the session. An activation link opened
    in another browser (mail app's in-app browser, second device) lost it and
    left a fresh account without its list. The invitation now rides on the
    ActivationToken and is resumed at enrolment."""

    def setUp(self):
        from lists.models import List, ListJoinToken, ListTemplate

        owner = _make_user(email="admin@example.org", given="Ada", family="Admin")
        tpl = ListTemplate.objects.create(
            name="Schulklasse",
            member_subject_mode=ListTemplate.MemberSubjectMode.VIA_ASSOCIATE,
        )
        self.lst = List.objects.create(title="Klasse 5a", email_alias="5a-resume", template=tpl)
        self.join = ListJoinToken.objects.create(list=self.lst, created_by=owner)

    def test_registration_stores_invitation_on_token(self):
        session = self.client.session
        session["pending_join_token"] = self.join.token
        session.save()
        self.client.post("/auth/register/", {
            "email": "neu@example.org", "given_name": "Nina", "family_name": "Neu",
            "privacy_consent": "on",
        })
        tok = ActivationToken.objects.get(email="neu@example.org")
        self.assertEqual(tok.pending_key, "pending_join_token")
        self.assertEqual(tok.pending_token, self.join.token)

    def test_enrolment_in_fresh_browser_still_joins_the_list(self):
        from types import SimpleNamespace
        from unittest.mock import patch

        from lists.models import ListAccess

        person = Person.objects.create(given_name="Nina", family_name="Neu", email="neu@example.org")
        user = User.objects.create_user(person=person, username=random_username(), is_active=False)
        tok = ActivationToken.issue(
            person=person, email=person.email, user=user,
            pending_key="pending_join_token", pending_token=self.join.token,
        )
        fake = SimpleNamespace(credential_id=b"cred-1", public_key=b"\x00", sign_count=0, transports=[])
        # A brand-new client: no session from the QR scan at all.
        with patch("accounts.views.finish_registration", return_value=fake):
            resp = self.client.post(
                reverse("accounts:register_passkey_finish"),
                data=json.dumps({"token": tok.token, "credential": {"id": "x"}}),
                content_type="application/json",
            )
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(
            resp.json()["redirect"],
            reverse("lists:record_create_associate", kwargs={"pk": self.lst.pk}),
        )
        self.assertTrue(ListAccess.objects.filter(list=self.lst, user=user).exists())


class ProfileViewTests(TestCase):
    def setUp(self):
        self.person = Person.objects.create(
            given_name="Erwin", family_name="Familie E", email="erwin@example.invalid"
        )
        self.user = User.objects.create_user(person=self.person, username="erwin")

    def test_requires_login(self):
        resp = self.client.get(reverse("accounts:profile"))
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/auth/login/", resp["Location"])

    def test_get_shows_name_and_readonly_email(self):
        self.client.force_login(self.user)
        resp = self.client.get(reverse("accounts:profile"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Erwin")
        self.assertContains(resp, "erwin@example.invalid")
        self.assertContains(resp, "nicht geändert werden")  # email read-only note

    def test_post_updates_name(self):
        self.client.force_login(self.user)
        resp = self.client.post(
            reverse("accounts:profile"),
            {"given_name": "Erwin", "family_name": "Müller-Lüdenscheidt"},
        )
        self.assertEqual(resp.status_code, 302)
        self.person.refresh_from_db()
        self.assertEqual(self.person.family_name, "Müller-Lüdenscheidt")

    def test_post_does_not_change_email(self):
        self.client.force_login(self.user)
        # Even if an email field is smuggled into the POST, it must be ignored.
        self.client.post(
            reverse("accounts:profile"),
            {"given_name": "Erwin", "family_name": "Familie E", "email": "attacker@evil.invalid"},
        )
        self.person.refresh_from_db()
        self.assertEqual(self.person.email, "erwin@example.invalid")

    def test_empty_name_rejected(self):
        self.client.force_login(self.user)
        resp = self.client.post(
            reverse("accounts:profile"),
            {"given_name": "", "family_name": ""},
        )
        self.assertEqual(resp.status_code, 200)  # re-rendered with errors
        self.person.refresh_from_db()
        self.assertEqual(self.person.given_name, "Erwin")


class RecoverViewTests(TestCase):
    """Self-service passkey recovery: a fresh enrollment link is mailed to the
    on-file address, no admin involved. The endpoint is enumeration-resistant
    and additive (it must not delete existing passkeys).
    """

    def setUp(self):
        from django.core.cache import cache

        cache.clear()

    def test_get_renders_form(self):
        resp = self.client.get(reverse("accounts:recover"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Zugang wiederherstellen")

    def test_known_email_issues_recover_token_and_mails_link(self):
        from django.core import mail

        user = _make_user(email="locked@example.org")
        resp = self.client.post(reverse("accounts:recover"), {"email": "locked@example.org"})
        self.assertEqual(resp.status_code, 200)
        token = ActivationToken.objects.get(user=user)
        self.assertEqual(token.purpose, ActivationToken.Purpose.RECOVER)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn(token.token, mail.outbox[0].body)
        self.assertEqual(mail.outbox[0].to, ["locked@example.org"])

    def test_recovery_does_not_delete_existing_passkeys(self):
        """The lost-on-phone case leaves a stale Passkey row; recovery is
        additive, so we must not wipe it (the user prunes it after signing in).
        """
        user = _make_user(email="stale@example.org")
        _make_passkey(user, label="altes-handy")
        self.client.post(reverse("accounts:recover"), {"email": "stale@example.org"})
        self.assertEqual(user.passkeys.count(), 1)

    def test_unknown_email_is_enumeration_resistant(self):
        from django.core import mail

        resp = self.client.post(reverse("accounts:recover"), {"email": "nobody@example.org"})
        self.assertEqual(resp.status_code, 200)
        # Same confirmation copy as the known case…
        self.assertContains(resp, "Falls ein Konto")
        # …but nothing was created or sent.
        self.assertEqual(ActivationToken.objects.count(), 0)
        self.assertEqual(len(mail.outbox), 0)

    def test_inactive_account_is_not_recoverable(self):
        """A never-activated stub (registration not finished) is not a recovery
        target — that path is registration, not recovery.
        """
        from django.core import mail

        person = Person.objects.create(given_name="Stub", family_name="X", email="stub@example.org")
        User.objects.create_user(person=person, username=random_username(), is_active=False)
        self.client.post(reverse("accounts:recover"), {"email": "stub@example.org"})
        self.assertEqual(ActivationToken.objects.count(), 0)
        self.assertEqual(len(mail.outbox), 0)

    def test_shared_family_email_gets_one_link_per_account(self):
        from django.core import mail

        mother = _make_user(email="family@example.org", given="Anna")
        father = _make_user(email="family@example.org", given="Bernd")
        resp = self.client.post(reverse("accounts:recover"), {"email": "family@example.org"})
        self.assertEqual(resp.status_code, 200)
        # One RECOVER token per account, both links in a single mail.
        self.assertEqual(ActivationToken.objects.filter(user=mother).count(), 1)
        self.assertEqual(ActivationToken.objects.filter(user=father).count(), 1)
        self.assertEqual(len(mail.outbox), 1)
        body = mail.outbox[0].body
        for token in ActivationToken.objects.all():
            self.assertIn(token.token, body)

    def test_rate_limited(self):
        from accounts.views import _REG_RATE_LIMIT

        for _ in range(_REG_RATE_LIMIT):
            resp = self.client.post(reverse("accounts:recover"), {"email": "x@example.org"})
            self.assertEqual(resp.status_code, 200)
        resp = self.client.post(reverse("accounts:recover"), {"email": "x@example.org"})
        self.assertEqual(resp.status_code, 429)


@override_settings(TOKEN_RETENTION_DAYS=30)
class RetentionSweepTests(TestCase):
    def test_sweep_activation_tokens(self):
        from .tasks import sweep_activation_tokens

        user = _make_user("t@example.org")
        old = ActivationToken.issue(person=user.person, email="t@example.org", user=user)
        ActivationToken.objects.filter(pk=old.pk).update(
            expires_at=timezone.now() - timedelta(days=40)
        )
        used = ActivationToken.issue(person=user.person, email="t@example.org", user=user)
        ActivationToken.objects.filter(pk=used.pk).update(
            consumed_at=timezone.now() - timedelta(days=31)
        )
        live = ActivationToken.issue(person=user.person, email="t@example.org", user=user)

        sweep_activation_tokens.func(timestamp=0)

        self.assertEqual(
            list(ActivationToken.objects.values_list("pk", flat=True)), [live.pk]
        )

    def test_clear_expired_sessions(self):
        from django.contrib.sessions.models import Session

        from .tasks import clear_expired_sessions

        Session.objects.create(
            session_key="old", session_data="", expire_date=timezone.now() - timedelta(days=1)
        )
        Session.objects.create(
            session_key="new", session_data="", expire_date=timezone.now() + timedelta(days=1)
        )
        clear_expired_sessions.func(timestamp=0)
        self.assertEqual(list(Session.objects.values_list("session_key", flat=True)), ["new"])


class PrivacyConsentRegistrationTests(TestCase):
    def setUp(self):
        _grant_registration(self.client)

    def test_register_without_consent_stores_nothing(self):
        from django.core import mail

        resp = self.client.post(
            "/auth/register/",
            {"email": "new@example.org", "given_name": "Anna", "family_name": "Müller"},
        )
        self.assertEqual(resp.status_code, 400)
        self.assertContains(resp, "Datenschutzhinweise", status_code=400)
        self.assertFalse(Person.objects.filter(email="new@example.org").exists())
        self.assertEqual(len(mail.outbox), 0)

    @override_settings(PRIVACY_POLICY_VERSION="2026-10-12")
    def test_register_with_consent_records_it(self):
        self.client.post(
            "/auth/register/",
            {
                "email": "new@example.org",
                "given_name": "Anna",
                "family_name": "Müller",
                "privacy_consent": "1",
            },
        )
        user = User.objects.get(person__email="new@example.org")
        self.assertEqual(user.privacy_consent_version, "2026-10-12")
        self.assertIsNotNone(user.privacy_consent_at)

    def test_force_without_consent_creates_nothing(self):
        self.client.post(
            "/auth/register/force/",
            {"email": "new@example.org", "given_name": "Anna", "family_name": "Müller"},
        )
        self.assertFalse(Person.objects.filter(email="new@example.org").exists())


@override_settings(PRIVACY_CONSENT_REQUIRED=True, PRIVACY_POLICY_VERSION="2026-10-12")
class PrivacyConsentMiddlewareTests(TestCase):
    def setUp(self):
        self.user = _make_user("anna@example.org")
        self.client.force_login(self.user)

    def test_page_redirects_to_consent_with_next(self):
        resp = self.client.get("/lists/")
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp["Location"], "/auth/einwilligung/?next=%2Flists%2F")

    def test_exempt_paths_and_htmx_pass(self):
        self.assertEqual(self.client.get("/datenschutz/").status_code, 200)
        resp = self.client.get("/lists/", HTTP_HX_REQUEST="true")
        self.assertNotEqual(resp.get("Location", ""), "/auth/einwilligung/?next=%2Flists%2F")

    def test_consent_post_records_and_continues(self):
        resp = self.client.post(
            "/auth/einwilligung/", {"privacy_consent": "1", "next": "/lists/"}
        )
        self.assertRedirects(resp, "/lists/", fetch_redirect_response=False)
        self.user.refresh_from_db()
        self.assertEqual(self.user.privacy_consent_version, "2026-10-12")
        self.assertNotEqual(self.client.get("/lists/").status_code, 302)

    def test_consent_post_without_box_is_rejected(self):
        resp = self.client.post("/auth/einwilligung/", {"next": "/lists/"})
        self.assertEqual(resp.status_code, 400)
        self.user.refresh_from_db()
        self.assertEqual(self.user.privacy_consent_version, "")

    def test_offsite_next_is_ignored(self):
        resp = self.client.post(
            "/auth/einwilligung/", {"privacy_consent": "1", "next": "https://evil.example/"}
        )
        self.assertRedirects(resp, reverse("lists:home"), fetch_redirect_response=False)

    def test_version_bump_asks_again(self):
        self.user.record_privacy_consent()
        with self.settings(PRIVACY_POLICY_VERSION="2027-01-01"):
            self.assertEqual(self.client.get("/lists/").status_code, 302)


@override_settings(PRIVACY_POLICY_VERSION="2026-10-12")
class DatenschutzPageTests(TestCase):
    def test_public_and_shows_stand(self):
        resp = self.client.get("/datenschutz/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Stand: 12.10.2026")
