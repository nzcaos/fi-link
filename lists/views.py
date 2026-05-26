"""Views for the lists app.

Phase 3a: list-index, list-create, list-detail stub.
Phase 3b: list-detail with record rows, record-add/edit with visibility matrix.
"""
from __future__ import annotations

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
from .models import List, ListAdmin, ListInviteToken, ListRecord, RecordManager
from .permissions import (
    can_user_admin_list,
    can_user_edit_record,
    can_user_see_list,
)
from .visibility import visible_attributes_for


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
    for record in records_qs:
        attrs = list(visible_attributes_for(request.user, record))
        values_map = {v.attribute_id: v.value for v in record.values.all()}
        fields = [(a, values_map.get(a.pk, "")) for a in attrs]
        rows.append(
            {
                "record": record,
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
    """
    lst = get_object_or_404(List, pk=pk)
    if not can_user_see_list(request.user, lst):
        return HttpResponseForbidden("Sie haben keinen Zugriff auf diese Liste.")
    if lst.archived_at is not None:
        return HttpResponseForbidden("Diese Liste ist archiviert.")
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

        record, _ = ListRecord.objects.get_or_create(
            list=invite.list,
            subject=invite.target_person,
            defaults={
                "role": (
                    ListRecord.Role.MEMBER
                    if invite.mode == ListInviteToken.Mode.SELF
                    else ListRecord.Role.ASSOCIATE
                ),
            },
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

    if invite.target_person_id is None:
        if request.user.is_authenticated:
            invite.target_person_id = request.user.person_id
            invite.save(update_fields=["target_person"])
            return _consume_invite_and_redirect(request, invite)
        request.session["pending_invite_token"] = invite.token
        return redirect(
            f"{reverse('accounts:register_start')}?email={invite.target_email}"
        )

    if not request.user.is_authenticated:
        request.session["pending_invite_token"] = invite.token
        return redirect(f"{reverse('accounts:login')}?next={request.path}")
    if request.user.person_id != invite.target_person_id:
        return render(
            request,
            "lists/invite_problem.html",
            {
                "reason": (
                    f"Diese Einladung ist für {invite.target_person} bestimmt. "
                    "Bitte melden Sie sich als diese Person an."
                )
            },
            status=403,
        )
    return _consume_invite_and_redirect(request, invite)


@login_required
def list_invite(request, pk: int):
    lst = get_object_or_404(List, pk=pk)
    if not can_user_admin_list(request.user, lst):
        return HttpResponseForbidden("Nur Listen-Admins dürfen einladen.")

    if request.method == "POST":
        form = ListInviteForm(request.POST, list_obj=lst)
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
            send_mail(
                subject=f'Einladung zur Liste „{lst.title}"',
                message=(
                    f"Hallo,\n\n"
                    f'{request.user.person} hat Sie zur Liste „{lst.title}" eingeladen.\n\n'
                    f"Klicken Sie hier, um beizutreten:\n{link}\n\n"
                    f"Der Link ist gültig bis {token.expires_at:%d.%m.%Y}.\n"
                ),
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[target_email],
            )
            messages.success(request, f"Einladung an {target_email} versendet.")
            return redirect("lists:detail", pk=lst.pk)
    else:
        form = ListInviteForm(list_obj=lst)
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
