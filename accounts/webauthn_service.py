"""Thin wrapper around py_webauthn for Fichtelink's registration and login
ceremonies.

Keeps the RP config in one place, hides the import surface from the views,
and adds the two pieces of behavior that aren't in py_webauthn itself:

  - a constant-time floor for `login-begin` (CLAUDE.md / "Enumeration resistance")
  - deterministic fake `allowCredentials` for unknown emails, so an
    attacker cannot distinguish "email known" from "email unknown" by
    response shape or content.

py_webauthn v2.x — pinned in pyproject.toml.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass
from datetime import timedelta
from typing import Iterable

from django.conf import settings
from django.utils import timezone
from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers.cose import COSEAlgorithmIdentifier
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    AuthenticatorTransport,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from .models import Passkey, User, WebAuthnChallenge

CHALLENGE_TTL = timedelta(minutes=5)
LOGIN_FLOOR_SECONDS = 0.25
FAKE_CRED_COUNT = 2  # how many decoy credentials to return for unknown emails


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _user_handle(user: User) -> bytes:
    """Stable WebAuthn user.id for a User. Must NOT contain PII (spec). Use
    the primary key encoded as 8-byte big-endian — opaque but reversible
    server-side via the credential lookup, never exposed to the client UI."""
    return user.pk.to_bytes(8, "big", signed=False)


def _user_handle_to_pk(handle: bytes) -> int:
    return int.from_bytes(handle, "big", signed=False)


def _store_challenge(
    challenge: bytes,
    purpose: str,
    *,
    expected_user: User | None = None,
) -> None:
    WebAuthnChallenge.objects.create(
        challenge=challenge,
        purpose=purpose,
        expected_user=expected_user,
        expires_at=timezone.now() + CHALLENGE_TTL,
    )


def _consume_challenge(challenge: bytes, purpose: str) -> WebAuthnChallenge | None:
    """Pop a challenge by raw bytes + purpose, returning the row or None if
    missing/expired. Single-use: deletes the row before verification so a
    successful or failed ceremony cannot be replayed."""
    try:
        row = WebAuthnChallenge.objects.get(challenge=challenge, purpose=purpose)
    except WebAuthnChallenge.DoesNotExist:
        return None
    expired = row.is_expired()
    row.delete()
    return None if expired else row


def _transports_for(passkey: Passkey) -> list[AuthenticatorTransport] | None:
    if not passkey.transports:
        return None
    out: list[AuthenticatorTransport] = []
    for t in passkey.transports:
        try:
            out.append(AuthenticatorTransport(t))
        except ValueError:
            continue
    return out or None


def _fake_credentials(email: str) -> list[PublicKeyCredentialDescriptor]:
    """Deterministic decoys for an unknown email — same email returns the
    same fake credential IDs every time, so an attacker repeating the
    request cannot distinguish "real but no passkeys" from "no such user".
    """
    key = settings.SECRET_KEY.encode()
    seed = email.strip().lower().encode()
    descriptors: list[PublicKeyCredentialDescriptor] = []
    for i in range(FAKE_CRED_COUNT):
        mac = hmac.new(key, seed + b"|fake|" + str(i).encode(), hashlib.sha256).digest()
        descriptors.append(PublicKeyCredentialDescriptor(id=mac))
    return descriptors


# ---------------------------------------------------------------------------
# Registration ceremony
# ---------------------------------------------------------------------------


@dataclass
class RegistrationOptions:
    options_json: str
    challenge: bytes


def begin_registration(
    *,
    user: User,
    display_name: str,
    exclude_credential_ids: Iterable[bytes] = (),
) -> RegistrationOptions:
    """Generate options for either initial passkey enrollment or adding an
    additional passkey to an existing user. `exclude_credential_ids` lists
    credentials the authenticator should refuse to re-register over."""

    opts = generate_registration_options(
        rp_id=settings.RP_ID,
        rp_name=settings.RP_NAME,
        user_id=_user_handle(user),
        user_name=user.username,
        user_display_name=display_name,
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.REQUIRED,
            user_verification=UserVerificationRequirement.PREFERRED,
        ),
        exclude_credentials=[
            PublicKeyCredentialDescriptor(id=cid) for cid in exclude_credential_ids
        ],
        supported_pub_key_algs=[
            COSEAlgorithmIdentifier.ECDSA_SHA_256,
            COSEAlgorithmIdentifier.RSASSA_PKCS1_v1_5_SHA_256,
        ],
    )
    _store_challenge(opts.challenge, WebAuthnChallenge.Purpose.REGISTER, expected_user=user)
    return RegistrationOptions(options_json=options_to_json(opts), challenge=opts.challenge)


@dataclass
class RegistrationResult:
    credential_id: bytes
    public_key: bytes
    sign_count: int
    transports: list[str]


def finish_registration(*, credential: dict, expected_user: User) -> RegistrationResult:
    """Verify the registration response. Raises on any failure. Caller is
    responsible for inserting the Passkey row.
    """
    raw_challenge_b64 = credential.get("response", {}).get("clientDataJSON")
    if not raw_challenge_b64:
        raise ValueError("clientDataJSON missing")

    # Extract challenge from clientDataJSON and consume the row up front, so
    # a verification failure still invalidates the challenge.
    challenge = _extract_challenge_from_client_data(raw_challenge_b64)
    row = _consume_challenge(challenge, WebAuthnChallenge.Purpose.REGISTER)
    if row is None or row.expected_user_id != expected_user.pk:
        raise ValueError("challenge expired or not issued for this user")

    verification = verify_registration_response(
        credential=credential,
        expected_challenge=challenge,
        expected_rp_id=settings.RP_ID,
        expected_origin=settings.RP_ORIGIN,
        require_user_verification=False,
    )
    transports = credential.get("response", {}).get("transports") or []
    return RegistrationResult(
        credential_id=verification.credential_id,
        public_key=verification.credential_public_key,
        sign_count=verification.sign_count,
        transports=[t for t in transports if isinstance(t, str)],
    )


# ---------------------------------------------------------------------------
# Authentication ceremony
# ---------------------------------------------------------------------------


@dataclass
class AuthenticationOptions:
    options_json: str
    challenge: bytes


def begin_authentication(
    *,
    email: str | None = None,
    started_at: float | None = None,
) -> AuthenticationOptions:
    """Discoverable-credential login.

    If `email` is None or empty, no `allowCredentials` is sent; the browser
    shows whichever resident keys the authenticator advertises for this RP.

    If `email` is set, resolves it to the *set* of Users (shared family
    mailbox case) and lists every Passkey across that set. Unknown emails
    receive deterministic fake credentials so the response is shape- and
    content-indistinguishable from a real result.

    `started_at` is the caller's `time.monotonic()` at the start of the
    request; this function enforces a 250 ms floor before returning so an
    attacker cannot time-attack to learn whether the email is known.
    """
    started_at = started_at if started_at is not None else time.monotonic()
    allow: list[PublicKeyCredentialDescriptor] = []

    if email:
        passkeys = list(
            Passkey.objects.filter(
                user__person__email__iexact=email.strip(),
                user__is_active=True,
            )
        )
        if passkeys:
            for pk in passkeys:
                allow.append(
                    PublicKeyCredentialDescriptor(
                        id=bytes(pk.credential_id),
                        transports=_transports_for(pk),
                    )
                )
        else:
            allow = _fake_credentials(email)

    opts = generate_authentication_options(
        rp_id=settings.RP_ID,
        allow_credentials=allow,
        user_verification=UserVerificationRequirement.PREFERRED,
    )
    _store_challenge(opts.challenge, WebAuthnChallenge.Purpose.LOGIN, expected_user=None)

    _sleep_until_floor(started_at, LOGIN_FLOOR_SECONDS)
    return AuthenticationOptions(options_json=options_to_json(opts), challenge=opts.challenge)


@dataclass
class AuthenticationResult:
    user: User
    new_sign_count: int


def finish_authentication(*, credential: dict) -> AuthenticationResult:
    """Verify the authentication response, return the User to log in.

    Lookup is by credential.id (the WebAuthn credential ID the browser
    just chose); this naturally handles the shared-family-email case
    because each Passkey row points to exactly one User.
    """
    raw_b64 = credential.get("response", {}).get("clientDataJSON")
    if not raw_b64:
        raise ValueError("clientDataJSON missing")
    challenge = _extract_challenge_from_client_data(raw_b64)
    row = _consume_challenge(challenge, WebAuthnChallenge.Purpose.LOGIN)
    if row is None:
        raise ValueError("challenge expired")

    raw_credential_id = _b64url_decode(credential.get("id", ""))
    try:
        passkey = Passkey.objects.select_related("user", "user__person").get(
            credential_id=raw_credential_id
        )
    except Passkey.DoesNotExist as exc:
        raise ValueError("unknown credential") from exc

    verification = verify_authentication_response(
        credential=credential,
        expected_challenge=challenge,
        expected_rp_id=settings.RP_ID,
        expected_origin=settings.RP_ORIGIN,
        credential_public_key=bytes(passkey.public_key),
        credential_current_sign_count=passkey.sign_count,
        require_user_verification=False,
    )
    passkey.sign_count = verification.new_sign_count
    passkey.last_used_at = timezone.now()
    passkey.save(update_fields=["sign_count", "last_used_at"])
    return AuthenticationResult(user=passkey.user, new_sign_count=verification.new_sign_count)


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _sleep_until_floor(started_at: float, floor: float) -> None:
    elapsed = time.monotonic() - started_at
    remaining = floor - elapsed
    if remaining > 0:
        time.sleep(remaining)


def _b64url_decode(s: str) -> bytes:
    import base64

    padding = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + padding)


def _extract_challenge_from_client_data(client_data_json_b64: str) -> bytes:
    """The challenge the client signed is the one in clientDataJSON.challenge,
    base64url-encoded. We need its raw bytes to look up the row.
    """
    import json

    raw = _b64url_decode(client_data_json_b64)
    obj = json.loads(raw.decode("utf-8"))
    return _b64url_decode(obj["challenge"])


def random_username() -> str:
    """Opaque internal handle. Login is by passkey; the username only exists
    because AbstractBaseUser needs a USERNAME_FIELD with a unique value."""
    return "u-" + secrets.token_urlsafe(8)
