"""Task 28: whole-order templates on the server.

Templates live in the browser; the server only validates their entries when
one is loaded as a starting point — either as a spreadsheet-path run or as
pending group-room suggestions — so a stale or hand-edited template still
gets the same checks as any other collection path.
"""

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

from app import group_orders, menu, order_templates, pipeline, runs, server


ORDER = {
    "person": "Alice",
    "drink": "Taro Slush",
    "size": "Large",
    "sugar": "50%",
    "ice": "Less Ice",
    "toppings": ["Boba"],
    "quantity": 1,
}


class TemplateRunTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.original_run_dir = runs.RUN_DIR
        runs.RUN_DIR = Path(self.temporary.name) / "runs"

    def tearDown(self):
        runs.RUN_DIR = self.original_run_dir
        self.temporary.cleanup()

    def test_create_run_keeps_every_person_with_their_drink(self):
        run_id, saved = order_templates.create_run({
            "template_name": "Friday crew",
            "entries": [
                {**ORDER},
                {"person": "Bob", "drink": "Winter Melon Tea",
                 "sugar": "50%", "quantity": 2},
            ],
        })
        self.assertEqual(saved["source"]["kind"], "order_template")
        self.assertEqual(saved["source"]["template_name"], "Friday crew")
        self.assertEqual(saved["source"]["restaurant_id"], menu.TARGET_STORE)
        self.assertEqual(
            [(row["person"], row["drink"]) for row in saved["rows"]],
            [("Alice", "Taro Slush"), ("Bob", "Winter Melon Tea")])
        enriched = pipeline.enrich(saved)
        self.assertEqual(
            [row["match"]["status"] for row in enriched["rows"]],
            ["ready", "ready"])
        self.assertIsNotNone(runs.load(run_id))

    def test_entries_need_a_name_and_a_drink(self):
        for entries in (
            [],
            "not-a-list",
            [{"person": "", "drink": "Taro Slush"}],
            [{"person": "Alice", "drink": "   "}],
            [{"drink": "Taro Slush"}],
        ):
            with self.assertRaises(order_templates.OrderTemplateError):
                order_templates.create_run({"entries": entries})

    def test_quantity_bounds_match_the_solo_ceiling(self):
        with self.assertRaises(order_templates.OrderTemplateError):
            order_templates.create_run({"entries": [{**ORDER, "quantity": 0}]})
        with self.assertRaises(order_templates.OrderTemplateError):
            order_templates.create_run({"entries": [{**ORDER, "quantity": 21}]})
        _run_id, saved = order_templates.create_run(
            {"entries": [{**ORDER, "quantity": "2"}]})
        self.assertEqual(saved["rows"][0]["quantity"], 2)

    def test_error_names_the_entry_position(self):
        with self.assertRaises(order_templates.OrderTemplateError) as raised:
            order_templates.create_run({"entries": [{**ORDER}, {"person": "Bob"}]})
        self.assertIn("entry 2", str(raised.exception))

    def test_unknown_store_is_a_404(self):
        with self.assertRaises(order_templates.UnknownTemplateStore) as raised:
            order_templates.create_run(
                {"entries": [{**ORDER}]}, restaurant_id="no-such-store")
        self.assertEqual(raised.exception.status, 404)

    def test_unmatched_drink_still_creates_a_run_for_reconciliation(self):
        run_id, saved = order_templates.create_run(
            {"entries": [{**ORDER, "drink": "Not A Real Drink At All"}]})
        enriched = pipeline.enrich(saved)
        self.assertNotEqual(enriched["rows"][0]["match"]["status"], "ready")
        self.assertIsNotNone(runs.load(run_id))


class GroupSuggestionTests(unittest.TestCase):
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

    def test_seeded_entries_are_pending_not_submitted(self):
        room, organizer_token = group_orders.create()
        seeded = group_orders.seed_suggestions(
            room["id"], [{**ORDER}, {"person": "Bob", "drink": "Winter Melon Tea"}],
            organizer_token)
        # Pending entries are visible but excluded from every submitted count.
        self.assertEqual(len(seeded["suggestions"]), 2)
        self.assertEqual(seeded["summary"]["orders"], 0)
        self.assertEqual(seeded["summary"]["people"], 0)
        # And finalizing an unconfirmed room still reports an empty room.
        with self.assertRaises(group_orders.EmptyRoom):
            group_orders.finalize(room["id"], organizer_token)

    def test_seeding_needs_the_organizer_token(self):
        room, _organizer_token = group_orders.create()
        with self.assertRaises(group_orders.Forbidden):
            group_orders.seed_suggestions(
                room["id"], [{**ORDER}], "wrong-token")
        self.assertEqual(group_orders.get(room["id"])["suggestions"], [])

    def test_confirm_moves_a_suggestion_into_submitted_orders(self):
        room, organizer_token = group_orders.create()
        seeded = group_orders.seed_suggestions(room["id"], [{**ORDER}], organizer_token)
        suggestion_id = seeded["suggestions"][0]["id"]

        order, edit_token, updated = group_orders.confirm_suggestion(
            room["id"], suggestion_id, {})
        self.assertTrue(edit_token)
        self.assertEqual(order["person"], "Alice")
        self.assertEqual(order["drink"], "Taro Slush")
        self.assertEqual(updated["suggestions"], [])
        self.assertEqual(updated["summary"]["orders"], 1)
        # The confirmed order is a real order: editable with its edit token.
        changed, _room = group_orders.update_order(
            room["id"], order["id"], {"sugar": "30%"}, order_token=edit_token)
        self.assertEqual(changed["sugar"], "30%")

    def test_confirm_accepts_changes_before_submitting(self):
        room, organizer_token = group_orders.create()
        seeded = group_orders.seed_suggestions(room["id"], [{**ORDER}], organizer_token)
        suggestion_id = seeded["suggestions"][0]["id"]

        order, _edit_token, updated = group_orders.confirm_suggestion(
            room["id"], suggestion_id, {"sugar": "30%", "quantity": 2})
        self.assertEqual(order["sugar"], "30%")
        self.assertEqual(order["quantity"], 2)
        self.assertEqual(updated["suggestions"], [])

    def test_confirming_an_unknown_suggestion_is_a_404(self):
        room, _organizer_token = group_orders.create()
        with self.assertRaises(group_orders.OrderNotFound):
            group_orders.confirm_suggestion(room["id"], "no-such-suggestion", {})

    def test_participant_can_remove_their_expected_entry(self):
        room, organizer_token = group_orders.create()
        seeded = group_orders.seed_suggestions(
            room["id"], [{**ORDER}, {"person": "Bob", "drink": "Tea"}],
            organizer_token)
        ids = [entry["id"] for entry in seeded["suggestions"]]
        updated = group_orders.delete_suggestion(room["id"], ids[0])
        self.assertEqual(
            [entry["person"] for entry in updated["suggestions"]], ["Bob"])
        # The organizer can remove the rest without touching submitted orders.
        updated = group_orders.delete_suggestion(
            room["id"], ids[1], organizer_token=organizer_token)
        self.assertEqual(updated["suggestions"], [])

    def test_suggestions_never_leak_secrets(self):
        room, organizer_token = group_orders.create()
        seeded = group_orders.seed_suggestions(room["id"], [{**ORDER}], organizer_token)
        self.assertNotIn("token", json.dumps(seeded))

    def test_replace_mode_reseeds_while_append_mode_accumulates(self):
        room, organizer_token = group_orders.create()
        group_orders.seed_suggestions(room["id"], [{**ORDER}], organizer_token)
        replaced = group_orders.seed_suggestions(
            room["id"], [{"person": "Bob", "drink": "Tea"}],
            organizer_token, mode="replace")
        self.assertEqual(
            [entry["person"] for entry in replaced["suggestions"]], ["Bob"])
        appended = group_orders.seed_suggestions(
            room["id"], [{**ORDER}], organizer_token, mode="append")
        self.assertEqual(
            [entry["person"] for entry in appended["suggestions"]],
            ["Bob", "Alice"])

    def test_organizer_can_submit_all_expected_orders_then_finalize(self):
        room, organizer_token = group_orders.create()
        group_orders.seed_suggestions(
            room["id"], [{**ORDER}, {"person": "Bob", "drink": "Tea"}],
            organizer_token)
        submitted, updated = group_orders.submit_all_suggestions(
            room["id"], organizer_token)
        self.assertEqual(len(submitted), 2)
        self.assertEqual(updated["suggestions"], [])
        self.assertEqual(updated["summary"]["orders"], 2)
        # And the room now finalizes straight to cart review.
        _room, run_id = group_orders.finalize(room["id"], organizer_token)
        self.assertTrue(run_id)

    def test_submit_all_needs_the_organizer_token(self):
        room, organizer_token = group_orders.create()
        group_orders.seed_suggestions(room["id"], [{**ORDER}], organizer_token)
        with self.assertRaises(group_orders.Forbidden):
            group_orders.submit_all_suggestions(room["id"], "wrong-token")
        with self.assertRaises(group_orders.GroupOrderError):
            group_orders.submit_all_suggestions(room["id"], "")
        self.assertEqual(len(group_orders.get(room["id"])["suggestions"]), 1)

    def test_submit_all_validates_everything_before_writing_anything(self):
        room, organizer_token = group_orders.create(budget_cap="0.01")
        group_orders.seed_suggestions(
            room["id"],
            [{**ORDER, "quantity": 1}, {"person": "Bob", "drink": "Tea", "quantity": 1}],
            organizer_token)
        # Any priced drink breaks a $0.01 cap: nothing converts.
        with self.assertRaises(group_orders.BudgetExceeded):
            group_orders.submit_all_suggestions(room["id"], organizer_token)
        fresh = group_orders.get(room["id"])
        self.assertEqual(len(fresh["suggestions"]), 2)
        self.assertEqual(fresh["summary"]["orders"], 0)


class TemplateHttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.original_run_dir = runs.RUN_DIR
        cls.original_session_dir = group_orders.SESSION_DIR
        runs.RUN_DIR = Path(cls.temporary.name) / "runs"
        group_orders.SESSION_DIR = Path(cls.temporary.name) / "group-orders"
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.base = f"http://127.0.0.1:{cls.httpd.server_address[1]}"
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        runs.RUN_DIR = cls.original_run_dir
        group_orders.SESSION_DIR = cls.original_session_dir
        cls.temporary.cleanup()

    def request(self, method: str, path: str, payload=None, headers=None):
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        merged = dict(headers or {})
        if payload is not None:
            merged["Content-Type"] = "application/json"
        request = urllib.request.Request(
            self.base + path, data=data, method=method, headers=merged)
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                body = response.read()
                try:
                    return response.status, json.loads(body)
                except json.JSONDecodeError:
                    return response.status, body
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            try:
                return exc.code, json.loads(raw)
            except json.JSONDecodeError:
                return exc.code, raw

    def test_template_run_creates_a_previewable_multi_person_run(self):
        status, created = self.request("POST", "/api/template-runs", {
            "template_name": "Friday crew",
            "entries": [{**ORDER}, {"person": "Bob", "drink": "Winter Melon Tea"}],
        })
        self.assertEqual(status, 201, created)
        self.assertTrue(created["ok"])
        self.assertTrue(created["preview_url"].startswith("/preview/"))

        status, fetched = self.request("GET", f"/api/runs/{created['run_id']}")
        self.assertEqual(status, 200, fetched)
        self.assertEqual(fetched["run"]["source"]["kind"], "order_template")
        rows = fetched["run"]["rows"]
        self.assertEqual({row["person"] for row in rows}, {"Alice", "Bob"})

    def test_template_run_rejects_nameless_entries(self):
        status, body = self.request("POST", "/api/template-runs", {
            "entries": [{"person": "", "drink": "Taro Slush"}],
        })
        self.assertEqual(status, 400)
        self.assertFalse(body["ok"])

    def test_group_suggestion_flow_over_http(self):
        status, created = self.request("POST", "/api/group-orders", {
            "title": "Friday boba",
        })
        self.assertEqual(status, 201, created)
        room_id = created["session_id"]
        organizer_token = created["organizer_token"]

        # Seeding without the organizer token is forbidden.
        status, _body = self.request(
            "POST", f"/api/group-orders/{room_id}/suggestions",
            {"entries": [{**ORDER}]})
        self.assertEqual(status, 403)

        status, seeded = self.request(
            "POST", f"/api/group-orders/{room_id}/suggestions",
            {"entries": [{**ORDER}]},
            headers={"X-Organizer-Token": organizer_token})
        self.assertEqual(status, 200, seeded)
        self.assertEqual(len(seeded["session"]["suggestions"]), 1)
        self.assertEqual(seeded["session"]["summary"]["orders"], 0)
        suggestion_id = seeded["session"]["suggestions"][0]["id"]

        # Anyone with the link can confirm (with edits) while the room is open.
        status, confirmed = self.request(
            "POST",
            f"/api/group-orders/{room_id}/suggestions/{suggestion_id}/confirm",
            {"quantity": 2})
        self.assertEqual(status, 201, confirmed)
        self.assertTrue(confirmed["order_token"])
        self.assertEqual(confirmed["order"]["quantity"], 2)
        self.assertEqual(confirmed["session"]["suggestions"], [])
        self.assertEqual(confirmed["session"]["summary"]["orders"], 1)

    def test_submit_all_then_finalize_over_http(self):
        status, created = self.request("POST", "/api/group-orders", {
            "title": "Friday boba",
        })
        room_id = created["session_id"]
        organizer_token = created["organizer_token"]
        _status, seeded = self.request(
            "POST", f"/api/group-orders/{room_id}/suggestions",
            {"entries": [{**ORDER}, {"person": "Bob", "drink": "Tea"}]},
            headers={"X-Organizer-Token": organizer_token})
        self.assertEqual(len(seeded["session"]["suggestions"]), 2)

        status, submitted = self.request(
            "POST", f"/api/group-orders/{room_id}/suggestions/submit-all", {},
            headers={"X-Organizer-Token": organizer_token})
        self.assertEqual(status, 200, submitted)
        self.assertEqual(submitted["submitted"], 2)
        self.assertEqual(submitted["session"]["suggestions"], [])
        self.assertEqual(submitted["session"]["summary"]["orders"], 2)

        status, finalized = self.request(
            "POST", f"/api/group-orders/{room_id}/finalize", {},
            headers={"X-Organizer-Token": organizer_token})
        self.assertEqual(status, 200, finalized)
        self.assertTrue(finalized["preview_url"].startswith("/preview/"))

    def test_participant_can_dismiss_an_expected_entry_over_http(self):
        status, created = self.request("POST", "/api/group-orders", {
            "title": "Friday boba",
        })
        room_id = created["session_id"]
        organizer_token = created["organizer_token"]
        _status, seeded = self.request(
            "POST", f"/api/group-orders/{room_id}/suggestions",
            {"entries": [{**ORDER}]},
            headers={"X-Organizer-Token": organizer_token})
        suggestion_id = seeded["session"]["suggestions"][0]["id"]

        status, updated = self.request(
            "DELETE", f"/api/group-orders/{room_id}/suggestions/{suggestion_id}")
        self.assertEqual(status, 200, updated)
        self.assertEqual(updated["session"]["suggestions"], [])


if __name__ == "__main__":
    unittest.main()
