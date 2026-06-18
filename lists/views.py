"""Views for the lists app.

Phase 3a: list-index, list-create, list-detail stub.
Phase 3b: list-detail with record rows, record-add/edit with visibility matrix.
"""
from __future__ import annotations

import logging
import smtplib
from urllib.parse import urlencode

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.mail import send_mail
from django.db import transaction
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from .forms import (
    AdminInviteForm,
    AssociateWizardForm,
    CohortEditForm,
    ListCreateForm,
    ListInviteForm,
    RecordEditForm,
    TestSendForm,
    TransferInitiateForm,
)
from .models import (
    AdminInviteToken,
    InboundMessage,
    List,
    ListAccess,
    ListAdmin,
    ListInviteToken,
    ListJoinToken,
    ListAttribute,
    ListRecord,
    ListRecordAccess,
    ListRecordValue,
    ListTemplate,
    MailReleaseToken,
    PendingTransfer,
    PersonRelationship,
    RecordManager,
)


log = logging.getLogger(__name__)


def _send_mail_safe(*, subject: str, message: str, recipient_list: list[str]) -> bool:
    """Send a transactional mail without ever letting an SMTP error become a
    500 (N14). The originating DB row (invite token etc.) is already committed
    by the time we get here, so on failure we log, return False, and let the
    caller surface a status message with the link so the admin can relay it
    out-of-band or resend.
    """
    try:
        send_mail(
            subject=subject,
            message=message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=recipient_list,
        )
        return True
    except (smtplib.SMTPException, OSError) as exc:
        log.error("send_mail to %s failed: %s", recipient_list, exc)
        return False


def _sanitize_header_value(value: str, max_length: int = 200) -> str:
    """Collapse whitespace (incl. embedded CR/LF) and cap length, so values
    bound for email headers cannot cause BadHeaderError / 500 — see M9.
    Defense-in-depth: list titles are also cleaned in ListCreateForm, but the
    admin UI and direct DB writes bypass that.
    """
    return " ".join((value or "").split())[:max_length]
from .permissions import (
    accessible_lists_for,
    can_user_admin_list,
    can_user_decide_transfer,
    can_user_edit_record,
    can_user_initiate_transfer,
    can_user_see_list,
    is_super_admin,
    resolve_default_list,
    would_self_removal_leave_no_admin,
)
from . import lifecycle
from .tasks import enqueue_aggregate_fanout, enqueue_list_fanout, list_recipient_emails
from .visibility import build_composed_rows, build_visible_rows, member_grid_header, _role_label


@login_required
def list_home(request):
    """Post-login landing. Bounce the user straight onto a list view rather
    than a separate landing page (CLAUDE.md / *Post-login landing*): their
    last-selected list, else their first accessible one. Users with no lists
    yet fall through to the index, which explains the empty state.
    """
    lst = resolve_default_list(request.user)
    if lst is not None:
        return redirect("lists:detail", pk=lst.pk)
    return redirect("lists:index")


@login_required
def list_index(request):
    """Unified "Meine Listen & Formulare" overview. Lists and forms are not
    differentiated for the end user (CLAUDE.md / title-switcher decision) — both
    are containers to fill in, shown together in one section, tagged only by a
    muted kind label. The Listen/Formulare split survives solely in the admin
    surfaces (Django Admin, the per-object admin menus).
    """
    from forms.models import Form

    user = request.user
    own_lists = accessible_lists_for(user)
    if user.is_superuser:
        own_forms = Form.objects.all().order_by("title")
    else:
        own_forms = Form.objects.filter(access__user=user).distinct().order_by("title")
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
        {"own_lists": own_lists, "own_forms": own_forms, "public_lists": public_lists},
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

    # Remember this as the user's most recently opened list, so the next login
    # lands here (CLAUDE.md / *Post-login landing*). Only write on change.
    if request.user.last_selected_list_id != lst.pk:
        request.user.last_selected_list = lst
        request.user.save(update_fields=["last_selected_list"])

    is_admin = can_user_admin_list(request.user, lst)

    # via_associate lists (school classes) render the wide multi-person row
    # (child + linked parents); every other template stays one row per record.
    composed = (
        lst.template.member_subject_mode
        == ListTemplate.MemberSubjectMode.VIA_ASSOCIATE
    )
    # Associate-role attributes become the shared column headers of the wide
    # class-list table (one column per attribute, lined up across all parents).
    # Member-role attributes form the child grid; its header (rows × columns)
    # must line up with each child's value grid — same helper, no drift.
    associate_attrs = []
    member_header_lines = []
    member_col_count = 0
    if composed:
        rows = build_composed_rows(request.user, lst)
        for row in rows:
            row["can_edit"] = can_user_edit_record(request.user, row["record"])
            for parent in row["parents"]:
                parent["can_edit"] = can_user_edit_record(
                    request.user, parent["record"]
                )
        associate_attrs = [
            a
            for a in lst.template.attributes.all()
            if a.applies_to_role == ListAttribute.AppliesTo.ASSOCIATE
        ]
        # The shared column header: one comma-separated line of labels per
        # display_row (in position order), mirroring the value rows. Header and
        # values line up by order, not by column (the value cells are content-
        # sized), so the labels within a line are comma-separated.
        member_header, member_col_count = member_grid_header(lst.template)
        member_header_lines = [
            ", ".join(a.name for a in hrow if a is not None) for hrow in member_header
        ]
    else:
        rows = build_visible_rows(request.user, lst)
        for row in rows:
            row["can_edit"] = can_user_edit_record(request.user, row["record"])

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
            "composed": composed,
            "associate_attrs": associate_attrs,
            "member_header_lines": member_header_lines,
            "member_col_count": member_col_count,
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
        # Self-registering makes the user a member of the Benutzergruppe —
        # without this they cannot see the (private) list they just joined,
        # receive none of its mail, and are not addressable as an audience.
        ListAccess.objects.get_or_create(list=lst, user=request.user)
    return redirect("lists:record_edit", pk=lst.pk, record_pk=record.pk)


def _consume_invite_and_redirect(request, invite: ListInviteToken):
    """Inside an authenticated session, finalize the invitation.

    Two shapes, branched on the invite's mode:

    - ``via_associate``: the invited person is a *guardian*, not the list
      member. They are routed into the associate wizard, where they declare
      the child (= member record), their own associate record, and optionally
      a second parent. Creating a bare associate record with
      ``subject=target_person`` here would be wrong — it would leave the
      invitee with list access but no child and no place in the per-child
      rows. Token consumption is deferred to the wizard's save (see
      ``record_create_associate``) so an abandoned wizard leaves the invite
      reusable and writes no phantom records; the session grant marker lets
      the wizard open before the invitee has ListAccess.

    - ``self``: the invited person *is* the member. Create (or fetch) the
      LIST_RECORD with subject=target_person, write RecordManager(invited),
      join the Benutzergruppe, mark the token consumed, and land in the
      record-edit form.

    Caller has already verified that the visitor's USER is bound to
    `invite.target_person`.
    """
    with transaction.atomic():
        invite = ListInviteToken.objects.select_for_update().get(pk=invite.pk)
        if invite.is_consumed or invite.is_expired:
            return redirect("lists:detail", pk=invite.list_id)

        if invite.mode == ListInviteToken.Mode.VIA_ASSOCIATE:
            request.session["wizard_grant_list_id"] = invite.list_id
            request.session["wizard_invite_token"] = invite.token
            return redirect("lists:record_create_associate", pk=invite.list_id)

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
                role=ListRecord.Role.MEMBER,
            )
        if not RecordManager.objects.filter(record=record, user=request.user).exists():
            RecordManager.objects.create(
                record=record,
                user=request.user,
                basis=RecordManager.Basis.INVITED,
            )
        # Accepting the invitation joins the Benutzergruppe — required to see a
        # private list, receive its mail, and be addressable as an audience.
        ListAccess.objects.get_or_create(list=invite.list, user=request.user)
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
            sent = _send_mail_safe(
                subject=f'Einladung zur Liste „{safe_title}"',
                message=(
                    f"Hallo,\n\n"
                    f'{request.user.person} hat Sie zur Liste „{safe_title}" eingeladen.\n\n'
                    f"Klicken Sie hier, um beizutreten:\n{link}\n\n"
                    f"Der Link ist gültig bis {token.expires_at:%d.%m.%Y}.\n"
                ),
                recipient_list=[target_email],
            )
            if sent:
                messages.success(request, f"Einladung an {target_email} versendet.")
            else:
                messages.error(
                    request,
                    f"Die Einladung an {target_email} konnte nicht per Mail "
                    f"versendet werden (Mail-Server-Fehler). Der Link ist "
                    f"trotzdem gültig — Sie können ihn manuell weitergeben: {link}",
                )
            return redirect("lists:detail", pk=lst.pk)
    else:
        form = ListInviteForm(list_obj=lst, inviting_user=request.user)
    return render(request, "lists/invite_create.html", {"list_obj": lst, "form": form})


def _editable_associate_records(user, lst: List, child: ListRecord):
    """The associate (parent) records linked to a child `member` record in this
    list that `user` may edit — for the combined family edit dialog. Returns
    ``[(record, role_label), …]`` ordered stably by record id.
    """
    rel_role = dict(
        PersonRelationship.objects.filter(subject_person_id=child.subject_id)
        .values_list("related_person_id", "role")
    )
    if not rel_role:
        return []
    assoc = (
        ListRecord.objects.filter(
            list=lst,
            role=ListRecord.Role.ASSOCIATE,
            subject_id__in=list(rel_role.keys()),
            archived_at__isnull=True,
        )
        .select_related("subject")
        .order_by("id")
    )
    return [
        (rec, _role_label(rel_role.get(rec.subject_id, ""))) for rec in assoc
        if can_user_edit_record(user, rec)
    ]


@login_required
def record_edit(request, pk: int, record_pk: int):
    """Edit a record. For a child (`member`) record in a `via_associate` list
    this is a *combined family dialog*: the child plus each linked parent
    (associate) record the user may edit, each as its own prefixed
    RecordEditForm inside a single HTML form. The anchor keeps the unprefixed
    field names (backward compatible); associates are namespaced `a<pk>-…` so
    the POST fields don't collide. One submit saves them all.
    """
    lst = get_object_or_404(List, pk=pk)
    record = get_object_or_404(ListRecord, pk=record_pk, list=lst)
    if not can_user_edit_record(request.user, record):
        return HttpResponseForbidden("Sie dürfen diesen Eintrag nicht bearbeiten.")

    # (kind, record, role_label, prefix) — the anchor first, unprefixed.
    section_specs = [("member" if record.role == ListRecord.Role.MEMBER else "associate", record, "", None)]
    if (
        lst.template.member_subject_mode == ListTemplate.MemberSubjectMode.VIA_ASSOCIATE
        and record.role == ListRecord.Role.MEMBER
    ):
        for arec, role in _editable_associate_records(request.user, lst, record):
            section_specs.append(("associate", arec, role, f"a{arec.pk}"))

    data = request.POST if request.method == "POST" else None
    sections = [
        {
            "kind": kind,
            "record": rec,
            "role": role,
            "form": RecordEditForm(data, record=rec, user=request.user, prefix=prefix),
        }
        for (kind, rec, role, prefix) in section_specs
    ]

    if request.method == "POST":
        # Validate every form (list comp forces evaluation so all errors show).
        if all([s["form"].is_valid() for s in sections]):
            for s in sections:
                s["form"].save()
            return redirect("lists:detail", pk=lst.pk)

    return render(
        request,
        "lists/record_edit.html",
        {
            "list_obj": lst,
            "record": record,
            "sections": sections,
            "can_transfer": can_user_initiate_transfer(request.user, record),
        },
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
        # Join the Benutzergruppe (see record_create_self): otherwise the QR
        # joiner is locked out of a private list and receives no list mail.
        ListAccess.objects.get_or_create(list=lst, user=request.user)
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
            # The wizard reads the session marker to allow access even when
            # the visitor is not yet in the list's Benutzergruppe.
            request.session["wizard_grant_list_id"] = lst.pk
            return redirect("lists:record_create_associate", pk=lst.pk)
        return _join_self_mode(request, lst)

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


# ---------------------------------------------------------------------------
# via_associate Wizard (Phase 3b-2)
# ---------------------------------------------------------------------------


def _user_may_open_associate_wizard(user, lst: List, request) -> bool:
    """The wizard is reachable for:
    - superusers,
    - users who can already see the list (existing members/admins/public list),
    - users who arrived via a fresh QR-join (session marker
      `wizard_grant_list_id`).

    The session marker is needed because a fresh visitor on a private list has
    no ListAccess yet; without it `can_user_see_list` would 403 immediately
    after the QR-handler redirected here.
    """
    if not getattr(user, "is_authenticated", False):
        return False
    if user.is_superuser:
        return True
    if can_user_see_list(user, lst):
        return True
    return request.session.get("wizard_grant_list_id") == lst.pk


@login_required
def record_create_associate(request, pk: int):
    """`via_associate` onboarding: a USER declares another PERSON (a child,
    typically) as the member and themselves as guardian.

    Reachable via:
    - `lists:record_create_associate` URL directly (from the list-detail UI),
    - automatic redirect from `lists:join_via_token` POST when the LIST's
      template is `via_associate` (the QR scanner is bounced through register/
      login if needed and lands here authenticated).
    """
    lst = get_object_or_404(List, pk=pk)
    if lst.archived_at is not None:
        return HttpResponseForbidden("Diese Liste ist archiviert.")
    if (
        lst.template.member_subject_mode
        != ListTemplate.MemberSubjectMode.VIA_ASSOCIATE
    ):
        return HttpResponseForbidden(
            "Diese Liste ist nicht im via_associate-Modus. "
            "Bitte den 'Mich eintragen'-Button benutzen."
        )
    if not _user_may_open_associate_wizard(request.user, lst, request):
        return HttpResponseForbidden("Sie haben keinen Zugriff auf diese Liste.")

    if request.method == "POST":
        form = AssociateWizardForm(
            request.POST, list_obj=lst, user=request.user
        )
        if form.is_valid():
            record = form.save()
            # One-shot session marker is now consumed.
            request.session.pop("wizard_grant_list_id", None)
            # If the wizard was reached via a LIST_INVITE_TOKEN (via_associate
            # branch of _consume_invite_and_redirect), the token is consumed
            # only now — on a real save — so an abandoned wizard leaves the
            # invite reusable. Idempotent: a stale/already-consumed token is a
            # no-op.
            invite_token = request.session.pop("wizard_invite_token", None)
            if invite_token:
                ListInviteToken.objects.filter(
                    token=invite_token, consumed_at__isnull=True
                ).update(consumed_at=timezone.now())
            messages.success(
                request,
                f'„{record.subject}" wurde zur Liste „{lst.title}" hinzugefügt.',
            )
            return redirect("lists:record_edit", pk=lst.pk, record_pk=record.pk)
    else:
        form = AssociateWizardForm(list_obj=lst, user=request.user)

    return render(
        request,
        "lists/record_create_associate.html",
        {"list_obj": lst, "form": form},
    )


# ---------------------------------------------------------------------------
# Phase 4: Super-Admin test-send
# ---------------------------------------------------------------------------


@login_required
def list_send_test(request, pk: int):
    """Super-admin-only entry point to trigger a real SMTP fan-out across the
    list's Benutzergruppe. Used during deployment to verify provider config,
    headers, alias generation, and bounce routing end-to-end.

    Restricted to super-admin: regular list-admins shouldn't be able to mass-
    mail their list from the UI without going through the inbound pipeline
    (Phase 5b) — that path has the anti-spoof release-link gate which this
    test-send deliberately bypasses.
    """
    lst = get_object_or_404(List, pk=pk)
    if not is_super_admin(request.user):
        return HttpResponseForbidden("Nur Super-Admins dürfen Test-Mails versenden.")
    if lst.archived_at is not None:
        return HttpResponseForbidden("Diese Liste ist archiviert.")

    recipients = list_recipient_emails(lst)

    if request.method == "POST":
        form = TestSendForm(request.POST)
        if form.is_valid():
            sender_email = (
                request.user.person.email
                or settings.DEFAULT_FROM_EMAIL
            )
            with transaction.atomic():
                created = enqueue_list_fanout(
                    list_obj=lst,
                    from_email=sender_email,
                    subject=form.cleaned_data["subject"],
                    body=form.cleaned_data["body"],
                )
            if created:
                messages.success(
                    request,
                    f"Test-Mail an {len(created)} Empfänger eingereiht.",
                )
            else:
                messages.info(
                    request,
                    "Liste hat keine Empfänger mit E-Mail-Adresse — nichts versendet.",
                )
            return redirect("lists:detail", pk=lst.pk)
    else:
        form = TestSendForm()

    return render(
        request,
        "lists/send_test.html",
        {"list_obj": lst, "form": form, "recipients": recipients},
    )


# ---------------------------------------------------------------------------
# Phase 5b: mail release / approval click endpoint
# ---------------------------------------------------------------------------


def _release_forward(rel: MailReleaseToken, inbound: InboundMessage, anonymize: bool) -> None:
    """Commit a forward: fan out the stored message to the token's source (a
    list, or an aggregate alias resolved to its send-time recipient set), flip
    the inbound to FORWARDED, consume the token. Re-locks the token under
    select_for_update so two concurrent clicks (e.g. two list admins) cannot
    double-forward — the loser sees a consumed token and no-ops.
    """
    from .inbound_pipeline import extract_subject_and_body, parse_message

    msg = parse_message(bytes(inbound.raw_eml))
    subject, body = extract_subject_and_body(msg)
    from_email = inbound.from_email or settings.DEFAULT_FROM_EMAIL
    with transaction.atomic():
        locked = MailReleaseToken.objects.select_for_update().get(pk=rel.pk)
        if not locked.is_usable:
            return
        if locked.aggregate_id is not None:
            from .aggregates import aggregate_recipient_emails

            enqueue_aggregate_fanout(
                aggregate=locked.aggregate,
                recipients=aggregate_recipient_emails(locked.aggregate),
                from_email=from_email,
                subject=subject,
                body=body,
                anonymize=anonymize,
            )
        else:
            enqueue_list_fanout(
                list_obj=locked.list,
                from_email=from_email,
                subject=subject,
                body=body,
                anonymize=anonymize,
            )
        inbound.decision = InboundMessage.Decision.FORWARDED
        inbound.reason = f"Über Freigabe-Link weitergeleitet (anonymize={anonymize})."
        inbound.save(update_fields=["decision", "reason"])
        locked.consumed_at = timezone.now()
        locked.resolution = MailReleaseToken.Resolution.FORWARDED
        locked.save(update_fields=["consumed_at", "resolution"])


def _release_reject(rel: MailReleaseToken, inbound: InboundMessage) -> None:
    with transaction.atomic():
        locked = MailReleaseToken.objects.select_for_update().get(pk=rel.pk)
        if not locked.is_usable:
            return
        inbound.decision = InboundMessage.Decision.REJECTED
        inbound.reason = "Über Freigabe-Link abgelehnt."
        inbound.save(update_fields=["decision", "reason"])
        locked.consumed_at = timezone.now()
        locked.resolution = MailReleaseToken.Resolution.REJECTED
        locked.save(update_fields=["consumed_at", "resolution"])


def mail_release(request, token: str):
    """Click-time handler for a MailReleaseToken (member-release or admin-
    approval). Possession of the link is the authorisation — it was mailed to
    the sender (anti-spoofing) or the list admins. No login required.

    M10 pattern: GET renders a confirmation page with no side effects so mail-
    client link-prefetchers cannot forward or reject; the decision commits only
    on the CSRF-protected POST.
    """
    rel = get_object_or_404(
        MailReleaseToken.objects.select_related("inbound", "list", "aggregate"),
        token=token,
    )
    if rel.is_consumed:
        return render(
            request,
            "lists/invite_problem.html",
            {"reason": "Diese Freigabe wurde bereits bearbeitet."},
            status=410,
        )
    if rel.is_expired:
        return render(
            request,
            "lists/invite_problem.html",
            {"reason": "Diese Freigabe ist abgelaufen."},
            status=410,
        )

    inbound = rel.inbound
    # The release target is either a list or an aggregate alias; both expose a
    # `.title`, which is all the release templates render.
    target = rel.list or rel.aggregate

    if request.method == "POST":
        if request.POST.get("action") == "reject":
            _release_reject(rel, inbound)
            return render(
                request,
                "lists/mail_release_done.html",
                {"list_obj": target, "rel": rel, "action": "rejected"},
            )
        anonymize = rel.offer_anonymize and bool(request.POST.get("anonymize"))
        _release_forward(rel, inbound, anonymize)
        return render(
            request,
            "lists/mail_release_done.html",
            {"list_obj": target, "rel": rel, "action": "forwarded", "anonymized": anonymize},
        )

    return render(
        request,
        "lists/mail_release_confirm.html",
        {"list_obj": target, "rel": rel, "inbound": inbound},
    )


# ---------------------------------------------------------------------------
# Phase 6: school-class lifecycle
# ---------------------------------------------------------------------------


def _rollover_field_key(merge_key: str) -> str:
    """Merge keys look like "G8:0" (curriculum:parent_id); ":" is awkward in
    HTML form-field names, so swap it for "-" for the override field names.
    """
    return merge_key.replace(":", "-")


@login_required
def list_cohort_edit(request, pk: int):
    """Edit a list's cohort metadata (grade / track / curriculum). List admins
    and super-admin only — these fields drive the rollover.
    """
    lst = get_object_or_404(List, pk=pk)
    if not can_user_admin_list(request.user, lst):
        return HttpResponseForbidden("Nur Listen-Admins dürfen Kohorten-Daten ändern.")
    if request.method == "POST":
        form = CohortEditForm(request.POST, instance=lst)
        if form.is_valid():
            form.save()
            messages.success(request, "Kohorten-Daten gespeichert.")
            return redirect("lists:detail", pk=lst.pk)
    else:
        form = CohortEditForm(instance=lst)
    return render(request, "lists/cohort_edit.html", {"list_obj": lst, "form": form})


@login_required
def rollover_preview(request):
    """Super-admin-only rollover wizard: show the proposed action for every
    school-class list. No side effects — execution is a separate POST.
    """
    if not is_super_admin(request.user):
        return HttpResponseForbidden("Nur Super-Admins dürfen den Schuljahres-Übergang ausführen.")
    plan = lifecycle.plan_rollover()
    actions = [
        {
            "list_id": a.list_id,
            "title": a.title,
            "current_label": a.current_label,
            "kind": a.kind,
            "default_title": a.default_title,
            "default_alias": a.default_alias,
        }
        for a in plan.actions
    ]
    merges = [
        {
            "field_key": _rollover_field_key(m.key),
            "curriculum": m.curriculum,
            "source_labels": m.source_labels,
            "default_title": m.default_title,
            "default_alias": m.default_alias,
        }
        for m in plan.merges
    ]
    return render(
        request,
        "lists/rollover_preview.html",
        {"actions": actions, "merges": merges, "is_empty": plan.is_empty},
    )


@login_required
@require_POST
def rollover_execute(request):
    if not is_super_admin(request.user):
        return HttpResponseForbidden("Nur Super-Admins dürfen den Schuljahres-Übergang ausführen.")
    plan = lifecycle.plan_rollover()
    if plan.is_empty:
        messages.info(request, "Keine Klassenlisten zum Hochstufen gefunden.")
        return redirect("lists:rollover_preview")

    overrides = {"list": {}, "merge": {}}
    for a in plan.actions:
        title = (request.POST.get(f"title_{a.list_id}") or "").strip()
        alias = (request.POST.get(f"alias_{a.list_id}") or "").strip()
        overrides["list"][a.list_id] = {"title": title, "alias": alias}
    for m in plan.merges:
        fk = _rollover_field_key(m.key)
        title = (request.POST.get(f"merge_title_{fk}") or "").strip()
        alias = (request.POST.get(f"merge_alias_{fk}") or "").strip()
        overrides["merge"][m.key] = {"title": title, "alias": alias}

    try:
        result = lifecycle.execute_rollover(plan, overrides)
    except Exception as exc:  # noqa: BLE001 — surface alias clashes etc. to the admin
        messages.error(
            request,
            f"Übergang fehlgeschlagen (nichts geändert): {exc}. "
            "Bitte E-Mail-Aliase auf Eindeutigkeit prüfen.",
        )
        return redirect("lists:rollover_preview")

    messages.success(
        request,
        "Schuljahres-Übergang ausgeführt: "
        f"{len(result['advanced'])} hochgestuft, "
        f"{len(result['k1_to_k2'])} K1→K2, "
        f"{len(result['merged_into'])} neue Kursstufe(n), "
        f"{len(result['archived'])} archiviert.",
    )
    return redirect("lists:rollover_preview")


@login_required
def transfer_initiate(request, pk: int, record_pk: int):
    """Request a single-PERSON class transfer. Creates a PENDING_TRANSFER that
    the destination-list admin must accept (CLAUDE.md / *Class transfer*).
    """
    lst = get_object_or_404(List, pk=pk)
    record = get_object_or_404(ListRecord, pk=record_pk, list=lst)
    if not can_user_initiate_transfer(request.user, record):
        return HttpResponseForbidden("Sie dürfen diesen Eintrag nicht umziehen.")

    if request.method == "POST":
        form = TransferInitiateForm(request.POST, source_list=lst)
        if form.is_valid():
            to_list = form.cleaned_data["to_list"]
            existing = PendingTransfer.objects.filter(
                person=record.subject,
                from_list=lst,
                to_list=to_list,
                status=PendingTransfer.Status.PENDING,
            ).exists()
            if existing:
                messages.info(request, "Für diese Person läuft bereits ein Antrag auf diese Liste.")
            else:
                PendingTransfer.objects.create(
                    from_list=lst,
                    to_list=to_list,
                    person=record.subject,
                    requested_by=request.user,
                )
                messages.success(
                    request,
                    f'Umzug von „{record.subject}" nach „{to_list.title}" beantragt — '
                    "die Ziel-Admins müssen zustimmen.",
                )
            return redirect("lists:detail", pk=lst.pk)
    else:
        form = TransferInitiateForm(source_list=lst)
    return render(
        request,
        "lists/transfer_initiate.html",
        {"list_obj": lst, "record": record, "form": form},
    )


@login_required
def transfers_pending(request, pk: int):
    """Destination-admin queue: pending incoming transfers for this list."""
    lst = get_object_or_404(List, pk=pk)
    if not can_user_admin_list(request.user, lst):
        return HttpResponseForbidden("Nur Listen-Admins sehen die Umzugs-Anträge.")
    transfers = (
        PendingTransfer.objects.filter(to_list=lst, status=PendingTransfer.Status.PENDING)
        .select_related("from_list", "person", "requested_by__person")
    )
    return render(
        request,
        "lists/transfers_pending.html",
        {"list_obj": lst, "transfers": transfers},
    )


def _copy_record_access_to(src: ListRecord, new: ListRecord, dest_list: List) -> None:
    """Carry the source record's visibility matrix (the LIST_RECORD_ACCESS rows)
    onto the freshly-created destination record, so the subject's per-field
    disclosure choices — and any anonymisation of their name — survive a class
    transfer instead of silently resetting (review finding A).

    Two adjustments:

    * The ``ListRecord.post_save`` signal already wrote a default
      ``(name → public)`` row on ``new``; drop the whole matrix on ``new`` first
      so a copied (possibly anonymised) name-sentinel set fully replaces it and
      no stray public name grant lingers.
    * Audience rows pointing at the *source* list are remapped to ``dest_list``:
      "visible to my class" must follow the record into its new class, since the
      source class's Benutzergruppe no longer contains the new classmates. Public
      (NULL) and unrelated-list audiences copy verbatim. ``get_or_create`` guards
      the rare case where a remap collides with an already-present dest audience.
    """
    ListRecordAccess.objects.filter(record=new).delete()
    for acc in src.access.all():
        audience_id = dest_list.id if acc.audience_id == src.list_id else acc.audience_id
        ListRecordAccess.objects.get_or_create(
            record=new,
            attribute_id=acc.attribute_id,
            sentinel=acc.sentinel,
            audience_id=audience_id,
        )


def _copy_record_to(
    src: ListRecord, dest_list: List, now, *, archive_source: bool = True
) -> ListRecord | None:
    """Copy one record (values + visibility matrix + managers + manager access)
    into dest_list. Archives the source unless ``archive_source`` is False (see
    finding B: a parent associate record stays in the source class while another
    of their children remains there). Returns the new record, or None if the
    subject already has an active record in dest.
    """
    def _maybe_archive_source():
        if archive_source:
            src.archived_at = now
            src.save(update_fields=["archived_at"])

    if ListRecord.objects.filter(
        list=dest_list, subject=src.subject, archived_at__isnull=True
    ).exists():
        _maybe_archive_source()
        return None
    new = ListRecord.objects.create(
        list=dest_list, subject=src.subject, role=src.role
    )
    for v in src.values.all():
        ListRecordValue.objects.create(record=new, attribute=v.attribute, value=v.value)
    _copy_record_access_to(src, new, dest_list)
    for mgr in src.managers.all():
        RecordManager.objects.get_or_create(
            record=new, user=mgr.user, defaults={"basis": mgr.basis}
        )
        ListAccess.objects.get_or_create(list=dest_list, user=mgr.user)
    _maybe_archive_source()
    return new


@login_required
@require_POST
def transfer_decide(request, transfer_pk: int):
    """Destination admin accepts or rejects a pending transfer. On accept the
    moving Person's record plus their associates' records migrate to the
    destination; source records are archived (trail kept).
    """
    transfer = get_object_or_404(
        PendingTransfer.objects.select_related("from_list", "to_list", "person"),
        pk=transfer_pk,
    )
    if not can_user_decide_transfer(request.user, transfer):
        return HttpResponseForbidden("Nur Ziel-Admins entscheiden über den Umzug.")
    if not transfer.is_pending:
        messages.info(request, "Dieser Antrag wurde bereits bearbeitet.")
        return redirect("lists:transfers_pending", pk=transfer.to_list_id)

    action = request.POST.get("action")
    now = timezone.now()
    with transaction.atomic():
        locked = PendingTransfer.objects.select_for_update().get(pk=transfer.pk)
        if not locked.is_pending:
            return redirect("lists:transfers_pending", pk=transfer.to_list_id)

        if action == "reject":
            locked.status = PendingTransfer.Status.REJECTED
        else:
            person = locked.person
            src = locked.from_list
            dest = locked.to_list
            parent_ids = list(
                PersonRelationship.objects.filter(subject_person=person).values_list(
                    "related_person_id", flat=True
                )
            )
            # The moving child's own member record always migrates + archives.
            for rec in ListRecord.objects.filter(
                list=src, subject=person, archived_at__isnull=True
            ):
                _copy_record_to(rec, dest, now)

            # Parents (associates) migrate to the destination so the child keeps
            # its parent columns there. A parent record stays in the source class
            # if another of their children remains there: there is only one
            # per-(list, subject) associate record, so archiving it would strip
            # that parent from the remaining sibling's row (finding B). The
            # destination copy is created either way; only the source archival is
            # conditional.
            for rec in ListRecord.objects.filter(
                list=src,
                subject_id__in=parent_ids,
                role=ListRecord.Role.ASSOCIATE,
                archived_at__isnull=True,
            ):
                other_child_ids = (
                    PersonRelationship.objects.filter(related_person_id=rec.subject_id)
                    .exclude(subject_person=person)
                    .values_list("subject_person_id", flat=True)
                )
                parent_stays = ListRecord.objects.filter(
                    list=src,
                    subject_id__in=list(other_child_ids),
                    role=ListRecord.Role.MEMBER,
                    archived_at__isnull=True,
                ).exists()
                _copy_record_to(rec, dest, now, archive_source=not parent_stays)
            locked.status = PendingTransfer.Status.ACCEPTED

        locked.resolved_at = now
        locked.resolved_by = request.user
        locked.save(update_fields=["status", "resolved_at", "resolved_by"])

    if action == "reject":
        messages.success(request, "Umzug abgelehnt.")
    else:
        messages.success(
            request, f'„{transfer.person}" wurde nach „{transfer.to_list.title}" übernommen.'
        )
    return redirect("lists:transfers_pending", pk=transfer.to_list_id)


@login_required
@require_POST
def record_remove(request, pk: int, record_pk: int):
    """Self-removal: archive a record. Not transfer-gated (CLAUDE.md). When the
    visitor removes *their own* record (opting out of the list) they also leave
    the Benutzergruppe and drop any admin row — unless that would orphan the
    list (zero admins), which is blocked with a "handover first" message.
    """
    lst = get_object_or_404(List, pk=pk)
    record = get_object_or_404(ListRecord, pk=record_pk, list=lst)
    if not can_user_edit_record(request.user, record):
        return HttpResponseForbidden("Sie dürfen diesen Eintrag nicht entfernen.")

    leaving_self = record.subject_id == getattr(request.user, "person_id", None)
    if leaving_self and would_self_removal_leave_no_admin(request.user, lst):
        messages.error(
            request,
            "Sie sind der einzige Admin dieser Liste. Bitte übergeben Sie die "
            "Admin-Rolle zuerst an eine Nachfolgerin/einen Nachfolger.",
        )
        return redirect("lists:detail", pk=lst.pk)

    with transaction.atomic():
        record.archived_at = timezone.now()
        record.save(update_fields=["archived_at"])
        if leaving_self:
            ListAccess.objects.filter(list=lst, user=request.user).delete()
            ListAdmin.objects.filter(list=lst, user=request.user).delete()
    messages.success(request, "Eintrag entfernt.")
    return redirect("lists:detail", pk=lst.pk)


# ---------------------------------------------------------------------------
# Admin handover (ADMIN_INVITE_TOKEN)
# ---------------------------------------------------------------------------


@login_required
def admin_manage(request, pk: int):
    """List admins, plus a form to invite a successor (`handover`) or an extra
    admin (`add`). Admins of the list and super-admin only.
    """
    lst = get_object_or_404(List, pk=pk)
    if not can_user_admin_list(request.user, lst):
        return HttpResponseForbidden("Nur Listen-Admins verwalten die Admin-Rollen.")

    if request.method == "POST":
        form = AdminInviteForm(request.POST)
        if form.is_valid():
            initiator_is_admin = ListAdmin.objects.filter(
                list=lst, user=request.user
            ).exists()
            with transaction.atomic():
                token = AdminInviteToken.objects.create(
                    list=lst,
                    from_user=request.user if initiator_is_admin else None,
                    to_email=form.cleaned_data["to_email"],
                    mode=form.cleaned_data["mode"],
                )
            link = request.build_absolute_uri(
                reverse("lists:admin_invite_accept", kwargs={"token": token.token})
            )
            safe_title = _sanitize_header_value(lst.title)
            sent = _send_mail_safe(
                subject=f'Admin-Einladung: Liste „{safe_title}"',
                message=(
                    f"Hallo,\n\n"
                    f'Sie wurden als Admin der Liste „{safe_title}" vorgesehen.\n\n'
                    f"Öffnen Sie den folgenden Link und melden Sie sich an, um die "
                    f"Admin-Rolle zu übernehmen:\n{link}\n\n"
                    f"Der Link ist gültig bis {token.expires_at:%d.%m.%Y}.\n"
                ),
                recipient_list=[token.to_email],
            )
            if sent:
                messages.success(
                    request, f"Admin-Einladung an {token.to_email} versendet."
                )
            else:
                messages.error(
                    request,
                    f"Die Admin-Einladung an {token.to_email} konnte nicht per "
                    f"Mail versendet werden (Mail-Server-Fehler). Der Link ist "
                    f"trotzdem gültig — Sie können ihn manuell weitergeben: {link}",
                )
            return redirect("lists:admin_manage", pk=lst.pk)
    else:
        form = AdminInviteForm()

    admins = ListAdmin.objects.filter(list=lst).select_related("user__person")
    pending = lst.admin_invitations.filter(consumed_at__isnull=True)
    return render(
        request,
        "lists/admin_manage.html",
        {"list_obj": lst, "form": form, "admins": admins, "pending": pending},
    )


def admin_invite_accept(request, token: str):
    """Click handler for an AdminInviteToken. Authentication is mandatory — a
    click alone never confers admin rights (CLAUDE.md / *Admin handover*).

    M10 pattern: GET is side-effect-free (confirmation page); the token is
    consumed only on the CSRF-protected POST. An unauthenticated POST stashes
    the token and routes the visitor through login (existing USER) or
    registration + passkey enrollment (not-yet-USER), returning here afterwards.
    """
    inv = get_object_or_404(AdminInviteToken.objects.select_related("list"), token=token)
    if inv.is_consumed:
        return render(
            request,
            "lists/invite_problem.html",
            {"reason": "Diese Admin-Einladung wurde bereits eingelöst."},
            status=410,
        )
    if inv.is_expired:
        return render(
            request,
            "lists/invite_problem.html",
            {"reason": "Diese Admin-Einladung ist abgelaufen."},
            status=410,
        )

    lst = inv.list

    if request.method == "POST":
        if not request.user.is_authenticated:
            request.session["pending_admin_invite_token"] = inv.token
            from accounts.models import Person

            is_user = Person.objects.filter(
                email__iexact=inv.to_email, user__isnull=False
            ).exists()
            if is_user:
                return redirect(
                    reverse("accounts:login") + "?" + urlencode({"next": request.path})
                )
            return redirect(
                reverse("accounts:register_start")
                + "?"
                + urlencode({"email": inv.to_email})
            )

        # Identity binding: a click + authentication alone must not confer
        # admin rights to *anyone* holding the link — the token is addressed to
        # `to_email`, and only a USER on that address may consume it. Mirrors
        # the wrong-user rejection on the lower-privileged member invite
        # (`invite_accept`); without it any logged-in visitor who obtains a
        # forwarded/leaked admin link could promote themselves.
        user_email = (getattr(request.user.person, "email", "") or "").strip().lower()
        if user_email != inv.to_email.strip().lower():
            return render(
                request,
                "lists/invite_problem.html",
                {
                    "reason": (
                        "Diese Admin-Einladung gehört nicht zu Ihrem Konto. "
                        "Bitte melden Sie sich mit dem eingeladenen Konto an "
                        "und öffnen Sie den Link erneut."
                    )
                },
                status=403,
            )

        with transaction.atomic():
            locked = AdminInviteToken.objects.select_for_update().get(pk=inv.pk)
            if not locked.is_usable:
                return redirect("lists:detail", pk=lst.pk)
            ListAdmin.objects.get_or_create(list=locked.list, user=request.user)
            if (
                locked.mode == AdminInviteToken.Mode.HANDOVER
                and locked.from_user_id
                and locked.from_user_id != request.user.id
            ):
                ListAdmin.objects.filter(
                    list=locked.list, user_id=locked.from_user_id
                ).delete()
            locked.consumed_at = timezone.now()
            locked.save(update_fields=["consumed_at"])
        messages.success(request, f'Sie sind jetzt Admin der Liste „{lst.title}".')
        return redirect("lists:detail", pk=lst.pk)

    # GET: confirmation page, no side effects.
    next_action = "accept" if request.user.is_authenticated else "auth"
    return render(
        request,
        "lists/admin_invite_confirm.html",
        {"list_obj": lst, "invite": inv, "next_action": next_action},
    )
