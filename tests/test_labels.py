"""One label per cup, pairing each person with their exact drink."""

from __future__ import annotations

import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from app import importer, labels, pipeline, runs, saved_orders, server


OWNER = "browser_owner_token_abcdefghijklmnopqrstuvwxyz"
OTHER_OWNER = "another_browser_token_abcdefghijklmnopqrstuvwxyz"


def line(person="Alice", drink="Taro Milk Tea", quantity=1, options=None,
         notes="", row_number=2):
    return {
        "row_number": row_number,
        "person": person,
        "drink": drink,
        "quantity": quantity,
        "options": options or [],
        "notes": notes,
    }


def size_sugar_toppings(size="Large .7", sugar="Half S 50%", toppings=None):
    options = [
        {"axis": "size", "name": size, "quantity": 1},
        {"axis": "sugar", "name": sugar, "quantity": 1},
    ]
    for name, count in (toppings or []):
        options.append({"axis": "toppings", "name": name, "quantity": count})
    return options


def finished_run() -> dict:
    parsed = importer.import_bytes(
        b"Name,Drink,Size,Sugar,Toppings,Qty,Notes\n"
        b"Alice,Taro Milk Tea,Large,50%,Boba,2,no straw\n"
        b"Bob,Mango Slush,Medium,100%,,1,\n",
        "team-order.csv",
    )
    run = pipeline.enrich(parsed.as_dict())
    run["run_id"] = "1234abcd"
    rows = {row["person"]: row for row in run["rows"]}
    added = []
    for person in ("Alice", "Bob"):
        row = rows[person]
        found = row["match"]
        added.append({
            "row_number": row["row_number"],
            "person": person,
            "drink": (found.get("item") or {}).get("name") or row["drink"],
            "quantity": found.get("quantity") or row.get("quantity") or 1,
            "options": [
                {key: option.get(key) for key in ("group", "axis", "name", "quantity")}
                for option in found.get("options") or []
            ],
            "notes": row.get("notes") or "",
            "estimated_total": found.get("total"),
        })
    run["cart"] = {
        "status": "ready",
        "review_ready": True,
        "store": {"restaurant_id": run["match"]["restaurant_id"]},
        "counts": {"requested_drinks": 3, "added_drinks": 3, "not_added_drinks": 0},
        "added": added,
        "failed": [],
        "skipped": [],
        "totals": {"subtotal": 19.25, "total": 19.25},
    }
    run["manifest"] = added
    return run


class BuildLabelsTests(unittest.TestCase):
    def test_quantity_expands_to_one_label_per_cup(self):
        made = labels.build_labels([line(quantity=2)])
        self.assertEqual(len(made), 2)
        self.assertEqual([(cup["seq"], cup["cup"], cup["person_cups"]) for cup in made],
                         [(1, 1, 2), (2, 2, 2)])
        self.assertEqual(made[0]["cups"], 2)

    def test_labels_group_by_person_and_keep_cart_order_within_a_group(self):
        made = labels.build_labels([
            line("Bob", "Mango Slush", row_number=3),
            line("Alice", "Taro Milk Tea", row_number=2),
            line("Bob", "Taro Slush", row_number=4),
        ])
        self.assertEqual([cup["person"] for cup in made], ["Alice", "Bob", "Bob"])
        self.assertEqual([cup["drink"] for cup in made],
                         ["Taro Milk Tea", "Mango Slush", "Taro Slush"])
        self.assertEqual([cup["seq"] for cup in made], [1, 2, 3])

    def test_names_group_case_insensitively_under_the_first_spelling(self):
        made = labels.build_labels([line("alice"), line("ALICE"), line("Bob")])
        self.assertEqual([cup["person"] for cup in made], ["alice", "alice", "Bob"])
        self.assertEqual([cup["cup"] for cup in made], [1, 2, 1])

    def test_cart_literals_print_as_cup_words(self):
        made = labels.build_labels([line(options=size_sugar_toppings(
            toppings=[("Boba", 1), ("Pudding", 2)]))])
        self.assertEqual(made[0]["spec"], "Large · 50% sugar · Boba, Pudding ×2")

    def test_similar_drinks_differing_only_in_sugar_read_differently(self):
        half = labels.build_labels(
            [line(options=size_sugar_toppings(sugar="Half S 50%"))])[0]["spec"]
        full = labels.build_labels(
            [line(options=size_sugar_toppings(sugar="Regular Sugar 100%"))])[0]["spec"]
        self.assertNotEqual(half, full)
        self.assertIn("50% sugar", half)
        self.assertIn("100% sugar", full)

    def test_notes_and_row_reference_survive(self):
        made = labels.build_labels([line(notes="no straw", row_number=7)])
        self.assertEqual(made[0]["notes"], "no straw")
        self.assertEqual(made[0]["row_number"], 7)

    def test_missing_names_and_modifiers_fall_back_without_going_blank(self):
        made = labels.build_labels([line("", "", options=[])])
        self.assertEqual(made[0]["person"], "Unlabelled")
        self.assertEqual(made[0]["drink"], "Unknown drink")
        self.assertEqual(made[0]["spec"], "Store defaults")

    def test_format_text_is_one_grouped_line_per_cup(self):
        made = labels.build_labels([
            line("Bob", "Mango Slush"),
            line("Alice", "Taro Milk Tea", quantity=2, notes="no straw"),
        ])
        text = labels.format_text(made, "Friday tea")
        self.assertEqual(text, "\n".join([
            "Friday tea (3 cups)",
            "Alice:",
            "  1/2 Taro Milk Tea — Store defaults (note: no straw)",
            "  2/2 Taro Milk Tea — Store defaults (note: no straw)",
            "Bob:",
            "  1/1 Mango Slush — Store defaults",
        ]))

    def test_format_text_names_an_empty_tray(self):
        self.assertIn("nothing to hand out", labels.format_text([], "Friday tea"))


class LabelApiTests(unittest.TestCase):
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

    def request(self, path: str, token=None):
        headers = {"X-Saved-Orders-Token": token} if token else {}
        request = urllib.request.Request(self.base + path, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def test_run_labels_need_a_reviewable_cart(self):
        run = finished_run()
        run_id = runs.save({key: value for key, value in run.items() if key != "cart"})
        status, payload = self.request(f"/api/runs/{run_id}/labels")
        self.assertEqual(status, 409)
        self.assertFalse(payload["ok"])

    def test_run_labels_pair_every_cup_with_its_person(self):
        run_id = runs.save(finished_run(), "abcd1234")
        status, payload = self.request(f"/api/runs/{run_id}/labels")
        self.assertEqual(status, 200)
        self.assertEqual(payload["cups"], 3)
        self.assertEqual(payload["unplaced"], 0)
        people = [(cup["person"], cup["cup"], cup["person_cups"]) for cup in payload["labels"]]
        self.assertEqual(people, [("Alice", 1, 2), ("Alice", 2, 2), ("Bob", 1, 1)])
        self.assertTrue(all(cup["drink"] and cup["spec"] for cup in payload["labels"]))
        self.assertIn("Alice:", payload["text"])

    def test_saved_order_labels_stay_in_their_browser(self):
        saved = saved_orders.save_finished(finished_run(), OWNER, "Team tea", "2026-09-08")
        status, payload = self.request(f"/api/saved-orders/{saved['id']}/labels", OWNER)
        self.assertEqual(status, 200)
        self.assertEqual(payload["title"], "Team tea")
        self.assertEqual(payload["cups"], 3)
        status, payload = self.request(
            f"/api/saved-orders/{saved['id']}/labels", OTHER_OWNER)
        self.assertEqual(status, 404)
        self.assertFalse(payload["ok"])


if __name__ == "__main__":
    unittest.main()
