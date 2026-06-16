"""Phase-1 unit tests for the Matrix client + models.

Tests run on the deploy VM (`docker compose run --rm web python manage.py test
matrix`). Lokal kein Runtime-Stack vorhanden. Synapse itself is not contacted —
the HTTP layer (MatrixClient._request) is mocked, so these tests are
hermetic and assert the request shapes we send, not Synapse's behaviour.
"""
from __future__ import annotations

import hashlib
import hmac
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import Person, User
from lists.models import List, ListTemplate
from matrix import service
from matrix.client import MatrixClient, MatrixError
from matrix.models import MatrixAccount, MatrixRoom, MatrixServiceAccount


def _client() -> MatrixClient:
    return MatrixClient(
        base_url="http://synapse:8008",
        server_name="fichtelink.caos.cloud",
        admin_shared_secret="s3cr3t",
        timeout=5,
    )


class GenerateMacTests(TestCase):
    def test_matches_synapse_field_order(self):
        mac = MatrixClient.generate_mac("s3cr3t", "abc", "u-1", "pw", admin=False)
        expected = hmac.new(b"s3cr3t", digestmod=hashlib.sha1)
        expected.update(b"abc\x00u-1\x00pw\x00notadmin")
        self.assertEqual(mac, expected.hexdigest())

    def test_admin_flag_changes_mac(self):
        notadmin = MatrixClient.generate_mac("s", "n", "u", "p", admin=False)
        is_admin = MatrixClient.generate_mac("s", "n", "u", "p", admin=True)
        self.assertNotEqual(notadmin, is_admin)


class UserIdTests(TestCase):
    def test_user_id_form(self):
        self.assertEqual(_client().user_id("u-7f3a9c"), "@u-7f3a9c:fichtelink.caos.cloud")


class RegisterUserTests(TestCase):
    def test_two_step_nonce_then_register(self):
        client = _client()
        calls = []

        def fake_request(method, path, *, json=None, access_token=None):
            calls.append((method, path, json))
            if path == "/_synapse/admin/v1/register" and method == "GET":
                return {"nonce": "NONCE"}
            return {
                "user_id": "@u-1:fichtelink.caos.cloud",
                "access_token": "tok",
                "device_id": "DEV",
                "home_server": "fichtelink.caos.cloud",
            }

        with patch.object(client, "_request", side_effect=fake_request):
            result = client.register_user("u-1", "pw", "Elternteil 5a-12", admin=False)

        self.assertEqual(result["user_id"], "@u-1:fichtelink.caos.cloud")
        self.assertEqual(calls[0], ("GET", "/_synapse/admin/v1/register", None))
        post_method, post_path, post_json = calls[1]
        self.assertEqual((post_method, post_path), ("POST", "/_synapse/admin/v1/register"))
        self.assertEqual(post_json["nonce"], "NONCE")
        self.assertEqual(post_json["username"], "u-1")
        self.assertEqual(post_json["displayname"], "Elternteil 5a-12")
        self.assertFalse(post_json["admin"])
        # mac must match a freshly computed one over the returned nonce.
        self.assertEqual(
            post_json["mac"],
            MatrixClient.generate_mac("s3cr3t", "NONCE", "u-1", "pw", admin=False),
        )


class CreateRoomTests(TestCase):
    def test_invite_only_powerlevels_and_history(self):
        client = _client()
        captured = {}

        def fake_request(method, path, *, json=None, access_token=None):
            captured["method"] = method
            captured["path"] = path
            captured["json"] = json
            captured["token"] = access_token
            return {"room_id": "!abc:fichtelink.caos.cloud"}

        with patch.object(client, "_request", side_effect=fake_request):
            room_id = client.create_room("svc-token", name="Klasse 5a", events_default=0)

        self.assertEqual(room_id, "!abc:fichtelink.caos.cloud")
        self.assertEqual(captured["path"], "/_matrix/client/v3/createRoom")
        self.assertEqual(captured["token"], "svc-token")
        body = captured["json"]
        self.assertEqual(body["power_level_content_override"]["invite"], 100)
        self.assertEqual(body["power_level_content_override"]["events_default"], 0)
        join_rules = [s for s in body["initial_state"] if s["type"] == "m.room.join_rules"][0]
        self.assertEqual(join_rules["content"]["join_rule"], "invite")
        history = [s for s in body["initial_state"] if s["type"] == "m.room.history_visibility"][0]
        self.assertEqual(history["content"]["history_visibility"], "invited")

    def test_broadcast_mode_raises_events_default(self):
        client = _client()
        with patch.object(client, "_request", return_value={"room_id": "!x:y"}) as req:
            client.create_room("t", name="Broadcast", events_default=50)
        body = req.call_args.kwargs["json"]
        self.assertEqual(body["power_level_content_override"]["events_default"], 50)


class SendMessageTests(TestCase):
    def test_put_to_send_endpoint_returns_event_id(self):
        client = _client()
        with patch.object(client, "_request", return_value={"event_id": "$evt"}) as req:
            event_id = client.send_message("t", "!room:fichtelink.caos.cloud", "Bus 5a: 45 Min Verspätung.")
        self.assertEqual(event_id, "$evt")
        method, path = req.call_args.args
        self.assertEqual(method, "PUT")
        self.assertIn("/send/m.room.message/", path)
        self.assertEqual(req.call_args.kwargs["json"], {"msgtype": "m.text", "body": "Bus 5a: 45 Min Verspätung."})


class ModelTests(TestCase):
    def setUp(self):
        self.person = Person.objects.create(given_name="Anna", family_name="Müller", email="anna@example.invalid")
        self.user = User.objects.create_user(person=self.person, username="anna")
        self.template = ListTemplate.objects.create(name="Schulklasse")
        self.list = List.objects.create(title="Klasse 5a", email_alias="5a", template=self.template)

    def test_encrypted_password_roundtrips(self):
        acc = MatrixAccount.objects.create(
            user=self.user,
            matrix_user_id="@u-1:fichtelink.caos.cloud",
            password="server-assigned-pw",
        )
        acc.refresh_from_db()
        self.assertEqual(acc.password, "server-assigned-pw")
        self.assertEqual(acc.onboarding_status, MatrixAccount.Status.CREATED)

    def test_matrix_room_one_per_list(self):
        MatrixRoom.objects.create(list=self.list, room_id="!abc:fichtelink.caos.cloud")
        self.assertEqual(self.list.matrix_room.room_id, "!abc:fichtelink.caos.cloud")

    def test_service_account_singleton_accessor(self):
        self.assertIsNone(MatrixServiceAccount.get())
        svc = MatrixServiceAccount.objects.create(
            matrix_user_id="@fichtelink-service:fichtelink.caos.cloud",
            access_token="long-lived-token",
        )
        self.assertEqual(MatrixServiceAccount.get(), svc)
        svc.refresh_from_db()
        self.assertEqual(svc.access_token, "long-lived-token")


class RequestErrorParsingTests(TestCase):
    def test_error_body_becomes_matrixerror(self):
        import httpx

        client = _client()
        request = httpx.Request("POST", "http://synapse:8008/_synapse/admin/v1/register")
        response = httpx.Response(400, json={"errcode": "M_USER_IN_USE", "error": "User ID already taken."}, request=request)
        with patch("httpx.Client.request", return_value=response):
            with self.assertRaises(MatrixError) as ctx:
                client._request("POST", "/_synapse/admin/v1/register", json={})
        self.assertEqual(ctx.exception.errcode, "M_USER_IN_USE")
        self.assertEqual(ctx.exception.status, 400)


@override_settings(MATRIX_ENABLED=True)
class EnsureAccountTests(TestCase):
    def setUp(self):
        self.person = Person.objects.create(given_name="Anna", family_name="Müller", email="anna@example.invalid")
        self.user = User.objects.create_user(person=self.person, username="anna")

    def _ok(self, **over):
        result = {
            "user_id": "@u-deadbeef:fichtelink.caos.cloud",
            "access_token": "t",
            "device_id": "d",
        }
        result.update(over)
        return result

    def test_provisions_once_and_is_idempotent(self):
        with patch.object(MatrixClient, "register_user", return_value=self._ok()) as reg:
            first = service.ensure_matrix_account(self.user)
            second = service.ensure_matrix_account(self.user)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(MatrixAccount.objects.count(), 1)
        # second call must not touch Synapse again
        self.assertEqual(reg.call_count, 1)
        self.assertEqual(first.matrix_user_id, "@u-deadbeef:fichtelink.caos.cloud")
        self.assertTrue(first.password)  # server-assigned password stored

    def test_localpart_is_lowercase_and_displayname_pseudonymous(self):
        captured = {}

        def reg(self_client, localpart, password, displayname, admin=False):
            captured["localpart"] = localpart
            captured["displayname"] = displayname
            return {"user_id": f"@{localpart}:fichtelink.caos.cloud", "access_token": "t", "device_id": "d"}

        with patch.object(MatrixClient, "register_user", autospec=True, side_effect=reg):
            service.ensure_matrix_account(self.user)
        self.assertTrue(captured["localpart"].startswith("u-"))
        self.assertEqual(captured["localpart"], captured["localpart"].lower())
        self.assertTrue(captured["displayname"].startswith("Elternteil "))
        # no real name leaks into the pseudonym
        self.assertNotIn("Müller", captured["displayname"])
        self.assertNotIn("Anna", captured["displayname"])

    def test_retries_on_localpart_collision(self):
        calls = {"n": 0}

        def reg(self_client, localpart, password, displayname, admin=False):
            calls["n"] += 1
            if calls["n"] == 1:
                raise MatrixError("taken", errcode="M_USER_IN_USE", status=400)
            return {"user_id": f"@{localpart}:fichtelink.caos.cloud", "access_token": "t", "device_id": "d"}

        with patch.object(MatrixClient, "register_user", autospec=True, side_effect=reg):
            account = service.ensure_matrix_account(self.user)
        self.assertEqual(calls["n"], 2)
        self.assertEqual(MatrixAccount.objects.count(), 1)
        self.assertTrue(account.matrix_user_id.startswith("@u-"))

    @override_settings(MATRIX_ENABLED=False)
    def test_disabled_raises(self):
        with self.assertRaises(MatrixError):
            service.ensure_matrix_account(self.user)


class MessengerAccessViewTests(TestCase):
    def setUp(self):
        self.person = Person.objects.create(given_name="Bea", family_name="Schmidt", email="bea@example.invalid")
        self.user = User.objects.create_user(person=self.person, username="bea")

    def test_requires_login(self):
        resp = self.client.get(reverse("matrix:access"))
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/auth/login/", resp["Location"])

    @override_settings(MATRIX_ENABLED=True)
    def test_provisions_and_shows_credentials(self):
        self.client.force_login(self.user)
        ok = {"user_id": "@u-abc123:fichtelink.caos.cloud", "access_token": "t", "device_id": "d"}
        with patch.object(MatrixClient, "register_user", return_value=ok):
            resp = self.client.get(reverse("matrix:access"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "@u-abc123:fichtelink.caos.cloud")
        self.assertTrue(MatrixAccount.objects.filter(user=self.user).exists())

    @override_settings(MATRIX_ENABLED=False)
    def test_disabled_shows_notice_and_provisions_nothing(self):
        self.client.force_login(self.user)
        resp = self.client.get(reverse("matrix:access"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "nicht aktiviert")
        self.assertFalse(MatrixAccount.objects.filter(user=self.user).exists())


@override_settings(MATRIX_ENABLED=True)
class EnsureRoomTests(TestCase):
    def setUp(self):
        self.template = ListTemplate.objects.create(name="Schulklasse")
        self.list = List.objects.create(
            title="Klasse 5a", email_alias="5a", template=self.template, matrix_room_enabled=True
        )
        self.svc = MatrixServiceAccount.objects.create(
            matrix_user_id="@fichtelink-service:fichtelink.caos.cloud",
            access_token="svc-token",
        )

    def test_creates_room_and_is_idempotent(self):
        with patch.object(MatrixClient, "create_room", return_value="!abc:fichtelink.caos.cloud") as cr:
            first = service.ensure_room_for_list(self.list)
            second = service.ensure_room_for_list(self.list)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(MatrixRoom.objects.count(), 1)
        self.assertEqual(cr.call_count, 1)
        self.assertEqual(first.room_id, "!abc:fichtelink.caos.cloud")

    def test_chat_mode_events_default_zero(self):
        with patch.object(MatrixClient, "create_room", return_value="!r:fichtelink.caos.cloud") as cr:
            service.ensure_room_for_list(self.list)
        self.assertEqual(cr.call_args.kwargs["events_default"], 0)
        self.assertEqual(cr.call_args.kwargs["history_visibility"], "invited")
        # acts as the service account
        self.assertEqual(cr.call_args.args[0], "svc-token")

    def test_broadcast_mode_events_default_fifty(self):
        self.list.matrix_broadcast_only = True
        self.list.save()
        with patch.object(MatrixClient, "create_room", return_value="!r:fichtelink.caos.cloud") as cr:
            service.ensure_room_for_list(self.list)
        self.assertEqual(cr.call_args.kwargs["events_default"], 50)

    def test_raises_without_service_account(self):
        self.svc.delete()
        with patch.object(MatrixClient, "create_room") as cr:
            with self.assertRaises(MatrixError):
                service.ensure_room_for_list(self.list)
        cr.assert_not_called()

    def test_raises_when_not_enabled(self):
        other = List.objects.create(title="VHS-Kurs", email_alias="vhs", template=self.template)
        with patch.object(MatrixClient, "create_room") as cr:
            with self.assertRaises(MatrixError):
                service.ensure_room_for_list(other)
        cr.assert_not_called()


class RoomStateClientTests(TestCase):
    def test_get_room_state_hits_state_endpoint(self):
        client = _client()
        sample = [{"type": "m.room.join_rules", "content": {"join_rule": "invite"}}]
        with patch.object(client, "_request", return_value=sample) as req:
            state = client.get_room_state("tok", "!room:fichtelink.caos.cloud")
        self.assertEqual(state, sample)
        method, path = req.call_args.args
        self.assertEqual(method, "GET")
        self.assertTrue(path.endswith("/state"))
        self.assertEqual(req.call_args.kwargs["access_token"], "tok")
