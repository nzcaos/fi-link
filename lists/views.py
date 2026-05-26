"""Views for the lists app (Phase 3a).

Scope: list-index, list-create, list-detail stub. Per-record interactions and
the visibility matrix follow in Phase 3b.
"""
from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Q
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render

from .forms import ListCreateForm
from .models import List, ListAdmin
from .permissions import can_user_admin_list, can_user_see_list


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
    return render(
        request,
        "lists/detail.html",
        {
            "list_obj": lst,
            "is_admin": can_user_admin_list(request.user, lst),
        },
    )
