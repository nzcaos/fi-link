"""HTTP entry points for Phase 2 auth: registration via email-link,
discoverable-credential login, passkey management. Templates live under
`templates/auth/`.

All POST bodies that carry WebAuthn payloads are JSON; the small HTML forms
(email-entry, passkey-label) are standard Django form POSTs.
"""
from __future__ import annotations

import json
import logging
import smtplib
import time
from datetime import timedelta

from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login as auth_login
from django.contrib.auth import logout as auth_logout
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.core.mail import send_mail
from django.db import transaction
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import ensure_csrf_cookie
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
# Registration rate-limit (N15)
# ---------------------------------------------------------------------------

# Per-IP throttle on the stub-account creation path. Both register_start
# (POST) and register_force funnel into _create_stub_and_send, which writes
# Person + User + ActivationToken and ships an email — i.e. one HTTP hit
# produces one new account row plus one outbound mail. Without a throttle
# a script can pump arbitrary Persons into the DB and mailings to arbitrary
# addresses from our system. We don't need a perfect bucket; we need a
# ceiling that stops trivial automation while not getting in the way of the
# legitimate family-shared-mailbox case (≤ a handful of registrations from
# the same household within an hour).
#
# Storage is Django's default cache (LocMemCache in dev / prod-without-Redis).
# That makes the counter per-gunicorn-worker rather than global; with 3
# workers the effective ceiling is 3× the configured limit. Acceptable for
# this threat model — a serious attacker rotates IPs anyway, this defends
# against accidents and casual abuse. Upgrade path: switch CACHES to a
# shared backend when Redis lands (Phase 4 area).
_REG_RATE_LIMIT = 10  # accounts created per IP per window
_REG_RATE_WINDOW = 60 * 60  # one hour


def _client_ip(request: HttpRequest) -> str:
    """Best-effort client IP. Honours X-Forwarded-For (left-most hop) when
    the Django setting permits, falls back to REMOTE_ADDR. We don't try to
    be clever about IPv6 scoping — the value is only a throttle key.
    """
    xff = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if xff:
        return xff.split(",", 1)[0].strip()
    return request.META.get("REMOTE_ADDR", "") or "unknown"


def _check_registration_rate_limit(request: HttpRequest) -> bool:
    """Returns True if the request is within budget. Increments the bucket
    as a side-effect. Use BEFORE the expensive create-and-mail path so a
    rejected request costs nothing.
    """
    key = f"reg_throttle:{_client_ip(request)}"
    try:
        count = cache.incr(key)
    except ValueError:
        # First hit in this window: incr() raises on missing key, so seed it.
        cache.set(key, 1, timeout=_REG_RATE_WINDOW)
        count = 1
    return count <= _REG_RATE_LIMIT


def _rate_limited_response(request: HttpRequest) -> HttpResponse:
    return render(
        request,
        "auth/register.html",
        {
            "error": (
                "Zu viele Registrierungsversuche aus Ihrem Netzwerk. "
                "Bitte versuchen Sie es in etwa einer Stunde erneut."
            )
        },
        status=429,
    )


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
        return render(
            request,
            "auth/register.html",
            {"email": (request.GET.get("email") or "").strip().lower() or None},
        )

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
    if not _check_registration_rate_limit(request):
        return _rate_limited_response(request)

    existing = Person.objects.filter(email__iexact=email, user__isnull=False).exists()
    if existing:
        return render(
            request,
            "auth/register.html",
            {
                "info": (
                    "Mit dieser Adresse ist bereits mindestens ein Konto verknüpft. "
                    "Falls Sie ein weiteres anlegen wollen (z.B. zweiter Elternteil), "
                    "fahren Sie unten fort. Wenn Sie nur wieder Zugang zu Ihrem "
                    "bestehenden Konto brauchen (z.B. Passkey verloren), nutzen Sie "
                    "die Zugang-wiederherstellen-Funktion."
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
    if not _check_registration_rate_limit(request):
        return _rate_limited_response(request)
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
    mail_failed = False
    try:
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
    except (smtplib.SMTPException, OSError) as exc:
        # N14: a mail-server outage must not become a 500. The stub User and
        # activation token are already committed; the user retries (or an admin
        # re-issues via reset_passkeys) once mail is back.
        mail_failed = True
        log.error("activation mail to %s failed: %s", email, exc)
    return render(
        request,
        "auth/register_check_email.html",
        {"email": email, "mail_failed": mail_failed},
    )


# ---------------------------------------------------------------------------
# Self-service passkey recovery: mail a fresh enrollment link to the address
# already on file. No admin or super-admin involved. See CLAUDE.md /
# "Account recovery".
# ---------------------------------------------------------------------------


def recover_start(request: HttpRequest) -> HttpResponse:
    """Email-entry form that mails a fresh passkey-enrollment link to the
    address on file. For users who lost their passkey(s) — e.g. deleted them
    on the phone or in the OS keychain (the in-app delete already refuses to
    drop the last one, so a lockout only happens outside the app).

    Enumeration-resistant: the response is identical whether or not an account
    exists, so the endpoint can't probe which addresses are registered. The
    link is *additive* — existing passkeys are kept; the user prunes the stale
    one after signing in. Targeting is by activated account, not by passkey
    count, because a stale Passkey row may linger after an out-of-app deletion.
    """
    if request.user.is_authenticated:
        return redirect(settings.LOGIN_REDIRECT_URL)
    if request.method == "GET":
        return render(request, "auth/recover.html", {})

    email = (request.POST.get("email") or "").strip().lower()
    if not email:
        return render(
            request,
            "auth/recover.html",
            {"error": "Bitte geben Sie Ihre E-Mail-Adresse an."},
            status=400,
        )
    if not _check_registration_rate_limit(request):
        return _rate_limited_response(request)

    # One link per activated account on this address (shared family mailbox →
    # a link per parent, each labelled by name; the recipient picks their own).
    users = list(
        User.objects.select_related("person").filter(
            person__email__iexact=email, is_active=True
        )
    )
    if users:
        links: list[tuple[str, str]] = []
        with transaction.atomic():
            for user in users:
                token = ActivationToken.issue(
                    person=user.person,
                    email=user.person.email or email,
                    user=user,
                    purpose=ActivationToken.Purpose.RECOVER,
                )
                links.append(
                    (
                        user.person.full_name,
                        request.build_absolute_uri(
                            reverse("accounts:activate", args=[token.token])
                        ),
                    )
                )
        _send_recovery_mail(email, links)

    # Identical response whether or not an account was found (enumeration
    # resistance) and whether or not the mail actually went out.
    return render(request, "auth/recover.html", {"sent": True, "email": email})


def _send_recovery_mail(email: str, links: list[tuple[str, str]]) -> None:
    if len(links) == 1:
        name, link = links[0]
        body = (
            f"Hallo {name},\n\n"
            f"Sie haben angefordert, einen neuen Passkey für Ihr Fichtelink-Konto "
            f"einzurichten. Öffnen Sie dazu den folgenden Link:\n\n{link}\n\n"
            f"Der Link ist sieben Tage gültig. Bestehende Passkeys bleiben erhalten. "
            f"Wenn Sie das nicht waren, ignorieren Sie diese Mail — es wird nichts "
            f"verändert."
        )
    else:
        lines = "\n".join(f"  - {name}: {link}" for name, link in links)
        body = (
            f"Hallo,\n\n"
            f"unter dieser E-Mail-Adresse sind mehrere Fichtelink-Konten registriert. "
            f"Wählen Sie den Link für Ihr Konto, um einen neuen Passkey einzurichten:\n\n"
            f"{lines}\n\n"
            f"Die Links sind sieben Tage gültig. Bestehende Passkeys bleiben erhalten. "
            f"Wenn Sie das nicht waren, ignorieren Sie diese Mail — es wird nichts "
            f"verändert."
        )
    try:
        send_mail(
            subject="Fichtelink — neuen Passkey einrichten",
            message=body,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[email],
        )
    except (smtplib.SMTPException, OSError) as exc:
        # Never surface the failure to the caller (enumeration resistance);
        # log it so the operator can spot mail-server trouble.
        log.error("recovery mail to %s failed: %s", email, exc)


@ensure_csrf_cookie
def activate(request: HttpRequest, token: str) -> HttpResponse:
    """Activation-link landing. Renders the passkey-enrollment page; the
    actual passkey ceremony goes through `register_passkey_begin/finish`.

    `@ensure_csrf_cookie` forces a CSRF cookie on the GET response so the
    embedded JS can read it on first visit (e.g. opening the activation
    link in a browser that has never touched the site before — iOS Safari).
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
    return JsonResponse({"redirect": _next_url_after_auth(request, default=reverse("accounts:passkeys"))})


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------


@ensure_csrf_cookie
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
    return JsonResponse(
        {"redirect": _next_url_after_auth(request, default=settings.LOGIN_REDIRECT_URL)}
    )


@require_POST
def logout_view(request: HttpRequest) -> HttpResponse:
    auth_logout(request)
    return redirect(settings.LOGOUT_REDIRECT_URL)


# ---------------------------------------------------------------------------
# Profile (own PERSON: name editable, email read-only)
# ---------------------------------------------------------------------------


class ProfileForm(forms.ModelForm):
    class Meta:
        model = Person
        fields = ["given_name", "family_name"]


@login_required
def profile(request: HttpRequest) -> HttpResponse:
    """Edit the logged-in user's own PERSON name. Email is the addressable
    identifier (activation links, mail routing) and is shown read-only — changes
    to it go through an admin until a verify-on-change flow exists.
    """
    person = request.user.person
    if request.method == "POST":
        form = ProfileForm(request.POST, instance=person)
        if form.is_valid():
            form.save()
            messages.success(request, "Profil gespeichert.")
            return redirect("accounts:profile")
    else:
        form = ProfileForm(instance=person)
    return render(request, "auth/profile.html", {"form": form, "email": person.email})


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
        return redirect("accounts:passkeys")
    passkey.delete()
    messages.success(request, "Passkey gelöscht.")
    return redirect("accounts:passkeys")


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


def _next_url_after_auth(request: HttpRequest, *, default: str) -> str:
    """If the session carries a pending list-join handoff (set by the lists
    app when an invite or QR-join click landed on register/login), continue
    to the matching handler so the join is finalised in one user-visible flow.
    """
    pending_invite = request.session.pop("pending_invite_token", None)
    if pending_invite:
        return reverse("lists:invite_accept", kwargs={"token": pending_invite})
    pending_join = request.session.pop("pending_join_token", None)
    if pending_join:
        return reverse("lists:join_via_token", kwargs={"token": pending_join})
    pending_admin = request.session.pop("pending_admin_invite_token", None)
    if pending_admin:
        return reverse("lists:admin_invite_accept", kwargs={"token": pending_admin})
    pending_form = request.session.pop("pending_form_token", None)
    if pending_form:
        return reverse("forms:shared", kwargs={"token": pending_form})
    return default


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
