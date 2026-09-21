"""Task 19: shared order status board, from submission through pickup."""

from __future__ import annotations

import datetime as dt
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


class FulfillmentStoreTests(unittest.TestCase):
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

    def test_new_rooms_start_collecting(self):
        room, _token = group_orders.create(title="Friday tea")

        self.assertEqual(room["fulfillment"], {
            "status": "collecting",
            "label": "Collecting drinks",
            "hint": group_orders.FULFILLMENT_HINTS["collecting"],
            "note": "",
            "updated_at": None,
            "history": [],
        })

    def test_organizer_advances_the_board_with_note_and_history(self):
        now = dt.datetime(2026, 9, 16, 12, 0, tzinfo=dt.timezone.utc)
        room, organizer_token = group_orders.create(now=now)

        ordered = group_orders.set_fulfillment(
            room["id"], "ordered", organizer_token,
            note="Placed at 5:10pm, pickup ~6", now=now)
        board = ordered["fulfillment"]
        self.assertEqual(board["status"], "ordered")
        self.assertEqual(board["label"], "Ordered — with the store")
        self.assertEqual(board["note"], "Placed at 5:10pm, pickup ~6")
        self.assertEqual(board["updated_at"], "2026-09-16T12:00:00Z")
        self.assertEqual(board["history"], [{
            "status": "ordered", "at": "2026-09-16T12:00:00Z",
            "note": "Placed at 5:10pm, pickup ~6",
        }])

        later = now + dt.timedelta(minutes=20)
        ready = group_orders.set_fulfillment(
            room["id"], "ready", organizer_token, now=later)
        self.assertEqual(ready["fulfillment"]["status"], "ready")
        # A new update replaces the visible note but keeps the trail.
        self.assertEqual(ready["fulfillment"]["note"], "")
        self.assertEqual(
            [entry["status"] for entry in ready["fulfillment"]["history"]],
            ["ordered", "ready"])

    def test_full_lifecycle_reaches_distributed_and_can_step_back(self):
        room, organizer_token = group_orders.create()
        for status in ("ordered", "ready", "picked_up", "distributed"):
            room = group_orders.set_fulfillment(room["id"], status, organizer_token)
        self.assertEqual(room["fulfillment"]["status"], "distributed")
        self.assertEqual(len(room["fulfillment"]["history"]), 4)

        # A mistaken tap is correctable — the board is not a one-way ratchet.
        corrected = group_orders.set_fulfillment(
            room["id"], "ready", organizer_token, note="wrong tray")
        self.assertEqual(corrected["fulfillment"]["status"], "ready")
        self.assertEqual(len(corrected["fulfillment"]["history"]), 5)

    def test_board_rejects_bad_statuses_tokens_and_long_notes(self):
        room, organizer_token = group_orders.create()

        with self.assertRaises(group_orders.GroupOrderError):
            group_orders.set_fulfillment(room["id"], "teleported", organizer_token)
        with self.assertRaises(group_orders.GroupOrderError):
            group_orders.set_fulfillment(room["id"], None, organizer_token)
        with self.assertRaises(group_orders.GroupOrderError):
            group_orders.set_fulfillment(
                room["id"], "ready", organizer_token, note="x" * 281)

        for bad_token in (None, "", "wrong"):
            with self.assertRaises(group_orders.Forbidden):
                group_orders.set_fulfillment(room["id"], "ready", bad_token)

        # Failed updates leave the board untouched.
        self.assertEqual(
            group_orders.get(room["id"])["fulfillment"]["status"], "collecting")

    def test_rooms_from_before_the_board_default_to_collecting(self):
        room, organizer_token = group_orders.create()
        raw = json.loads(group_orders.path_for(room["id"]).read_text(encoding="utf-8"))
        for key in ("fulfillment_status", "fulfillment_note",
                    "fulfillment_updated_at", "fulfillment_history"):
            raw.pop(key, None)
        group_orders.path_for(room["id"]).write_text(json.dumps(raw))

        board = group_orders.get(room["id"])["fulfillment"]
        self.assertEqual(board["status"], "collecting")
        self.assertEqual(board["history"], [])

        # And an old room can still join the lifecycle.
        updated = group_orders.set_fulfillment(
            room["id"], "distributed", organizer_token)
        self.assertEqual(updated["fulfillment"]["status"], "distributed")

    def test_board_survives_collection_lifecycle_and_finalize(self):
        room, organizer_token = group_orders.create()
        group_orders.add_order(room["id"], ORDER)
        group_orders.set_fulfillment(room["id"], "ordered", organizer_token)

        group_orders.set_status(room["id"], "locked", organizer_token)
        group_orders.set_status(room["id"], "closed", organizer_token)
        finalized, _run_id = group_orders.finalize(room["id"], organizer_token)

        self.assertEqual(finalized["status"], "closed")
        self.assertEqual(finalized["fulfillment"]["status"], "ordered")

        dashboard = group_orders.get_for_organizer(room["id"], organizer_token)
        self.assertEqual(dashboard["fulfillment"]["status"], "ordered")

    def test_board_updates_never_leak_secrets(self):
        room, organizer_token = group_orders.create()
        updated = group_orders.set_fulfillment(
            room["id"], "ready", organizer_token, note="counter B")
        self.assertNotIn("token", json.dumps(updated["fulfillment"]))
        self.assertNotIn(organizer_token, json.dumps(updated))


class FulfillmentHttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.original_dir = group_orders.SESSION_DIR
        cls.original_run_dir = runs.RUN_DIR
        group_orders.SESSION_DIR = Path(cls.temporary.name) / "group-orders"
        runs.RUN_DIR = Path(cls.temporary.name) / "runs"
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.base = f"http://127.0.0.1:{cls.httpd.server_address[1]}"
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        group_orders.SESSION_DIR = cls.original_dir
        runs.RUN_DIR = cls.original_run_dir
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

    def test_status_board_over_http(self):
        status, created = self.request("POST", "/api/group-orders", {
            "title": "Friday tea", "organizer_name": "Mariam",
            "expires_in_hours": 2,
        })
        self.assertEqual(status, 201)
        room_id = created["session_id"]
        organizer_token = created["organizer_token"]
        self.assertEqual(created["session"]["fulfillment"]["status"], "collecting")

        # Participants see the board on the public read.
        status, public = self.request("GET", f"/api/group-orders/{room_id}")
        self.assertEqual(status, 200)
        self.assertEqual(public["session"]["fulfillment"]["status"], "collecting")

        # Only the organizer token moves it.
        status, denied = self.request(
            "POST", f"/api/group-orders/{room_id}/fulfillment",
            {"fulfillment_status": "ordered"})
        self.assertEqual(status, 403)
        self.assertEqual(denied["code"], "forbidden")

        status, invalid = self.request(
            "POST", f"/api/group-orders/{room_id}/fulfillment",
            {"fulfillment_status": "teleported"},
            {"X-Organizer-Token": organizer_token})
        self.assertEqual(status, 400)
        self.assertEqual(invalid["code"], "invalid_request")

        status, ordered = self.request(
            "POST", f"/api/group-orders/{room_id}/fulfillment",
            {"fulfillment_status": "ordered", "note": "Pickup ~6pm"},
            {"X-Organizer-Token": organizer_token})
        self.assertEqual(status, 200)
        self.assertEqual(ordered["session"]["fulfillment"]["status"], "ordered")
        self.assertEqual(ordered["session"]["fulfillment"]["note"], "Pickup ~6pm")

        # The participant view now answers "is it here yet?" without asking.
        status, public = self.request("GET", f"/api/group-orders/{room_id}")
        self.assertEqual(public["session"]["fulfillment"]["status"], "ordered")
        self.assertEqual(public["session"]["fulfillment"]["note"], "Pickup ~6pm")
        self.assertNotIn("token", json.dumps(public))

        status, private = self.request(
            "GET", f"/api/group-orders/{room_id}/organizer",
            headers={"X-Organizer-Token": organizer_token})
        self.assertEqual(status, 200)
        self.assertEqual(private["session"]["fulfillment"]["status"], "ordered")

    def test_pages_surface_the_board(self):
        status, created = self.request("POST", "/api/group-orders", {
            "title": "Friday tea", "expires_in_hours": 2,
        })
        self.assertEqual(status, 201)
        room_id = created["session_id"]

        with urllib.request.urlopen(
                self.base + f"/group-order/{room_id}", timeout=10) as response:
            participant_page = response.read().decode("utf-8")
        self.assertIn('id="status-board"', participant_page)
        self.assertIn('id="status-steps"', participant_page)
        self.assertIn('id="status-note"', participant_page)
        self.assertIn("/static/group-order.js", participant_page)

        with urllib.request.urlopen(
                self.base + f"/group-order/{room_id}/organizer", timeout=10) as response:
            organizer_page = response.read().decode("utf-8")
        self.assertIn('id="fulfillment-card"', organizer_page)
        self.assertIn('id="fulfillment-buttons"', organizer_page)
        self.assertIn('id="fulfillment-note"', organizer_page)
        self.assertIn("/static/group-order-organizer.js", organizer_page)


if __name__ == "__main__":
    unittest.main()
