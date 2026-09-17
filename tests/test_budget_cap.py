"""Task 20: organizer-set per-person budget cap."""

from __future__ import annotations

import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from app import group_orders, runs, server


ORDER = {
    "person": "Alice",
    "drink": "Taro Slush",
    "size": "Large",
    "sugar": "50%",
    "ice": "Less Ice",
    "toppings": ["Boba"],
    "quantity": 1,
}


class BudgetCapTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.original_dir = group_orders.SESSION_DIR
        self.original_run_dir = runs.RUN_DIR
        group_orders.SESSION_DIR = Path(self.temporary.name) / "group-orders"
        runs.RUN_DIR = Path(self.temporary.name) / "runs"

    def tearDown(self):
        group_orders.SESSION_DIR = self.original_dir
        runs.RUN_DIR = self.original_run_dir
        self.temporary.cleanup()

    def test_cap_is_optional_cent_rounded_and_persisted(self):
        room, _token = group_orders.create(budget_cap=10.005)
        self.assertEqual(room["budget_cap"], 10.01)
        saved = json.loads(group_orders.path_for(room["id"]).read_text())
        self.assertEqual(saved["budget_cap"], 10.01)

        uncapped, _token = group_orders.create()
        self.assertIsNone(uncapped["budget_cap"])

    def test_invalid_caps_are_rejected(self):
        for value in (0, -1, True, "ten", float("inf"),
                      group_orders.MAX_BUDGET_CAP + 1):
            with self.subTest(value=value), self.assertRaises(group_orders.GroupOrderError):
                group_orders.create(budget_cap=value)

    def test_cap_applies_to_a_persons_combined_total(self):
        room, _token = group_orders.create(budget_cap=10)
        group_orders.add_order(room["id"], ORDER)
        group_orders.add_order(room["id"], {**ORDER, "person": "Bob"})

        with self.assertRaises(group_orders.BudgetExceeded) as raised:
            group_orders.add_order(
                room["id"], {**ORDER, "person": "ALICE", "drink": "Matcha Milk"})

        self.assertEqual(raised.exception.code, "budget_exceeded")
        self.assertEqual(raised.exception.status, 409)
        self.assertEqual(group_orders.get(room["id"])["summary"]["orders"], 2)

    def test_exact_cap_is_allowed_and_unpriced_lines_are_exempt(self):
        room, token = group_orders.create()
        group_orders.add_order(room["id"], ORDER)
        exact = group_orders.get(room["id"])["orders"][0]["estimated_total"]
        group_orders.set_budget_cap(room["id"], exact, token)

        group_orders.add_order(room["id"], {**ORDER, "person": "Bob"})
        _order, _edit_token, aggregate = group_orders.add_order(
            room["id"], {**ORDER, "drink": "Not A Captured Drink"})
        self.assertIsNone(aggregate["orders"][-1]["estimated_total"])

    def test_lowered_cap_grandfathers_but_does_not_allow_more_spend(self):
        room, organizer_token = group_orders.create()
        order, edit_token, _aggregate = group_orders.add_order(
            room["id"], {**ORDER, "quantity": 2})
        lowered = group_orders.set_budget_cap(room["id"], 5, organizer_token)
        self.assertEqual(len(lowered["orders"]), 1)

        updated, _aggregate = group_orders.update_order(
            room["id"], order["id"], {"notes": "extra napkins"},
            order_token=edit_token)
        self.assertEqual(updated["notes"], "extra napkins")
        with self.assertRaises(group_orders.BudgetExceeded):
            group_orders.update_order(
                room["id"], order["id"], {"quantity": 3},
                order_token=edit_token)

        # Organizer moderation remains possible after the cap changes.
        updated, _aggregate = group_orders.update_order(
            room["id"], order["id"], {"quantity": 3},
            organizer_token=organizer_token)
        self.assertEqual(updated["quantity"], 3)

    def test_only_organizer_can_change_a_nonclosed_room_cap(self):
        room, token = group_orders.create(budget_cap=10)
        with self.assertRaises(group_orders.Forbidden):
            group_orders.set_budget_cap(room["id"], 12, "wrong")
        self.assertIsNone(group_orders.set_budget_cap(
            room["id"], None, token)["budget_cap"])

        group_orders.set_status(room["id"], "closed", token)
        with self.assertRaises(group_orders.RoomNotOpen):
            group_orders.set_budget_cap(room["id"], 12, token)


class BudgetCapHttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.original_dir = group_orders.SESSION_DIR
        group_orders.SESSION_DIR = Path(cls.temporary.name) / "group-orders"
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

    def request(self, method, path, payload=None, headers=None):
        body = None if payload is None else json.dumps(payload).encode()
        request = urllib.request.Request(
            self.base + path, data=body, method=method,
            headers={**(headers or {}), **({"Content-Type": "application/json"}
                                         if payload is not None else {})})
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def test_budget_is_enforced_and_can_be_changed_over_http(self):
        status, created = self.request("POST", "/api/group-orders", {
            "expires_in_hours": 2, "budget_cap": 10,
        })
        self.assertEqual(status, 201)
        room_id = created["session_id"]
        token = created["organizer_token"]
        self.assertEqual(self.request(
            "POST", f"/api/group-orders/{room_id}/orders", ORDER)[0], 201)

        status, rejected = self.request(
            "POST", f"/api/group-orders/{room_id}/orders",
            {**ORDER, "drink": "Matcha Milk"})
        self.assertEqual((status, rejected["code"]), (409, "budget_exceeded"))

        status, changed = self.request(
            "PATCH", f"/api/group-orders/{room_id}/budget", {"budget_cap": 20},
            {"X-Organizer-Token": token})
        self.assertEqual(status, 200)
        self.assertEqual(changed["session"]["budget_cap"], 20.0)

    def test_pages_expose_budget_controls(self):
        status, created = self.request("POST", "/api/group-orders", {
            "expires_in_hours": 2, "budget_cap": 10,
        })
        self.assertEqual(status, 201)
        room_id = created["session_id"]
        pages = {
            "/": ('id="group-budget"',),
            f"/group-order/{room_id}": ('id="budget-card"', 'id="budget-hint"'),
            f"/group-order/{room_id}/organizer": (
                'id="budget-manager"', 'id="budget-form"', 'id="budget-input"'),
        }
        for path, markers in pages.items():
            with urllib.request.urlopen(self.base + path, timeout=10) as response:
                page = response.read().decode()
            for marker in markers:
                self.assertIn(marker, page)


if __name__ == "__main__":
    unittest.main()
