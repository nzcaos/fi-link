"""Views for the lists app.

Phase 3a: list-index, list-create, list-detail stub.
Phase 3b: list-detail with record rows, record-add/edit with visibility matrix.
"""
from __future__ import annotations

from urllib.parse import urlencode

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.mail import send_mail
from django.db import transaction
from django.db.models import Q
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from .forms import ListCreateForm, ListInviteForm, RecordEditForm
from .models import (
    List,
    ListAdmin,
    ListInviteToken,
    ListJoinToken,
    ListRecord,
    ListTemplate,
    RecordManager,
)


def _sanitize_header_value(value: str, max_length: int = 200) -> str:
    """Collapse whitespace (incl. embedded CR/LF) and cap length, so values
    bound for email headers cannot cause BadHeaderError / 500 — see M9.
    Defense-in-depth: list titles are also cleaned in ListCreateForm, but the
    admin UI and direct DB writes bypass that.
    """
    return " ".join((value or "").split())[:max_length]
from .permissions import (
    can_user_admin_list,
    can_user_edit_record,
    can_user_see_list,
)
from .visibility import can_user_see_subject_name, visible_attributes_for


@login_required
def list_index(request):
    user = request.user
    if user.is_superuser:
        own_lists = List.objects.filter(archived_at__isnull=True).order_by("title")
    else:
        own_lists = (
            List.objects.filter(archived_at__isnull=True)
            .filter(Q(admins__user=user) | Q(members__user=user))
            .distinct()
            .order_by("title")
        )
    public_lists = (
        List.objects.filter(archived_at__isnull=True)
        .filter(
            visibility__in=[
                List.Visibility.PUBLIC_VISIBLE,
                List.Visibility.PUBLIC_EDITABLE,
            ]
        )
        .exclude(pk__in=own_lists.values("pk"))
        .order_by("title")
    )
    return render(
        request,
        "lists/index.html",
        {"own_lists": own_lists, "public_lists": public_lists},
    )


@login_required
def list_create(request):
    if request.method == "POST":
        form = ListCreateForm(request.POST, user=request.user)
        if form.is_valid():
            with transaction.atomic():
                lst: List = form.save()
                ListAdmin.objects.create(list=lst, user=request.user)
            return redirect("lists:detail", pk=lst.pk)
    else:
        form = ListCreateForm(user=request.user)
    return render(request, "lists/new.html", {"form": form})


@login_required
def list_detail(request, pk: int):
    lst = get_object_or_404(List, pk=pk)
    if not can_user_see_list(request.user, lst):
        return HttpResponseForbidden("Sie haben keinen Zugriff auf diese Liste.")
    is_admin = can_user_admin_list(request.user, lst)

    records_qs = lst.records.filter(archived_at__isnull=True).select_related("subject")
    rows = []
    anon_counter = 0
    for record in records_qs:
        attrs = list(visible_attributes_for(request.user, record))
        values_map = {v.attribute_id: v.value for v in record.values.all()}
        fields = [(a, values_map.get(a.pk, "")) for a in attrs]
        # M8: subject-name anonymisation. Counter is per-render — ?N is a
        # display hack, not a stable identifier (see CLAUDE.md).
        if can_user_see_subject_name(request.user, record):
            subject_display = str(record.subject)
        else:
            anon_counter += 1
            subject_display = f"?{anon_counter}"
        rows.append(
            {
                "record": record,
                "subject_display": subject_display,
                "fields": fields,
                "can_edit": can_user_edit_record(request.user, record),
            }
        )

    own_record = None
    if request.user.is_authenticated and request.user.person_id:
        own_record = ListRecord.objects.filter(
            list=lst, subject_id=request.user.person_id, archived_at__isnull=True
        ).first()

    return render(
        request,
        "lists/detail.html",
        {
            "list_obj": lst,
            "is_admin": is_admin,
            "rows": rows,
            "own_record": own_record,
        },
    )


@login_required
def record_create_self(request, pk: int):
    """Create an empty LIST_RECORD (role=member, subject=request.user.person)
    for the current user and redirect into the edit form. Only meaningful for
    `self`-mode LISTTEMPLATEs.

    M11: Refuse on `via_associate` templates — in that mode the subject of a
    record is *another* PERSON (e.g. a child) declared by the registering USER,
    not the USER themselves. Creating `subject=user.person` blindly would
    mis-model the list. The full associate-wizard lives in Phase 3b-2; until
    then this endpoint must not silently produce wrong records.
    """
    lst = get_object_or_404(List, pk=pk)
    if not can_user_see_list(request.user, lst):
        return HttpResponseForbidden("Sie haben keinen Zugriff auf diese Liste.")
    if lst.archived_at is not None:
        return HttpResponseForbidden("Diese Liste ist archiviert.")
    if (
        lst.template.member_subject_mode
        == ListTemplate.MemberSubjectMode.VIA_ASSOCIATE
    ):
        return HttpResponseForbidden(
            "Diese Liste verlangt, dass Sie eine andere Person eintragen "
            "(z. B. Ihr Kind). Der dafür nötige Wizard wird in einer "
            "nächsten Phase ergänzt — bitte wenden Sie sich bis dahin an "
            "die Listen-Admins."
        )
    existing = ListRecord.objects.filter(
        list=lst, subject_id=request.user.person_id, archived_at__isnull=True
    ).first()
    if existing is not None:
        return redirect("lists:record_edit", pk=lst.pk, record_pk=existing.pk)

    with transaction.atomic():
        record = ListRecord.objects.create(
            list=lst,
            subject=request.user.person,
            role=ListRecord.Role.MEMBER,
        )
        RecordManager.objects.create(
            record=record,
            user=request.user,
            basis=RecordManager.Basis.SELF_REGISTERED,
        )
    return redirect("lists:record_edit", pk=lst.pk, record_pk=record.pk)


def _consume_invite_and_redirect(request, invite: ListInviteToken):
    """Inside an authenticated session, finalize the invitation:
    - create (or fetch) the LIST_RECORD with subject=target_person,
    - write RecordManager(basis=invited) for the visitor,
    - mark the token consumed,
    - return a redirect into the record-edit form.

    Caller has already verified that the visitor's USER is bound to
    `invite.target_person`.
    """
    with transaction.atomic():
        invite = ListInviteToken.objects.select_for_update().get(pk=invite.pk)
        if invite.is_consumed or invite.is_expired:
            return redirect("lists:detail", pk=invite.list_id)

        role = (
            ListRecord.Role.MEMBER
            if invite.mode == ListInviteToken.Mode.SELF
            else ListRecord.Role.ASSOCIATE
        )
        record = (
            ListRecord.objects.filter(
                list=invite.list,
                subject=invite.target_person,
                archived_at__isnull=True,
            )
            .first()
        )
        if record is None:
            record = ListRecord.objects.create(
                list=invite.list,
                subject=invite.target_person,
                role=role,
            )
        if not RecordManager.objects.filter(record=record, user=request.user).exists():
            RecordManager.objects.create(
                record=record,
                user=request.user,
                basis=RecordManager.Basis.INVITED,
            )
        invite.consumed_at = timezone.now()
        invite.save(update_fields=["consumed_at"])
    return redirect("lists:record_edit", pk=invite.list_id, record_pk=record.pk)


def invite_accept(request, token: str):
    """Click-time handler for a ListInviteToken.

    Two branches per CLAUDE.md / *Member invitation and LIST_INVITE_TOKEN*:
    - target_person IS NULL → not-yet-USER. If the visitor is not logged in,
      hand off to the register flow with email prefill (the accounts app will
      come back here after passkey enrollment). If they ARE logged in (round-
      trip after enrollment, OR an existing USER clicking a freshly-issued
      blank-target invite for themselves), bind target_person to their PERSON
      and complete the join.
    - target_person IS NOT NULL → existing-USER. Require auth as the bound
      USER, otherwise login first; refuse on wrong-USER session; complete
      the join on match.

    M10: GET is side-effect-free — auto-preview fetchers (Outlook Safe Links,
    Slack/Teams unfurlers, mail-client previews) must not consume tokens,
    bind target_person, or pollute the session. GET shows a confirmation page;
    the actual join is committed only on POST (CSRF-protected).
    """
    invite = get_object_or_404(ListInviteToken, token=token)
    if invite.is_consumed:
        return render(
            request,
            "lists/invite_problem.html",
            {"reason": "Diese Einladung wurde bereits eingelöst."},
            status=410,
        )
    if invite.is_expired:
        return render(
            request,
            "lists/invite_problem.html",
            {"reason": "Diese Einladung ist abgelaufen."},
            status=410,
        )

    # Wrong-user session is a terminal state on either method: even GET refuses
    # without leaking the target name (see B4). No side-effect, so safe on GET.
    if (
        invite.target_person_id is not None
        and request.user.is_authenticated
        and request.user.person_id != invite.target_person_id
    ):
        return render(
            request,
            "lists/invite_problem.html",
            {
                "reason": (
                    "Diese Einladung gehört nicht zu Ihrem Konto. "
                    "Bitte melden Sie sich mit dem richtigen Konto an "
                    "und öffnen Sie den Einladungs-Link erneut."
                )
            },
            status=403,
        )

    if request.method == "POST":
        if invite.target_person_id is None:
            if request.user.is_authenticated:
                invite.target_person_id = request.user.person_id
                invite.save(update_fields=["target_person"])
                return _consume_invite_and_redirect(request, invite)
            request.session["pending_invite_token"] = invite.token
            return redirect(
                reverse("accounts:register_start")
                + "?"
                + urlencode({"email": invite.target_email})
            )

        if not request.user.is_authenticated:
            request.session["pending_invite_token"] = invite.token
            return redirect(
                reverse("accounts:login") + "?" + urlencode({"next": request.path})
            )
        return _consume_invite_and_redirect(request, invite)

    # GET: render a confirmation page. No DB writes, no session writes.
    if invite.target_person_id is None and not request.user.is_authenticated:
        next_action = "register"
    elif invite.target_person_id is not None and not request.user.is_authenticated:
        next_action = "login"
    else:
        next_action = "accept"
    return render(
        request,
        "lists/invite_confirm.html",
        {
            "list_obj": invite.list,
            "invite": invite,
            "next_action": next_action,
        },
    )


@login_required
def list_invite(request, pk: int):
    lst = get_object_or_404(List, pk=pk)
    if not can_user_admin_list(request.user, lst):
        return HttpResponseForbidden("Nur Listen-Admins dürfen einladen.")

    if request.method == "POST":
        form = ListInviteForm(request.POST, list_obj=lst, inviting_user=request.user)
        if form.is_valid():
            target_person = form.cleaned_data.get("target_person")
            target_email = form.cleaned_data["target_email"]
            mode = form.cleaned_data.get("mode") or lst.template.member_subject_mode

            with transaction.atomic():
                token = ListInviteToken.objects.create(
                    list=lst,
                    invited_by=request.user,
                    target_email=target_email,
                    target_person=target_person,
                    mode=mode,
                )
            link = request.build_absolute_uri(
                reverse("lists:invite_accept", kwargs={"token": token.token})
            )
            safe_title = _sanitize_header_value(lst.title)
            send_mail(
                subject=f'Einladung zur Liste „{safe_title}"',
                message=(
                    f"Hallo,\n\n"
                    f'{request.user.person} hat Sie zur Liste „{safe_title}" eingeladen.\n\n'
                    f"Klicken Sie hier, um beizutreten:\n{link}\n\n"
                    f"Der Link ist gültig bis {token.expires_at:%d.%m.%Y}.\n"
                ),
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[target_email],
            )
            messages.success(request, f"Einladung an {target_email} versendet.")
            return redirect("lists:detail", pk=lst.pk)
    else:
        form = ListInviteForm(list_obj=lst, inviting_user=request.user)
    return render(request, "lists/invite_create.html", {"list_obj": lst, "form": form})


@login_required
def record_edit(request, pk: int, record_pk: int):
    lst = get_object_or_404(List, pk=pk)
    record = get_object_or_404(ListRecord, pk=record_pk, list=lst)
    if not can_user_edit_record(request.user, record):
        return HttpResponseForbidden("Sie dürfen diesen Eintrag nicht bearbeiten.")
    if request.method == "POST":
        form = RecordEditForm(request.POST, record=record, user=request.user)
        if form.is_valid():
            form.save()
            return redirect("lists:detail", pk=lst.pk)
    else:
        form = RecordEditForm(record=record, user=request.user)
    return render(
        request,
        "lists/record_edit.html",
        {"list_obj": lst, "record": record, "form": form},
    )


# ---------------------------------------------------------------------------
# QR-code join tokens (Phase 3b-2)
# ---------------------------------------------------------------------------


@login_required
def list_join_tokens(request, pk: int):
    """Admin-only page: list active QR join tokens for a list, create new ones."""
    lst = get_object_or_404(List, pk=pk)
    if not can_user_admin_list(request.user, lst):
        return HttpResponseForbidden("Nur Listen-Admins dürfen QR-Tokens verwalten.")

    if request.method == "POST":
        ListJoinToken.objects.create(list=lst, created_by=request.user)
        messages.success(request, "Neuer QR-Code erstellt.")
        return redirect("lists:join_tokens", pk=lst.pk)

    tokens = list(lst.join_tokens.all())
    # Annotate each token with its absolute URL so the template can render
    # a QR for it without recomputing the full URL per row.
    for tok in tokens:
        tok.join_url = request.build_absolute_uri(
            reverse("lists:join_via_token", kwargs={"token": tok.token})
        )
    return render(
        request,
        "lists/join_tokens.html",
        {"list_obj": lst, "tokens": tokens},
    )


@login_required
def revoke_join_token(request, pk: int, token: str):
    """Admin-only: mark a QR join token as revoked. POST-only."""
    lst = get_object_or_404(List, pk=pk)
    if not can_user_admin_list(request.user, lst):
        return HttpResponseForbidden("Nur Listen-Admins dürfen QR-Tokens widerrufen.")
    if request.method != "POST":
        return HttpResponseForbidden("Nur per POST.")
    join_token = get_object_or_404(ListJoinToken, token=token, list=lst)
    if join_token.revoked_at is None:
        join_token.revoked_at = timezone.now()
        join_token.save(update_fields=["revoked_at"])
        messages.success(request, "QR-Code widerrufen.")
    return redirect("lists:join_tokens", pk=lst.pk)


def _join_self_mode(request, lst: List):
    """Finalize a self-mode QR join for the authenticated visitor: ensure a
    LIST_RECORD with role=member, subject=user.person exists, then redirect to
    the record-edit form. Idempotent — re-scanning yields the same record.
    """
    existing = ListRecord.objects.filter(
        list=lst, subject_id=request.user.person_id, archived_at__isnull=True
    ).first()
    if existing is not None:
        return redirect("lists:record_edit", pk=lst.pk, record_pk=existing.pk)
    with transaction.atomic():
        record = ListRecord.objects.create(
            list=lst,
            subject=request.user.person,
            role=ListRecord.Role.MEMBER,
        )
        RecordManager.objects.create(
            record=record,
            user=request.user,
            basis=RecordManager.Basis.SELF_REGISTERED,
        )
    return redirect("lists:record_edit", pk=lst.pk, record_pk=record.pk)


def join_via_token(request, token: str):
    """Click handler for a QR join token.

    GET = confirmation page (no DB writes, no session writes — same M10
    pattern as invite_accept). POST commits:

    - unauthenticated → stash `pending_join_token` in session, redirect to
      register-start (no email prefill — the token is anonymous).
    - authenticated + `self`-mode → ensure-or-create LIST_RECORD, redirect to
      record-edit.
    - authenticated + `via_associate`-mode → land in the associate-wizard
      (added in Phase 3b-2 / second sub-task; until then the wizard endpoint
      itself owns the user-facing 'coming soon' message).
    """
    join_token = get_object_or_404(ListJoinToken, token=token)
    if join_token.is_revoked:
        return render(
            request,
            "lists/invite_problem.html",
            {"reason": "Dieser QR-Code wurde widerrufen."},
            status=410,
        )
    if join_token.is_expired:
        return render(
            request,
            "lists/invite_problem.html",
            {"reason": "Dieser QR-Code ist abgelaufen."},
            status=410,
        )
    lst = join_token.list
    if lst.archived_at is not None:
        return render(
            request,
            "lists/invite_problem.html",
            {"reason": "Diese Liste ist archiviert."},
            status=410,
        )

    if request.method == "POST":
        if not request.user.is_authenticated:
            request.session["pending_join_token"] = join_token.token
            return redirect(reverse("accounts:register_start"))
        if (
            lst.template.member_subject_mode
            == ListTemplate.MemberSubjectMode.VIA_ASSOCIATE
        ):
            return redirect("lists:record_create_associate", pk=lst.pk)
        return _join_self_mode(request, lst)

    # NOTE: `lists:record_create_associate` is wired in Phase 3b-2 / Wizard
    # sub-task. Until that lands, scanning a QR for a via_associate-mode list
    # while authenticated will hit a NoReverseMatch on POST. Tests in
    # sub-phase A therefore only exercise the self-mode path; the associate
    # path gets its own coverage in sub-phase B.

    # GET: render confirmation page, no side effects.
    if not request.user.is_authenticated:
        next_action = "register"
    else:
        next_action = "join"
    return render(
        request,
        "lists/join_confirm.html",
        {
            "list_obj": lst,
            "join_token": join_token,
            "next_action": next_action,
        },
    )
