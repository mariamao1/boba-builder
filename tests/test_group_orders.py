"""Task 8: persistent shared group-order rooms and their HTTP API."""

from __future__ import annotations

import datetime as dt
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from http.server import ThreadingHTTPServer
from pathlib import Path

from app import group_orders, server


ORDER = {
    "person": "Alice",
    "drink": "Taro Slush",
    "size": "Large",
    "sugar": "50%",
    "ice": "Less Ice",
    "toppings": ["Boba"],
    "quantity": 1,
}


class GroupOrderStoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.original_dir = group_orders.SESSION_DIR
        group_orders.SESSION_DIR = Path(self.temporary.name)

    def tearDown(self):
        group_orders.SESSION_DIR = self.original_dir
        self.temporary.cleanup()

    def test_create_persists_a_public_room_without_persisting_the_secret(self):
        now = dt.datetime(2026, 8, 31, 12, 0, tzinfo=dt.timezone.utc)

        room, organizer_token = group_orders.create(
            title="Studio boba", organizer_name="Mariam", now=now)

        self.assertEqual(room["status"], "open")
        self.assertTrue(room["accepting_orders"])
        self.assertEqual(room["expires_at"], "2026-09-01T12:00:00Z")
        self.assertGreaterEqual(len(room["id"]), 20)
        self.assertNotIn("token", json.dumps(room))
        saved = group_orders.path_for(room["id"]).read_text(encoding="utf-8")
        self.assertNotIn(organizer_token, saved)
        self.assertIn("organizer_token_hash", saved)
        self.assertEqual(group_orders.get(room["id"], now=now), room)

    def test_orders_are_aggregated_by_person_and_tokens_stay_private(self):
        room, _organizer_token = group_orders.create()
        first, first_token, aggregate = group_orders.add_order(
            room["id"], {**ORDER, "quantity": 2})
        second, _second_token, aggregate = group_orders.add_order(
            room["id"], {**ORDER, "person": "alice", "drink": "Matcha Milk"})

        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual(aggregate["summary"], {
            "orders": 2,
            "drinks": 3,
            "people": 1,
            "by_person": [{"person": "Alice", "orders": 2, "drinks": 3}],
        })
        self.assertNotIn(first_token, json.dumps(aggregate))
        self.assertEqual(aggregate["orders"][0]["toppings"], ["Boba"])

    def test_contributor_can_edit_and_organizer_can_remove_an_order(self):
        room, organizer_token = group_orders.create()
        order, edit_token, _room = group_orders.add_order(room["id"], ORDER)

        with self.assertRaises(group_orders.Forbidden):
            group_orders.update_order(
                room["id"], order["id"], {"quantity": 2}, order_token="wrong")

        updated, aggregate = group_orders.update_order(
            room["id"], order["id"], {"quantity": 2, "notes": "no straw"},
            order_token=edit_token,
        )
        self.assertEqual(updated["quantity"], 2)
        self.assertEqual(updated["notes"], "no straw")
        self.assertEqual(aggregate["summary"]["drinks"], 2)

        aggregate = group_orders.delete_order(
            room["id"], order["id"], organizer_token=organizer_token)
        self.assertEqual(aggregate["orders"], [])

    def test_lock_reopen_close_and_expiry_are_enforced(self):
        now = dt.datetime(2026, 8, 31, 12, 0, tzinfo=dt.timezone.utc)
        room, organizer_token = group_orders.create(expires_in_hours=1, now=now)

        with self.assertRaises(group_orders.Forbidden):
            group_orders.set_status(room["id"], "locked", "wrong", now=now)

        locked = group_orders.set_status(room["id"], "locked", organizer_token, now=now)
        self.assertFalse(locked["accepting_orders"])
        with self.assertRaises(group_orders.RoomNotOpen):
            group_orders.add_order(room["id"], ORDER, now=now)

        reopened = group_orders.set_status(room["id"], "open", organizer_token, now=now)
        self.assertTrue(reopened["accepting_orders"])
        group_orders.add_order(room["id"], ORDER, now=now)
        closed = group_orders.set_status(room["id"], "closed", organizer_token, now=now)
        self.assertEqual(closed["status"], "closed")
        with self.assertRaises(group_orders.RoomNotOpen):
            group_orders.set_status(room["id"], "open", organizer_token, now=now)

        expiring, _token = group_orders.create(expires_in_hours=1, now=now)
        later = now + dt.timedelta(hours=2)
        self.assertEqual(group_orders.get(expiring["id"], now=later)["status"], "expired")
        with self.assertRaises(group_orders.RoomExpired):
            group_orders.add_order(expiring["id"], ORDER, now=later)

    def test_validation_caps_the_public_write_surface(self):
        room, _organizer_token = group_orders.create()
        for bad in (
            {"person": "", "drink": "Taro Slush"},
            {"person": "Alice", "drink": ""},
            {**ORDER, "quantity": 0},
            {**ORDER, "quantity": 1.5},
            {**ORDER, "notes": "x" * 501},
        ):
            with self.assertRaises(group_orders.GroupOrderError):
                group_orders.add_order(room["id"], bad)

        with self.assertRaises(group_orders.GroupOrderError):
            group_orders.create(expires_in_hours=group_orders.MAX_TTL_HOURS + 1)

    def test_concurrent_additions_do_not_overwrite_each_other(self):
        room, _organizer_token = group_orders.create()

        def add(number):
            group_orders.add_order(
                room["id"], {**ORDER, "person": f"Person {number}"})

        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(add, range(24)))

        aggregate = group_orders.get(room["id"])
        self.assertEqual(aggregate["summary"]["orders"], 24)
        self.assertEqual(aggregate["summary"]["people"], 24)

    def test_pruning_never_removes_a_room_that_is_still_live(self):
        now = dt.datetime(2026, 8, 31, 12, 0, tzinfo=dt.timezone.utc)
        live, _live_token = group_orders.create(now=now)
        closed, closed_token = group_orders.create(now=now)
        group_orders.set_status(closed["id"], "closed", closed_token, now=now)

        group_orders.prune(now=now, keep=0)

        self.assertEqual(group_orders.get(live["id"], now=now)["status"], "open")
        with self.assertRaises(group_orders.RoomNotFound):
            group_orders.get(closed["id"], now=now)


class GroupOrderHttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.original_dir = group_orders.SESSION_DIR
        group_orders.SESSION_DIR = Path(cls.temporary.name)
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.base = f"http://127.0.0.1:{cls.httpd.server_address[1]}"
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        group_orders.SESSION_DIR = cls.original_dir
        cls.temporary.cleanup()

    def request(self, method: str, path: str, payload=None, headers=None):
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        request_headers = dict(headers or {})
        if payload is not None:
            request_headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            self.base + path, data=data, method=method, headers=request_headers)
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def test_room_lifecycle_over_http(self):
        status, created = self.request("POST", "/api/group-orders", {
            "title": "Monday tea", "organizer_name": "Mariam", "expires_in_hours": 2,
        })
        self.assertEqual(status, 201)
        room_id = created["session_id"]
        organizer_token = created["organizer_token"]
        self.assertEqual(created["share_url"], f"/group-order/{room_id}")

        status, added = self.request(
            "POST", f"/api/group-orders/{room_id}/orders", ORDER)
        self.assertEqual(status, 201)
        order_id = added["order"]["id"]
        order_token = added["order_token"]

        status, denied = self.request(
            "PATCH", f"/api/group-orders/{room_id}/orders/{order_id}",
            {"quantity": 2}, {"X-Order-Token": "wrong"})
        self.assertEqual(status, 403)
        self.assertEqual(denied["code"], "forbidden")

        status, updated = self.request(
            "PATCH", f"/api/group-orders/{room_id}/orders/{order_id}",
            {"quantity": 2}, {"X-Order-Token": order_token})
        self.assertEqual(status, 200)
        self.assertEqual(updated["session"]["summary"]["drinks"], 2)

        status, public = self.request("GET", created["share_url"])
        self.assertEqual(status, 200)
        self.assertEqual(public["session"]["orders"][0]["person"], "Alice")
        self.assertNotIn("token", json.dumps(public))

        status, locked = self.request(
            "POST", f"/api/group-orders/{room_id}/lock", {},
            {"X-Organizer-Token": organizer_token})
        self.assertEqual(status, 200)
        self.assertEqual(locked["session"]["status"], "locked")

        status, rejected = self.request(
            "POST", f"/api/group-orders/{room_id}/orders", ORDER)
        self.assertEqual(status, 409)
        self.assertEqual(rejected["code"], "room_not_open")

        status, closed = self.request(
            "POST", f"/api/group-orders/{room_id}/close", {},
            {"X-Organizer-Token": organizer_token})
        self.assertEqual(status, 200)
        self.assertEqual(closed["session"]["status"], "closed")

    def test_bad_room_payloads_return_structured_errors(self):
        status, invalid = self.request(
            "POST", "/api/group-orders", {"expires_in_hours": 999})
        self.assertEqual(status, 400)
        self.assertEqual(invalid["code"], "invalid_request")

        status, missing = self.request(
            "GET", "/api/group-orders/aaaaaaaaaaaaaaaaaaaaaaaa")
        self.assertEqual(status, 404)
        self.assertEqual(missing["code"], "room_not_found")


if __name__ == "__main__":
    unittest.main()
