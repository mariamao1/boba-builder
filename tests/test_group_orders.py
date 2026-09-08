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
from unittest import mock

from app import group_orders, importer, menu, pipeline, runs, server


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


class GroupOrderStoreTests(unittest.TestCase):
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
        self.assertEqual(aggregate["costs"]["source"], "menu_estimate")
        self.assertEqual(aggregate["costs"]["priced_drinks"], 3)
        self.assertEqual(
            sum(person["total"] for person in aggregate["costs"]["by_person"]),
            aggregate["costs"]["total"],
        )
        self.assertIsNotNone(aggregate["orders"][0]["estimated_total"])

    def test_create_pins_an_available_store_to_the_room(self):
        room, _organizer_token = group_orders.create(restaurant_id=ALTERNATE_STORE)

        self.assertEqual(room["restaurant_id"], ALTERNATE_STORE)
        self.assertIn("Washington Ave", room["store_name"])
        saved = json.loads(group_orders.path_for(room["id"]).read_text(encoding="utf-8"))
        self.assertEqual(saved["restaurant_id"], ALTERNATE_STORE)

        with self.assertRaises(group_orders.GroupOrderError):
            group_orders.create(restaurant_id="not-a-captured-store")

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

    def test_organizer_can_moderate_while_participants_are_locked_out(self):
        room, organizer_token = group_orders.create()
        order, edit_token, _room = group_orders.add_order(room["id"], ORDER)
        group_orders.set_status(room["id"], "locked", organizer_token)

        with self.assertRaises(group_orders.RoomNotOpen):
            group_orders.delete_order(room["id"], order["id"], order_token=edit_token)

        aggregate = group_orders.delete_order(
            room["id"], order["id"], organizer_token=organizer_token)
        self.assertEqual(aggregate["status"], "locked")
        self.assertEqual(aggregate["orders"], [])

    def test_finalize_creates_one_normal_pipeline_run_and_closes_the_room(self):
        room, organizer_token = group_orders.create(title="Launch tea")
        group_orders.add_order(room["id"], {**ORDER, "quantity": 2})
        group_orders.add_order(room["id"], {
            **ORDER, "person": "Bob", "drink": "Matcha Milk", "toppings": [],
        })

        finalized, run_id = group_orders.finalize(room["id"], organizer_token)
        saved = runs.load(run_id)

        self.assertEqual(finalized["status"], "closed")
        self.assertEqual(finalized["preview_url"], f"/preview/{run_id}")
        self.assertEqual(saved["source"]["kind"], "group_order")
        self.assertEqual(saved["source"]["session_id"], room["id"])
        self.assertEqual(saved["stats"]["drinks"], 3)
        self.assertEqual([row["person"] for row in saved["rows"]], ["Alice", "Bob"])
        self.assertEqual(saved["rows"][0]["canonical"]["drink"], "Taro Slush")
        self.assertEqual(saved["rows"][0]["toppings"], ["Boba"])
        self.assertEqual(saved["rows"][0]["canonical"]["toppings"], ["Boba"])
        self.assertNotIn("preview_url", group_orders.get(room["id"]))

        repeated, repeated_id = group_orders.finalize(room["id"], organizer_token)
        self.assertEqual(repeated_id, run_id)
        self.assertEqual(repeated["preview_url"], finalized["preview_url"])

    def test_sheet_and_group_link_orders_share_the_same_downstream_shape(self):
        sheet = importer.import_bytes(
            b"Name,Drink,Size,Sugar,Ice,Toppings,Milk,Temperature,Qty,Notes\n"
            b"Alice,Taro Slush,Large,50%,Less Ice,Boba,,,1,\n",
            "orders.csv",
        )
        room, organizer_token = group_orders.create()
        group_orders.add_order(room["id"], ORDER)
        _finalized, run_id = group_orders.finalize(room["id"], organizer_token)

        sheet_row = pipeline.enrich(sheet.as_dict())["rows"][0]
        group_row = pipeline.enrich(runs.load(run_id))["rows"][0]
        comparable = (
            "person", "drink", "size", "sugar", "ice", "milk", "temperature",
            "toppings", "quantity", "notes", "canonical", "issues", "ok", "match",
        )
        self.assertEqual(
            {key: sheet_row[key] for key in comparable},
            {key: group_row[key] for key in comparable},
        )
        self.assertEqual(sheet.source["kind"], "upload")
        self.assertEqual(runs.load(run_id)["source"]["kind"], "group_order")

    def test_finalize_requires_an_order_and_the_organizer_token(self):
        room, organizer_token = group_orders.create()
        with self.assertRaises(group_orders.Forbidden):
            group_orders.finalize(room["id"], "wrong")
        with self.assertRaises(group_orders.EmptyRoom):
            group_orders.finalize(room["id"], organizer_token)

    def test_organizer_switches_from_menu_estimate_to_built_cart_total(self):
        room, organizer_token = group_orders.create()
        group_orders.add_order(room["id"], ORDER)
        _finalized, run_id = group_orders.finalize(room["id"], organizer_token)
        run = runs.load(run_id)
        run["cart"] = {
            "status": "ready", "review_ready": True,
            "added": [{"row_number": 2, "person": "Alice", "drink": "Taro Slush",
                       "quantity": 1, "actual_total": 7.00}],
            "totals": {"subtotal": 7.00, "tax": 0.62, "fees": {}, "total": 7.62},
        }
        runs.save(run, run_id)

        dashboard = group_orders.get_for_organizer(room["id"], organizer_token)

        self.assertEqual(dashboard["costs"]["source"], "cart_total")
        self.assertEqual(dashboard["costs"]["by_person"][0]["total"], 7.62)
        self.assertEqual(dashboard["orders"][0]["actual_total"], 7.00)

    def test_selected_store_drives_final_matching_and_cart_destination(self):
        room, organizer_token = group_orders.create(restaurant_id=ALTERNATE_STORE)
        group_orders.add_order(room["id"], ORDER)

        _finalized, run_id = group_orders.finalize(room["id"], organizer_token)
        run = pipeline.enrich(runs.load(run_id))
        washington_taro = next(
            item for item in menu.participant_menu(ALTERNATE_STORE)["items"]
            if item["name"] == "Taro Slush"
        )

        self.assertEqual(run["source"]["restaurant_id"], ALTERNATE_STORE)
        self.assertEqual(run["match"]["restaurant_id"], ALTERNATE_STORE)
        self.assertEqual(run["rows"][0]["match"]["item"]["id"], washington_taro["id"])
        self.assertEqual(run["rows"][0]["match"]["item"]["price"], 5.75)

        edited = importer.apply_row_edit(
            runs.load(run_id), run["rows"][0]["row_number"], {"drink": "Apple Black Tea"})
        edited = pipeline.enrich(edited)
        self.assertEqual(edited["rows"][0]["canonical"]["drink"], "Apple Black Tea")
        self.assertEqual(edited["rows"][0]["match"]["item"]["name"], "Apple Black Tea")

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


class ParticipantMenuTests(unittest.TestCase):
    def test_every_captured_store_has_its_own_participant_menu(self):
        stores = menu.available_stores()
        self.assertTrue(
            {menu.TARGET_STORE, ALTERNATE_STORE}.issubset(
                {store["restaurant_id"] for store in stores}))
        alternate = menu.participant_menu(ALTERNATE_STORE)
        taro = next(item for item in alternate["items"] if item["name"] == "Taro Slush")
        self.assertEqual(alternate["restaurant_id"], ALTERNATE_STORE)
        self.assertIn("Washington Ave", alternate["store"])
        self.assertEqual(taro["price"], 5.75)

    def test_live_store_directory_filters_hidden_and_non_takeout_locations(self):
        class FakeDirectoryApi:
            @staticmethod
            def list_stores():
                return [
                    {"id": "visible", "name": "Visible", "city": "Boston",
                     "state": "MA", "takeout": True},
                    {"id": "hidden", "name": "Hidden", "hide_from_picker": True,
                     "takeout": True},
                    {"id": "delivery", "name": "Delivery", "takeout": False},
                ]

        self.assertEqual(menu.store_directory(api=FakeDirectoryApi()), [{
            "restaurant_id": "visible", "name": "Visible", "address": None,
            "city": "Boston", "state": "MA", "zip": None, "item_count": None,
        }])

    def test_menu_contains_only_real_per_drink_choices(self):
        payload = menu.participant_menu()
        self.assertEqual(payload["item_count"], len(payload["items"]))
        self.assertGreater(payload["item_count"], 100)

        taro = next(item for item in payload["items"] if item["name"] == "Taro Slush")
        groups = {group["axis"]: group for group in taro["option_groups"]}
        self.assertEqual([option["label"] for option in groups["size"]["options"]],
                         ["Medium", "Large"])
        self.assertIn("50%", [option["label"] for option in groups["sugar"]["options"]])
        self.assertIn("Boba", [option["label"] for option in groups["toppings"]["options"]])
        self.assertNotIn("ice", groups)  # Taro Slush genuinely has no ice choice.

    def test_menu_names_are_unique_and_categories_are_populated(self):
        payload = menu.participant_menu()
        names = [item["name"] for item in payload["items"]]
        self.assertEqual(len(names), len(set(names)))
        self.assertIn("Slush", payload["categories"])
        self.assertTrue(all(item["category"] in payload["categories"]
                            for item in payload["items"] if item["category"]))


class GroupOrderHttpTests(unittest.TestCase):
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

    def test_room_lifecycle_over_http(self):
        status, created = self.request("POST", "/api/group-orders", {
            "title": "Monday tea", "organizer_name": "Mariam", "expires_in_hours": 2,
            "restaurant_id": ALTERNATE_STORE,
        })
        self.assertEqual(status, 201)
        room_id = created["session_id"]
        organizer_token = created["organizer_token"]
        self.assertEqual(created["session"]["restaurant_id"], ALTERNATE_STORE)
        self.assertIn("Washington Ave", created["session"]["store_name"])
        self.assertEqual(created["share_url"], f"/group-order/{room_id}")
        self.assertEqual(
            created["organizer_url"],
            f"/group-order/{room_id}/organizer#token={organizer_token}",
        )

        with urllib.request.urlopen(
                self.base + f"/group-order/{room_id}/organizer", timeout=10) as response:
            organizer_page = response.read().decode("utf-8")
        self.assertIn("Organizer dashboard", organizer_page)
        self.assertIn("/static/group-order-organizer.js", organizer_page)

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

        with urllib.request.urlopen(self.base + created["share_url"], timeout=10) as response:
            page = response.read().decode("utf-8")
            self.assertEqual(response.status, 200)
            self.assertIn("text/html", response.headers.get("Content-Type", ""))
        self.assertIn("Add your drink", page)
        self.assertIn("Group drinks", page)
        self.assertIn("/static/group-order.js", page)

        status, public = self.request("GET", created["api_url"])
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

        status, private = self.request(
            "GET", f"/api/group-orders/{room_id}/organizer", headers={
                "X-Organizer-Token": organizer_token,
            })
        self.assertEqual(status, 200)
        self.assertIsNone(private["session"]["preview_url"])

        status, finalized = self.request(
            "POST", f"/api/group-orders/{room_id}/finalize", {},
            {"X-Organizer-Token": organizer_token})
        self.assertEqual(status, 200)
        self.assertEqual(finalized["session"]["status"], "closed")
        self.assertEqual(finalized["preview_url"],
                         f"/preview/{finalized['run_id']}")

        status, repeated = self.request(
            "POST", f"/api/group-orders/{room_id}/finalize", {},
            {"X-Organizer-Token": organizer_token})
        self.assertEqual(repeated["run_id"], finalized["run_id"])

        status, run_response = self.request("GET", finalized["preview_url"].replace(
            "/preview/", "/api/runs/"))
        self.assertEqual(status, 200)
        self.assertEqual(run_response["run"]["source"]["kind"], "group_order")
        self.assertEqual(run_response["run"]["match"]["restaurant_id"], ALTERNATE_STORE)

    def test_bad_room_payloads_return_structured_errors(self):
        status, invalid = self.request(
            "POST", "/api/group-orders", {"expires_in_hours": 999})
        self.assertEqual(status, 400)
        self.assertEqual(invalid["code"], "invalid_request")

        status, missing = self.request(
            "GET", "/api/group-orders/aaaaaaaaaaaaaaaaaaaaaaaa")
        self.assertEqual(status, 404)
        self.assertEqual(missing["code"], "room_not_found")

    def test_participant_menu_endpoint_has_item_specific_options(self):
        directory = menu.available_stores()
        with mock.patch.object(menu, "store_directory", return_value=directory):
            status, stores = self.request("GET", "/api/stores")
        self.assertEqual(status, 200)
        self.assertIn(ALTERNATE_STORE,
                      {store["restaurant_id"] for store in stores["stores"]})

        status, response = self.request(
            "GET", f"/api/menu?restaurant_id={ALTERNATE_STORE}")
        self.assertEqual(status, 200)
        self.assertEqual(response["menu"]["restaurant_id"], ALTERNATE_STORE)
        taro = next(item for item in response["menu"]["items"]
                    if item["name"] == "Taro Slush")
        size = next(group for group in taro["option_groups"] if group["axis"] == "size")
        self.assertEqual([choice["label"] for choice in size["options"]],
                         ["Medium", "Large"])
        self.assertEqual(taro["price"], 5.75)

        status, missing = self.request(
            "GET", "/api/menu?restaurant_id=not-a-captured-store")
        self.assertEqual(status, 404)
        self.assertFalse(missing["ok"])


if __name__ == "__main__":
    unittest.main()
