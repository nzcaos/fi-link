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

from django.test import TestCase

from accounts.models import Person, User
from lists.models import List, ListTemplate
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
