"""Thin Synapse HTTP client (docs/matrix-implementation-plan.md, Phase 1).

httpx, used SYNCHRONOUSLY: the callers are sync Django views, management
commands and procrastinate tasks, none of which need concurrency (one Synapse
call at a time per task), so an async client + asyncio.run() would only add
ceremony. matrix-nio is deliberately not used — E2EE is off and every operation
here is a plain HTTP call. The whole Matrix surface lives behind this one class,
so swapping the transport later (e.g. to nio for a server-side /sync consumer)
is a localized change.

Two API families go through the same base URL (Synapse's internal 8008):
  - Admin API  (/_synapse/admin/...)      — shared-secret registration.
  - Client-Server API (/_matrix/client/...) — acting as the service account
    with its access token (create room, invite, send, kick, ban).

Nothing here logs passwords or access tokens (A4).
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
from typing import Any

import httpx
from django.conf import settings

log = logging.getLogger(__name__)


class MatrixError(Exception):
    """A Synapse API call failed. Carries the Matrix errcode when available."""

    def __init__(self, message: str, *, errcode: str | None = None, status: int | None = None):
        super().__init__(message)
        self.errcode = errcode
        self.status = status


class MatrixClient:
    def __init__(self, base_url: str, server_name: str, admin_shared_secret: str, timeout: int = 30):
        self.base_url = base_url.rstrip("/")
        self.server_name = server_name
        self._admin_shared_secret = admin_shared_secret
        self.timeout = timeout

    @classmethod
    def from_settings(cls) -> "MatrixClient":
        return cls(
            base_url=settings.MATRIX_BASE_URL,
            server_name=settings.MATRIX_SERVER_NAME,
            admin_shared_secret=settings.MATRIX_ADMIN_SHARED_SECRET,
            timeout=settings.MATRIX_HTTP_TIMEOUT,
        )

    def user_id(self, localpart: str) -> str:
        return f"@{localpart}:{self.server_name}"

    # -- low-level ---------------------------------------------------------

    def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict | None = None,
        access_token: str | None = None,
    ) -> dict[str, Any]:
        headers = {}
        if access_token:
            headers["Authorization"] = f"Bearer {access_token}"
        try:
            with httpx.Client(base_url=self.base_url, timeout=self.timeout) as client:
                resp = client.request(method, path, json=json, headers=headers)
        except httpx.HTTPError as exc:
            raise MatrixError(f"transport error talking to Synapse: {exc}") from exc

        if resp.status_code >= 400:
            errcode = error = None
            try:
                body = resp.json()
                errcode = body.get("errcode")
                error = body.get("error")
            except ValueError:
                pass
            raise MatrixError(
                f"{method} {path} -> {resp.status_code} {errcode or ''} {error or ''}".strip(),
                errcode=errcode,
                status=resp.status_code,
            )
        if not resp.content:
            return {}
        return resp.json()

    # -- health ------------------------------------------------------------

    def versions(self) -> dict[str, Any]:
        """GET /_matrix/client/versions — used by the health check."""
        return self._request("GET", "/_matrix/client/versions")

    # -- admin API: account creation (Weg A) -------------------------------

    @staticmethod
    def generate_mac(
        shared_secret: str,
        nonce: str,
        user: str,
        password: str,
        admin: bool = False,
        user_type: str | None = None,
    ) -> str:
        """HMAC-SHA1 over the NUL-separated fields, exact Synapse admin order."""
        mac = hmac.new(key=shared_secret.encode("utf8"), digestmod=hashlib.sha1)
        mac.update(nonce.encode("utf8"))
        mac.update(b"\x00")
        mac.update(user.encode("utf8"))
        mac.update(b"\x00")
        mac.update(password.encode("utf8"))
        mac.update(b"\x00")
        mac.update(b"admin" if admin else b"notadmin")
        if user_type:
            mac.update(b"\x00")
            mac.update(user_type.encode("utf8"))
        return mac.hexdigest()

    def register_user(
        self,
        localpart: str,
        password: str,
        displayname: str,
        admin: bool = False,
    ) -> dict[str, Any]:
        """Two-step shared-secret registration. Returns access_token, user_id,
        home_server, device_id. Raises MatrixError(errcode='M_USER_IN_USE') on
        collision so callers can stay idempotent.
        """
        nonce = self._request("GET", "/_synapse/admin/v1/register")["nonce"]
        mac = self.generate_mac(self._admin_shared_secret, nonce, localpart, password, admin=admin)
        result = self._request(
            "POST",
            "/_synapse/admin/v1/register",
            json={
                "nonce": nonce,
                "username": localpart,
                "displayname": displayname,
                "password": password,
                "admin": admin,
                "mac": mac,
            },
        )
        log.info("matrix: registered user %s (admin=%s)", result.get("user_id"), admin)
        return result

    # -- client-server API (as the service account) -----------------------

    def create_room(
        self,
        access_token: str,
        *,
        name: str,
        topic: str = "",
        events_default: int = 0,
        history_visibility: str = "invited",
    ) -> str:
        """Create an invite-only room. invite/kick/ban are reserved to the
        creator (the service account, PL 100); events_default 0 = chat mode,
        50 = broadcast mode. Returns the room_id.
        """
        body = {
            "preset": "private_chat",
            "name": name,
            "topic": topic,
            "visibility": "private",
            "creation_content": {"m.federate": True},
            "power_level_content_override": {
                "invite": 100,
                "kick": 50,
                "ban": 50,
                "redact": 50,
                "events_default": events_default,
                "state_default": 50,
                "users_default": 0,
            },
            "initial_state": [
                {
                    "type": "m.room.history_visibility",
                    "state_key": "",
                    "content": {"history_visibility": history_visibility},
                },
                {
                    "type": "m.room.join_rules",
                    "state_key": "",
                    "content": {"join_rule": "invite"},
                },
            ],
        }
        result = self._request(
            "POST", "/_matrix/client/v3/createRoom", json=body, access_token=access_token
        )
        room_id = result["room_id"]
        log.info("matrix: created room %s (%s)", room_id, name)
        return room_id

    def get_room_state(self, access_token: str, room_id: str) -> list[dict[str, Any]]:
        """GET the full room state (list of state events) — used to verify a
        freshly created room's join_rules / history_visibility / power_levels.
        """
        from urllib.parse import quote

        rid = quote(room_id, safe="")
        return self._request(
            "GET",
            f"/_matrix/client/v3/rooms/{rid}/state",
            access_token=access_token,
        )  # type: ignore[return-value]

    def set_user_power_level(
        self, access_token: str, room_id: str, user_id: str, level: int
    ) -> None:
        """Update one user's power level (e.g. promote a list admin to 50).

        Reads the current m.room.power_levels state, patches the `users` map and
        writes it back — Matrix replaces the whole state event, so we must merge.
        """
        from urllib.parse import quote

        rid = quote(room_id, safe="")
        current = self._request(
            "GET",
            f"/_matrix/client/v3/rooms/{rid}/state/m.room.power_levels/",
            access_token=access_token,
        )
        users = dict(current.get("users", {}))
        users[user_id] = level
        current["users"] = users
        self._request(
            "PUT",
            f"/_matrix/client/v3/rooms/{rid}/state/m.room.power_levels/",
            json=current,
            access_token=access_token,
        )
        log.info("matrix: set power level %s for %s in %s", level, user_id, room_id)

    def set_room_name(self, access_token: str, room_id: str, name: str) -> None:
        """Set m.room.name — used to keep the room title in sync on rollover."""
        from urllib.parse import quote

        rid = quote(room_id, safe="")
        self._request(
            "PUT",
            f"/_matrix/client/v3/rooms/{rid}/state/m.room.name/",
            json={"name": name},
            access_token=access_token,
        )
        log.info("matrix: set room name %r for %s", name, room_id)

    def invite(self, access_token: str, room_id: str, user_id: str) -> None:
        from urllib.parse import quote

        rid = quote(room_id, safe="")
        self._request(
            "POST",
            f"/_matrix/client/v3/rooms/{rid}/invite",
            json={"user_id": user_id},
            access_token=access_token,
        )
        log.info("matrix: invited %s to %s", user_id, room_id)

    def send_message(self, access_token: str, room_id: str, body: str) -> str:
        """Send a plain-text message. Returns the event_id."""
        from urllib.parse import quote

        rid = quote(room_id, safe="")
        txn = secrets.token_urlsafe(16)
        result = self._request(
            "PUT",
            f"/_matrix/client/v3/rooms/{rid}/send/m.room.message/{txn}",
            json={"msgtype": "m.text", "body": body},
            access_token=access_token,
        )
        event_id = result.get("event_id", "")
        log.info("matrix: sent message %s to %s", event_id, room_id)
        return event_id

    def kick(self, access_token: str, room_id: str, user_id: str, reason: str = "") -> None:
        from urllib.parse import quote

        rid = quote(room_id, safe="")
        self._request(
            "POST",
            f"/_matrix/client/v3/rooms/{rid}/kick",
            json={"user_id": user_id, "reason": reason},
            access_token=access_token,
        )
        log.info("matrix: kicked %s from %s", user_id, room_id)

    def ban(self, access_token: str, room_id: str, user_id: str, reason: str = "") -> None:
        from urllib.parse import quote

        rid = quote(room_id, safe="")
        self._request(
            "POST",
            f"/_matrix/client/v3/rooms/{rid}/ban",
            json={"user_id": user_id, "reason": reason},
            access_token=access_token,
        )
        log.info("matrix: banned %s from %s", user_id, room_id)

    def unban(self, access_token: str, room_id: str, user_id: str) -> None:
        from urllib.parse import quote

        rid = quote(room_id, safe="")
        self._request(
            "POST",
            f"/_matrix/client/v3/rooms/{rid}/unban",
            json={"user_id": user_id},
            access_token=access_token,
        )
        log.info("matrix: unbanned %s from %s", user_id, room_id)

    def room_member_ids(self, access_token: str, room_id: str, memberships=("join", "invite")) -> set[str]:
        """Return the set of user ids whose membership is in `memberships`.

        Built from the room state (m.room.member events) — used by the reconcile
        task to find list members who are missing from the room.
        """
        present: set[str] = set()
        for ev in self.get_room_state(access_token, room_id):
            if ev.get("type") != "m.room.member":
                continue
            if ev.get("content", {}).get("membership") in memberships:
                state_key = ev.get("state_key")
                if state_key:
                    present.add(state_key)
        return present
