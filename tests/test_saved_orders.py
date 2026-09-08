"""Saved finished-order persistence and HTTP routes."""

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

from app import importer, pipeline, runs, saved_orders, server


OWNER = "browser_owner_token_abcdefghijklmnopqrstuvwxyz"
OTHER_OWNER = "another_browser_token_abcdefghijklmnopqrstuvwxyz"


def finished_run() -> dict:
    parsed = importer.import_bytes(
        b"Name,Drink,Size,Sugar,Toppings,Qty,Notes\n"
        b"Alice,Taro Slush,Large,50%,Boba,2,no straw\n",
        "team-order.csv",
    )
    run = pipeline.enrich(parsed.as_dict())
    run["run_id"] = "1234abcd"
    run["cart"] = {
        "status": "ready",
        "review_ready": True,
        "store": {
            "restaurant_id": run["match"]["restaurant_id"],
            "name": run["match"]["store"],
        },
        "counts": {
            "requested_drinks": 2,
            "added_drinks": 2,
            "not_added_drinks": 0,
        },
        "added": [{
            "row_number": 2,
            "person": "Alice",
            "drink": "Taro Slush",
            "quantity": 2,
            "options": [
                {"axis": "size", "name": "Large .7", "quantity": 1},
                {"axis": "sugar", "name": "Half S 50%", "quantity": 1},
                {"axis": "toppings", "name": "Boba", "quantity": 1},
            ],
            "notes": "no straw",
            "actual_total": 13.30,
        }],
        "failed": [],
        "skipped": [],
        "totals": {"subtotal": 13.30, "tax": 1.18, "fees": {}, "total": 14.48},
    }
    run["manifest"] = run["cart"]["added"]
    run["handoff_url"] = "https://example.test/old-cart"
    return run


class SavedOrderStoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.original_saved_dir = saved_orders.SAVED_ORDER_DIR
        self.original_run_dir = runs.RUN_DIR
        saved_orders.SAVED_ORDER_DIR = Path(self.temporary.name) / "saved-orders"
        runs.RUN_DIR = Path(self.temporary.name) / "runs"

    def tearDown(self):
        saved_orders.SAVED_ORDER_DIR = self.original_saved_dir
        runs.RUN_DIR = self.original_run_dir
        self.temporary.cleanup()

    def test_finished_order_is_saved_with_full_manifest_and_browser_scope(self):
        now = dt.datetime(2026, 9, 8, 15, 0, tzinfo=dt.timezone.utc)
        run = finished_run()
        saved = saved_orders.save_finished(
            run, OWNER, "Tuesday team tea", "2026-09-07", now=now)

        self.assertEqual(saved["label"], "Tuesday team tea")
        self.assertEqual(saved["order_date"], "2026-09-07")
        self.assertEqual(saved["source"]["type"], "sheet")
        self.assertEqual(saved["source"]["restaurant_id"], run["match"]["restaurant_id"])
        self.assertEqual(saved["store"]["restaurant_id"], run["match"]["restaurant_id"])
        self.assertEqual(saved["counts"]["placed_drinks"], 2)
        self.assertEqual(saved["totals"]["total"], 14.48)
        self.assertEqual(saved["items"][0]["person"], "Alice")
        self.assertEqual(saved["items"][0]["options"][2]["name"], "Boba")
        self.assertEqual(saved_orders.list_orders(OWNER)[0]["id"], saved["id"])
        self.assertNotIn("items", saved_orders.list_orders(OWNER)[0])
        self.assertEqual(saved_orders.list_orders(OTHER_OWNER), [])
        with self.assertRaises(saved_orders.SavedOrderNotFound):
            saved_orders.get(saved["id"], OTHER_OWNER)

        disk = saved_orders._path(saved["id"]).read_text(encoding="utf-8")
        self.assertNotIn(OWNER, disk)
        self.assertNotIn("old-cart", disk)

    def test_group_link_source_is_preserved_for_history_and_repeat(self):
        run = finished_run()
        run["source"] = {
            "kind": "group_order",
            "session_id": "abcdefghijklmnopqrstuvwx",
            "title": "Launch tea",
            "restaurant_id": run["match"]["restaurant_id"],
            "store": run["match"]["store"],
        }
        saved = saved_orders.save_finished(run, OWNER, "Launch tea", "2026-09-08")
        self.assertEqual(saved["source"]["type"], "group_link")
        self.assertEqual(saved["source"]["session_id"], "abcdefghijklmnopqrstuvwx")
        repeated = runs.load(saved_orders.repeat(saved["id"], OWNER))
        self.assertEqual(repeated["source"]["original_kind"], "group_order")

    def test_tip_and_final_receipt_total_are_saved_in_the_person_split(self):
        run = finished_run()
        second = dict(run["cart"]["added"][0], row_number=3, person="Bob",
                      quantity=1, actual_total=6.70)
        run["cart"]["added"].append(second)
        run["manifest"] = run["cart"]["added"]
        run["cart"]["totals"] = {
            "subtotal": 20.0, "tax": 1.78, "fees": {}, "total": 21.78,
        }
        saved = saved_orders.save_finished(
            run, OWNER, "Receipt", "2026-09-08", tip=3, total_paid=24.78)

        self.assertEqual(saved["totals"]["tip"], 3.0)
        self.assertEqual(saved["totals"]["total"], 24.78)
        self.assertEqual(saved["costs"]["total"], 24.78)
        self.assertEqual(sum(item["total"] for item in saved["costs"]["by_person"]), 24.78)

    def test_same_run_updates_one_saved_order(self):
        first = saved_orders.save_finished(finished_run(), OWNER, "First", "2026-09-07")
        second = saved_orders.save_finished(finished_run(), OWNER, "Renamed", "2026-09-08")
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(len(saved_orders.list_orders(OWNER)), 1)
        self.assertEqual(saved_orders.get(first["id"], OWNER)["label"], "Renamed")

    def test_unfinished_run_and_bad_fields_are_rejected(self):
        run = finished_run()
        run.pop("cart")
        with self.assertRaises(saved_orders.OrderNotFinished):
            saved_orders.save_finished(run, OWNER, "Not done")
        with self.assertRaises(saved_orders.SavedOrderUnauthorized):
            saved_orders.list_orders("short")
        with self.assertRaises(saved_orders.SavedOrderError):
            saved_orders.save_finished(finished_run(), OWNER, " ")
        with self.assertRaises(saved_orders.SavedOrderError):
            saved_orders.save_finished(finished_run(), OWNER, "Tea", "not-a-date")

    def test_repeat_is_a_fresh_editable_run_without_old_cart(self):
        saved = saved_orders.save_finished(finished_run(), OWNER, "Again", "2026-09-08")
        run_id = saved_orders.repeat(saved["id"], OWNER)
        repeated = runs.load(run_id)

        self.assertNotEqual(run_id, "1234abcd")
        self.assertEqual(repeated["source"]["kind"], "saved_order")
        self.assertEqual(repeated["source"]["saved_order_id"], saved["id"])
        self.assertNotIn("cart", repeated)
        self.assertNotIn("handoff_url", repeated)
        enriched = pipeline.enrich(repeated)
        self.assertEqual(enriched["rows"][0]["match"]["status"], "ready")

    def test_export_is_a_sheet_friendly_csv_with_status(self):
        run = finished_run()
        run["rows"][0]["notes"] = "=HYPERLINK(\"https://example.test\")"
        saved = saved_orders.save_finished(run, OWNER, "Export", "2026-09-08")
        exported = saved_orders.export_csv(saved["id"], OWNER).decode("utf-8-sig")
        self.assertIn("Name,Drink,Size,Sugar,Ice,Toppings,Milk,Qty,Notes,Cart status", exported)
        self.assertIn("Alice,Taro Slush,Large,50%,,Boba,,2", exported)
        self.assertIn("'=HYPERLINK", exported)
        self.assertIn(",Placed", exported)


class SavedOrderHttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.original_saved_dir = saved_orders.SAVED_ORDER_DIR
        cls.original_run_dir = runs.RUN_DIR
        saved_orders.SAVED_ORDER_DIR = Path(cls.temporary.name) / "saved-orders"
        runs.RUN_DIR = Path(cls.temporary.name) / "runs"
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.base = f"http://127.0.0.1:{cls.httpd.server_address[1]}"
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        saved_orders.SAVED_ORDER_DIR = cls.original_saved_dir
        runs.RUN_DIR = cls.original_run_dir
        cls.temporary.cleanup()

    def request(self, method: str, path: str, payload=None, token=OWNER):
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        headers = {"X-Saved-Orders-Token": token} if token else {}
        if payload is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(self.base + path, data=data, method=method,
                                         headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, response.headers, response.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.headers, exc.read()

    def test_save_list_detail_export_and_repeat_routes(self):
        run = finished_run()
        run_id = runs.save(run, "1234abcd")
        status, _headers, raw = self.request("POST", "/api/saved-orders", {
            "run_id": run_id, "label": "Team tea", "order_date": "2026-09-08",
        })
        created = json.loads(raw)
        self.assertEqual(status, 201)
        saved_id = created["order"]["id"]

        status, _headers, raw = self.request("GET", "/api/saved-orders")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(raw)["orders"][0]["label"], "Team tea")

        status, _headers, raw = self.request("GET", f"/api/saved-orders/{saved_id}")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(raw)["order"]["items"][0]["drink"], "Taro Slush")

        status, headers, raw = self.request("GET", f"/api/saved-orders/{saved_id}/export")
        self.assertEqual(status, 200)
        self.assertIn("text/csv", headers.get("Content-Type"))
        self.assertIn(b"Taro Slush", raw)

        status, _headers, raw = self.request(
            "POST", f"/api/saved-orders/{saved_id}/repeat")
        repeated = json.loads(raw)
        self.assertEqual(status, 201)
        self.assertTrue(runs.load(repeated["run_id"]))

        status, _headers, raw = self.request(
            "GET", f"/api/saved-orders/{saved_id}", token=OTHER_OWNER)
        self.assertEqual(status, 404)
        self.assertFalse(json.loads(raw)["ok"])

    def test_saved_orders_pages_are_served(self):
        for path in ("/saved-orders", "/saved-orders/abcdefghijklmnopqrst"):
            with urllib.request.urlopen(self.base + path, timeout=10) as response:
                page = response.read().decode("utf-8")
            self.assertIn("Saved Orders", page)
            self.assertIn("/static/saved-orders.js", page)


if __name__ == "__main__":
    unittest.main()
