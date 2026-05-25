"""HTTP entry points for Phase 2 auth: registration via email-link,
discoverable-credential login, passkey management. Templates live under
`templates/auth/`.

All POST bodies that carry WebAuthn payloads are JSON; the small HTML forms
(email-entry, passkey-label) are standard Django form POSTs.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import timedelta

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login as auth_login
from django.contrib.auth import logout as auth_logout
from django.contrib.auth.decorators import login_required
from django.core.mail import send_mail
from django.db import transaction
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from .models import ActivationToken, Passkey, Person, User
from .webauthn_service import (
    begin_authentication,
    begin_registration,
    finish_authentication,
    finish_registration,
    random_username,
)

log = logging.getLogger(__name__)

DEFAULT_BACKEND = "django.contrib.auth.backends.ModelBackend"


# ---------------------------------------------------------------------------
# Registration: email entry → activation link → passkey enrollment
# ---------------------------------------------------------------------------


def register_start(request: HttpRequest) -> HttpResponse:
    """Email entry form. POST creates a stub Person + inactive User if the
    email is unknown, then mails an activation link. Returning users see a
    "you already have an account" hint — deliberately NOT enumeration-
    resistant (see CLAUDE.md / "Enumeration resistance").
    """
    if request.method == "GET":
        return render(request, "auth/register.html")

    email = (request.POST.get("email") or "").strip().lower()
    given_name = (request.POST.get("given_name") or "").strip()
    family_name = (request.POST.get("family_name") or "").strip()
    if not email or not given_name or not family_name:
        return render(
            request,
            "auth/register.html",
            {"error": "Bitte Vor-/Nachname und E-Mail angeben."},
            status=400,
        )

    existing = Person.objects.filter(email__iexact=email, user__isnull=False).exists()
    if existing:
        return render(
            request,
            "auth/register.html",
            {
                "info": (
                    "Mit dieser Adresse ist bereits mindestens ein Konto verknüpft. "
                    "Falls Sie ein weiteres anlegen wollen (z.B. zweiter Elternteil), "
                    "fahren Sie unten fort. Wenn Sie Ihr Konto wiederherstellen wollen, "
                    "wenden Sie sich an Ihren Listen-Admin."
                ),
                "email": email,
                "given_name": given_name,
                "family_name": family_name,
                "allow_continue": True,
            },
        )
    return _create_stub_and_send(request, email=email, given_name=given_name, family_name=family_name)


@require_POST
def register_force(request: HttpRequest) -> HttpResponse:
    """User confirmed they want a second account under an already-known email
    (shared family mailbox). Skips the existing-account check."""
    email = (request.POST.get("email") or "").strip().lower()
    given_name = (request.POST.get("given_name") or "").strip()
    family_name = (request.POST.get("family_name") or "").strip()
    if not (email and given_name and family_name):
        return redirect("accounts:register_start")
    return _create_stub_and_send(request, email=email, given_name=given_name, family_name=family_name)


def _create_stub_and_send(
    request: HttpRequest, *, email: str, given_name: str, family_name: str
) -> HttpResponse:
    with transaction.atomic():
        person = Person.objects.create(
            given_name=given_name,
            family_name=family_name,
            email=email,
        )
        user = User.objects.create_user(person=person, username=random_username(), is_active=False)
        token = ActivationToken.issue(person=person, email=email, user=user)
    link = request.build_absolute_uri(
        reverse("accounts:activate", args=[token.token])
    )
    send_mail(
        subject="Fichtelink — Konto aktivieren",
        message=(
            f"Hallo {given_name},\n\n"
            f"klicken Sie auf den folgenden Link, um Ihr Fichtelink-Konto zu aktivieren "
            f"und einen Passkey einzurichten:\n\n{link}\n\n"
            f"Der Link ist {token.expires_at:%d.%m.%Y %H:%M} gültig."
        ),
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[email],
    )
    return render(request, "auth/register_check_email.html", {"email": email})


def activate(request: HttpRequest, token: str) -> HttpResponse:
    """Activation-link landing. Renders the passkey-enrollment page; the
    actual passkey ceremony goes through `register_passkey_begin/finish`.
    """
    activation = get_object_or_404(ActivationToken, token=token)
    if not activation.is_valid():
        return render(
            request,
            "auth/activate_invalid.html",
            {"token": activation},
            status=410,
        )
    return render(
        request,
        "auth/enroll.html",
        {
            "token": activation.token,
            "person": activation.person,
            "display_name": activation.person.full_name,
        },
    )


@require_POST
def register_passkey_begin(request: HttpRequest) -> JsonResponse:
    payload = _json_body(request)
    token_value = payload.get("token", "")
    activation = _load_active_token(token_value)
    if activation is None:
        return JsonResponse({"error": "token invalid"}, status=400)

    user = activation.user
    assert user is not None, "ActivationToken without user — see _create_stub_and_send"
    opts = begin_registration(
        user=user,
        display_name=activation.person.full_name,
        exclude_credential_ids=[
            bytes(pk.credential_id) for pk in user.passkeys.all()
        ],
    )
    return _json_options(opts.options_json)


@require_POST
def register_passkey_finish(request: HttpRequest) -> JsonResponse:
    payload = _json_body(request)
    token_value = payload.get("token", "")
    credential = payload.get("credential")
    label = (payload.get("label") or "").strip()[:100]
    if not credential:
        return JsonResponse({"error": "credential missing"}, status=400)

    activation = _load_active_token(token_value)
    if activation is None:
        return JsonResponse({"error": "token invalid"}, status=400)

    user = activation.user
    assert user is not None
    try:
        result = finish_registration(credential=credential, expected_user=user)
    except Exception as exc:  # noqa: BLE001 — py_webauthn raises a wide tree
        log.warning("register_passkey_finish failed: %s", exc)
        return JsonResponse({"error": str(exc)}, status=400)

    with transaction.atomic():
        Passkey.objects.create(
            user=user,
            credential_id=result.credential_id,
            public_key=result.public_key,
            sign_count=result.sign_count,
            transports=result.transports,
            label=label or "Erster Passkey",
        )
        if not user.is_active:
            user.is_active = True
            user.save(update_fields=["is_active"])
        activation.consumed_at = timezone.now()
        activation.save(update_fields=["consumed_at"])

    auth_login(request, user, backend=DEFAULT_BACKEND)
    return JsonResponse({"redirect": reverse("accounts:passkeys")})


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------


def login_start(request: HttpRequest) -> HttpResponse:
    if request.user.is_authenticated:
        return redirect(settings.LOGIN_REDIRECT_URL)
    return render(request, "auth/login.html")


@require_POST
def login_begin(request: HttpRequest) -> JsonResponse:
    started_at = time.monotonic()
    payload = _json_body(request)
    email = (payload.get("email") or "").strip().lower() or None
    opts = begin_authentication(email=email, started_at=started_at)
    return _json_options(opts.options_json)


@require_POST
def login_finish(request: HttpRequest) -> JsonResponse:
    payload = _json_body(request)
    credential = payload.get("credential")
    if not credential:
        return JsonResponse({"error": "credential missing"}, status=400)
    try:
        result = finish_authentication(credential=credential)
    except Exception as exc:  # noqa: BLE001
        log.warning("login_finish failed: %s", exc)
        return JsonResponse({"error": "Anmeldung fehlgeschlagen."}, status=400)
    if not result.user.is_active:
        return JsonResponse({"error": "Konto deaktiviert."}, status=403)
    auth_login(request, result.user, backend=DEFAULT_BACKEND)
    return JsonResponse({"redirect": settings.LOGIN_REDIRECT_URL})


@require_POST
def logout_view(request: HttpRequest) -> HttpResponse:
    auth_logout(request)
    return redirect(settings.LOGOUT_REDIRECT_URL)


# ---------------------------------------------------------------------------
# Passkey management (HTMX-driven)
# ---------------------------------------------------------------------------


@login_required
def passkeys_index(request: HttpRequest) -> HttpResponse:
    return render(
        request,
        "auth/passkeys.html",
        {"passkeys": request.user.passkeys.order_by("-created_at")},
    )


@login_required
@require_GET
def passkeys_list_fragment(request: HttpRequest) -> HttpResponse:
    return render(
        request,
        "auth/_passkey_list.html",
        {"passkeys": request.user.passkeys.order_by("-created_at")},
    )


@login_required
@require_POST
def passkey_add_begin(request: HttpRequest) -> JsonResponse:
    user = request.user
    opts = begin_registration(
        user=user,
        display_name=user.person.full_name,
        exclude_credential_ids=[bytes(pk.credential_id) for pk in user.passkeys.all()],
    )
    return _json_options(opts.options_json)


@login_required
@require_POST
def passkey_add_finish(request: HttpRequest) -> JsonResponse:
    payload = _json_body(request)
    credential = payload.get("credential")
    label = (payload.get("label") or "").strip()[:100]
    if not credential:
        return JsonResponse({"error": "credential missing"}, status=400)
    try:
        result = finish_registration(credential=credential, expected_user=request.user)
    except Exception as exc:  # noqa: BLE001
        log.warning("passkey_add_finish failed: %s", exc)
        return JsonResponse({"error": str(exc)}, status=400)
    Passkey.objects.create(
        user=request.user,
        credential_id=result.credential_id,
        public_key=result.public_key,
        sign_count=result.sign_count,
        transports=result.transports,
        label=label or "Weiterer Passkey",
    )
    return JsonResponse({"ok": True})


@login_required
@require_POST
def passkey_delete(request: HttpRequest, pk: int) -> HttpResponse:
    passkey = get_object_or_404(Passkey, pk=pk, user=request.user)
    remaining = request.user.passkeys.exclude(pk=pk).count()
    if remaining == 0:
        messages.error(
            request,
            "Das ist Ihr letzter Passkey — Sie würden sich aussperren. "
            "Bitte richten Sie zuerst einen weiteren ein.",
        )
        return _passkey_list_response(request)
    passkey.delete()
    return _passkey_list_response(request)


def _passkey_list_response(request: HttpRequest) -> HttpResponse:
    return render(
        request,
        "auth/_passkey_list.html",
        {"passkeys": request.user.passkeys.order_by("-created_at")},
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _json_body(request: HttpRequest) -> dict:
    if not request.body:
        return {}
    try:
        return json.loads(request.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}


def _json_options(options_json: str) -> JsonResponse:
    # py_webauthn already serializes to JSON-with-base64url; re-parse so
    # Django serializes once at the HTTP layer.
    return JsonResponse(json.loads(options_json))


def _load_active_token(token_value: str) -> ActivationToken | None:
    if not token_value:
        return None
    try:
        activation = ActivationToken.objects.select_related("user", "person").get(
            token=token_value
        )
    except ActivationToken.DoesNotExist:
        return None
    if not activation.is_valid():
        return None
    return activation
