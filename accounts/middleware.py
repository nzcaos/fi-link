"""Ask signed-in users for consent to the current privacy notice.

Registration already records consent (checkbox on the form). This catches
everyone else once: accounts that predate the notice, accounts created by an
operator command (`bootstrap_super_admin`), and — after a bump of
`settings.PRIVACY_POLICY_VERSION` — every user, so a changed notice is
re-consented to.

Only full-page GETs are redirected. HTMX fragments, fetch()/JSON endpoints and
POSTs pass through: a redirect there would break the request mid-flow, and
every such request starts from a page that was gated already.
"""
from __future__ import annotations

from urllib.parse import urlencode

from django.conf import settings
from django.shortcuts import redirect
from django.urls import reverse

# The consent page itself, the notice, auth ceremonies/logout, help, static
# assets and the Django admin stay reachable without consent.
_EXEMPT_PREFIXES = ("/auth/", "/datenschutz/", "/hilfe/", "/static/", "/admin/")


class PrivacyConsentMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if self._needs_consent(request):
            url = reverse("accounts:privacy_consent")
            return redirect(f"{url}?{urlencode({'next': request.get_full_path()})}")
        return self.get_response(request)

    @staticmethod
    def _needs_consent(request) -> bool:
        if not settings.PRIVACY_CONSENT_REQUIRED or request.method != "GET":
            return False
        if request.headers.get("HX-Request"):
            return False
        user = getattr(request, "user", None)
        if user is None or not user.is_authenticated:
            return False
        if request.path.startswith(_EXEMPT_PREFIXES):
            return False
        return not user.has_current_privacy_consent
