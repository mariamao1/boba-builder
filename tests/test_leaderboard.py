"""Task 21: recent base-drink popularity and its public endpoint."""

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

from app import group_orders, menu, server


ORDER = {
    "person": "Alice",
    "drink": "Taro Slush",
    "size": "Large",
    "sugar": "50%",
    "ice": "Less Ice",
    "toppings": ["Boba"],
    "quantity": 1,
}
ALTERNATE_STORE = "650c9c52d73592bc0e0bd5a7"


class LeaderboardStoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.original_dir = group_orders.SESSION_DIR
        group_orders.SESSION_DIR = Path(self.temporary.name) / "group-orders"
        self.now = dt.datetime(2026, 9, 17, 12, 0, tzinfo=dt.timezone.utc)

    def tearDown(self):
        group_orders.SESSION_DIR = self.original_dir
        self.temporary.cleanup()

    def test_counts_cups_by_base_drink_and_limits_the_result(self):
        room, _token = group_orders.create(now=self.now)
        group_orders.add_order(
            room["id"], {**ORDER, "drink": "Taro Milk Tea", "quantity": 2},
            now=self.now)
        group_orders.add_order(
            room["id"], {**ORDER, "drink": "  TARO   MILK TEA  ", "sugar": "0%"},
            now=self.now)
        for drink in ("Matcha Milk", "Chai Milk", "Mango Slush", "Oreo Slush",
                      "Jasmine Green Tea"):
            group_orders.add_order(
                room["id"], {**ORDER, "drink": drink}, now=self.now)

        board = group_orders.popular_drinks(now=self.now)

        self.assertEqual(board["window_days"], 7)
        self.assertEqual(len(board["entries"]), 5)
        self.assertEqual(board["entries"][0], {
            "drink": "Taro Milk Tea", "drinks": 3, "orders": 2,
        })
        self.assertEqual(board["total_drinks"], 8)
        self.assertEqual(board["total_orders"], 7)
        self.assertEqual(board["group_orders"], 1)

    def test_rolling_window_and_store_filter_are_applied(self):
        first, _token = group_orders.create(now=self.now)
        group_orders.add_order(first["id"], ORDER, now=self.now)
        second, _token = group_orders.create(
            restaurant_id=ALTERNATE_STORE, now=self.now)
        group_orders.add_order(
            second["id"], {**ORDER, "drink": "Matcha Milk", "quantity": 2},
            now=self.now)

        saved = json.loads(group_orders.path_for(first["id"]).read_text())
        saved["orders"][0]["created_at"] = "2026-09-10T11:59:59Z"
        group_orders.path_for(first["id"]).write_text(json.dumps(saved))

        board = group_orders.popular_drinks(
            restaurant_id=ALTERNATE_STORE, now=self.now)

        self.assertEqual(board["restaurant_id"], ALTERNATE_STORE)
        self.assertEqual(board["entries"], [{
            "drink": "Matcha Milk", "drinks": 2, "orders": 1,
        }])
        self.assertEqual(board["since"], "2026-09-10T12:00:00Z")

    def test_empty_and_unreadable_history_are_safe(self):
        group_orders._directory().joinpath("broken.json").write_text("{broken")

        board = group_orders.popular_drinks(now=self.now)

        self.assertEqual(board["entries"], [])
        self.assertEqual(board["total_drinks"], 0)


class LeaderboardHttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.original_dir = group_orders.SESSION_DIR
        group_orders.SESSION_DIR = Path(cls.temporary.name) / "group-orders"
        room, _token = group_orders.create()
        group_orders.add_order(room["id"], ORDER)
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

    def get(self, path):
        try:
            with urllib.request.urlopen(self.base + path, timeout=10) as response:
                return response.status, response.headers, response.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.headers, exc.read()

    def test_endpoint_returns_public_ranking_and_validates_store(self):
        status, headers, body = self.get("/api/leaderboard")
        payload = json.loads(body)

        self.assertEqual(status, 200)
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertEqual(payload["leaderboard"]["entries"][0]["drink"], "Taro Slush")
        self.assertNotIn("person", json.dumps(payload))

        status, _headers, _body = self.get("/api/leaderboard?restaurant_id=missing")
        self.assertEqual(status, 404)

    def test_landing_and_participant_pages_expose_the_two_views(self):
        status, _headers, landing = self.get("/")
        self.assertEqual(status, 200)
        self.assertIn(b'id="popular-drinks"', landing)
        self.assertIn(b'/static/leaderboard.js', landing)

        room, _token = group_orders.create(restaurant_id=menu.TARGET_STORE)
        status, _headers, participant = self.get(f"/group-order/{room['id']}")
        self.assertEqual(status, 200)
        self.assertIn(b'id="popular-picks"', participant)
        self.assertIn(b'/static/leaderboard.js', participant)


if __name__ == "__main__":
    unittest.main()
