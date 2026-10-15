"""The invitations that may open the registration gate ("grants").

A grant is a (session key, token) pair — see CLAUDE.md / "Registration
requires an invitation". It also rides on the ActivationToken
(`pending_key`/`pending_token`) so a join survives the activation round-trip.
Lookups import lazily: lists and forms depend on accounts, not vice versa.
"""
from __future__ import annotations

# The three token-model grants; `accounts.views._next_url_after_auth` pops the
# same keys after enrolment to finish the join — keep both in sync.
LIST_GRANT_KEYS = ("pending_invite_token", "pending_join_token", "pending_admin_invite_token")
FORM_GRANT_KEY = "pending_form_token"


def _list_token(key: str, value: str):
    from lists.models import AdminInviteToken, ListInviteToken, ListJoinToken

    model = {
        "pending_invite_token": ListInviteToken,
        "pending_join_token": ListJoinToken,
        "pending_admin_invite_token": AdminInviteToken,
    }.get(key)
    if model is None or not value:
        return None
    return model.objects.select_related("list").filter(token=value).first()


def grant_is_usable(key: str, value: str) -> bool:
    """True while the invitation behind (key, value) can still be redeemed."""
    if key in LIST_GRANT_KEYS:
        token = _list_token(key, value)
        return token is not None and token.is_usable
    if key == FORM_GRANT_KEY and value:
        from forms.models import Form

        form = Form.objects.filter(share_token=value, is_open=True).first()
        return form is not None and form.share_link_active
    return False


def grant_list(key: str, value: str):
    """The list a list-grant points at (usable or not), else None."""
    token = _list_token(key, value) if key in LIST_GRANT_KEYS else None
    return token.list if token is not None else None
