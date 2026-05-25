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
    def test_get_renders_form(self):
        resp = self.client.get("/auth/register/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Konto anlegen")

    def test_post_creates_stub_and_sends_link(self):
        from django.core import mail

        resp = self.client.post(
            "/auth/register/",
            {"email": "new@example.org", "given_name": "Anna", "family_name": "Müller"},
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
            {"email": "parent@example.org", "given_name": "Bernd", "family_name": "Müller"},
        )
        self.assertEqual(resp.status_code, 200)
        # The form should offer to continue under the shared address (family-mailbox case).
        self.assertContains(resp, "weiteres Konto")
