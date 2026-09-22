"""Task 25: sold-out and availability detection, checked twice.

Availability is verified at two points because it can change between them:
when a participant picks a drink (immediate 409 with alternatives, while
they can still re-choose) and when the cart is built (per-row failure codes
plus a clustered summary, without stalling the rest of the run).
"""

from __future__ import annotations

import copy
import datetime as dt
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app import cart, group_orders, importer, mapping, matcher, menu, pipeline
from tests.test_cart import FakeApi, matched, raw_item


def tweaked_store(*, sold_out_drinks=(), disabled_options=()):
    """A captured-menu StoreMenu with scripted outages.

    sold_out_drinks: drink names flagged sold out. disabled_options:
    (drink, option-label) pairs turned off, e.g. ("Taro Slush", "Boba").
    """
    data = copy.deepcopy(menu.snapshot())
    config = mapping.load()
    for item in data["items"]:
        if item["name"] in sold_out_drinks:
            item["available"] = False
            item["sold_out"] = True
        for drink, label in disabled_options:
            if item["name"] != drink:
                continue
            for group in item["option_groups"]:
                for option in group["options"]:
                    if option["name"] == label:
                        option["is_disabled"] = True
    return menu.StoreMenu(data, config)


def disable_live(item: dict, label: str) -> dict:
    """Mark one live-API option disabled, the way a mid-run sell-out reads."""
    for group in item["option_groups"]:
        for option in group["options"]:
            if option["name"] == label:
                option["is_disabled"] = True
    return item


def live_matched(run: dict, store) -> dict:
    return matcher.match(run, store=store)


class SoldOutMenuTests(unittest.TestCase):
    def test_a_disabled_option_is_remembered_not_just_hidden(self):
        store = tweaked_store(disabled_options=[("Taro Slush", "Boba")])
        item = store.find("Taro Slush").item

        self.assertIsNone(item.literal("toppings", "Boba"))
        self.assertNotIn("Boba", item.options("toppings"))
        self.assertEqual(item.unavailable("toppings", "Boba"), "Boba")

    def test_an_enabled_option_is_not_reported_unavailable(self):
        store = tweaked_store(disabled_options=[("Taro Slush", "Boba")])
        item = store.find("Taro Slush").item

        self.assertIsNone(item.unavailable("toppings", "Pudding"))
        self.assertIsNone(item.unavailable("toppings", "Unicorn Sprinkles"))
        self.assertIsNone(item.unavailable("size", ""))


class SoldOutMatcherTests(unittest.TestCase):
    def run_one(self, row: dict, store) -> dict:
        run = {
            "source": {"restaurant_id": menu.TARGET_STORE},
            "column_map": {},
            "rows": [{"row_number": 2, "person": "Alice", "quantity": 1,
                      **row}],
        }
        return live_matched(run, store)["rows"][0]

    def test_a_sold_out_topping_is_named_not_misreported(self):
        store = tweaked_store(disabled_options=[("Taro Slush", "Boba")])
        row = self.run_one(
            {"drink": "Taro Slush",
             "canonical": {"drink": "Taro Slush", "toppings": ["Boba"]}},
            store)

        self.assertEqual(row["match"]["status"], matcher.READY)
        problem = row["match"]["unmapped"][0]
        self.assertEqual(problem["asked"], "Boba")
        self.assertTrue(problem["sold_out"])
        self.assertIn("sold out", problem["why"])
        # The re-choose list is what is still orderable, not the outage.
        self.assertNotIn("Boba", row["match"]["choices"]["toppings"])
        self.assertIn("Pudding", row["match"]["choices"]["toppings"])
        self.assertNotIn("Boba", [o["name"] for o in row["match"]["options"]
                                  if o["axis"] == "toppings"])

    def test_a_never_offered_topping_keeps_its_old_message(self):
        store = tweaked_store(disabled_options=[("Taro Slush", "Boba")])
        row = self.run_one(
            {"drink": "Taro Slush",
             "canonical": {"drink": "Taro Slush",
                           "toppings": ["Coconut Jelly"]}},
            store)

        problem = row["match"]["unmapped"][0]
        self.assertFalse(problem.get("sold_out"))
        self.assertIn("doesn't take Coconut Jelly", problem["why"])

    def test_a_sold_out_drink_names_itself_with_suggestions(self):
        store = tweaked_store(sold_out_drinks=["Taro Slush"])
        row = self.run_one({"drink": "Taro Slush",
                            "canonical": {"drink": "Taro Slush"}}, store)

        self.assertEqual(row["match"]["status"], matcher.NEEDS_DRINK)
        self.assertEqual(row["match"]["sold_out"], "Taro Slush")
        self.assertIn("sold out today",
                      " ".join(i["message"] for i in row["issues"]))

    def test_repeated_outages_cluster_into_one_warning(self):
        store = tweaked_store(disabled_options=[("Taro Slush", "Boba")])
        run = {
            "source": {"restaurant_id": menu.TARGET_STORE},
            "column_map": {},
            "rows": [
                {"row_number": 2, "person": "Alice", "quantity": 1,
                 "drink": "Taro Slush",
                 "canonical": {"drink": "Taro Slush", "toppings": ["Boba"]}},
                {"row_number": 3, "person": "Bob", "quantity": 1,
                 "drink": "Taro Slush",
                 "canonical": {"drink": "Taro Slush", "toppings": ["Boba"]}},
            ],
        }
        result = live_matched(run, store)

        summary = result["match"]["availability"]
        self.assertEqual(len(summary["sold_out_options"]), 1)
        self.assertEqual(summary["sold_out_options"][0]["asked"], "Boba")
        self.assertEqual(summary["sold_out_options"][0]["rows"], [2, 3])
        clustered = [i for i in result["issues"]
                     if i.get("code") == "match:sold-out-option"]
        self.assertEqual(len(clustered), 1)
        self.assertIn("2 drinks", clustered[0]["message"])

    def test_a_single_outage_needs_no_cluster_issue(self):
        store = tweaked_store(disabled_options=[("Taro Slush", "Boba")])
        run = {
            "source": {"restaurant_id": menu.TARGET_STORE},
            "column_map": {},
            "rows": [{"row_number": 2, "person": "Alice", "quantity": 1,
                      "drink": "Taro Slush",
                      "canonical": {"drink": "Taro Slush",
                                    "toppings": ["Boba"]}}],
        }
        result = live_matched(run, store)

        self.assertEqual(len(result["match"]["availability"]["sold_out_options"]), 1)
        self.assertEqual([i for i in result["issues"]
                          if i.get("code") == "match:sold-out-option"], [])


class SelectionTimeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.original_dir = group_orders.SESSION_DIR
        group_orders.SESSION_DIR = Path(self.temporary.name) / "group-orders"
        self.now = dt.datetime.now(dt.timezone.utc)

    def tearDown(self):
        group_orders.SESSION_DIR = self.original_dir
        self.temporary.cleanup()

    def create(self):
        room, token = group_orders.create(
            title="Studio boba", organizer_name="Mariam", now=self.now)
        return room, token

    def add(self, room_id, payload):
        return group_orders.add_order(room_id, payload, now=self.now)

    def update(self, room_id, order_id, payload, **tokens):
        return group_orders.update_order(room_id, order_id, payload,
                                         now=self.now, **tokens)

    def test_a_sold_out_drink_is_rejected_with_alternatives(self):
        room, _token = self.create()
        store = tweaked_store(sold_out_drinks=["Taro Slush"])
        with mock.patch("app.group_orders.menu.store_menu", return_value=store):
            with self.assertRaises(group_orders.SoldOut) as ctx:
                self.add(room["id"], {
                    "person": "Alice", "drink": "Taro Slush"})
        self.assertEqual(ctx.exception.code, "sold_out")
        self.assertIn("Taro Slush", str(ctx.exception))
        self.assertIn("sold out", str(ctx.exception))

    def test_an_unknown_drink_still_passes_selection(self):
        room, _token = self.create()
        order, _edit, _room = self.add(room["id"], {
            "person": "Alice", "drink": "Quantum Slush XYZ"})
        self.assertEqual(order["drink"], "Quantum Slush XYZ")

    def test_a_sold_out_topping_is_rejected_with_what_is_left(self):
        room, _token = self.create()
        store = tweaked_store(disabled_options=[("Taro Slush", "Boba")])
        with mock.patch("app.group_orders.menu.store_menu", return_value=store):
            with self.assertRaises(group_orders.SoldOut) as ctx:
                self.add(room["id"], {
                    "person": "Alice", "drink": "Taro Slush",
                    "toppings": ["Boba"]})
        self.assertIn("Boba", str(ctx.exception))
        self.assertIn("still does", str(ctx.exception))

    def test_a_healthy_order_passes_selection(self):
        room, _token = self.create()
        order, _edit, _room = self.add(room["id"], {
            "person": "Alice", "drink": "Taro Slush",
            "toppings": ["Boba"]})
        self.assertEqual(order["drink"], "Taro Slush")

    def test_notes_only_edits_are_never_blocked_by_a_later_sellout(self):
        room, _token = self.create()
        order, edit_token, _room = self.add(room["id"], {
            "person": "Alice", "drink": "Taro Slush",
            "toppings": ["Boba"]})
        store = tweaked_store(sold_out_drinks=["Taro Slush"])
        with mock.patch("app.group_orders.menu.store_menu", return_value=store):
            updated, _room = self.update(
                room["id"], order["id"], {"notes": "extra napkins"},
                order_token=edit_token)
        self.assertEqual(updated["notes"], "extra napkins")

    def test_picking_a_newly_sold_out_drink_is_rejected(self):
        room, _token = self.create()
        order, edit_token, _room = self.add(room["id"], {
            "person": "Alice", "drink": "Thai Tea Milk Cap"})
        store = tweaked_store(sold_out_drinks=["Taro Slush"])
        with mock.patch("app.group_orders.menu.store_menu", return_value=store):
            with self.assertRaises(group_orders.SoldOut):
                self.update(
                    room["id"], order["id"], {"drink": "Taro Slush"},
                    order_token=edit_token)

    def test_adding_a_newly_sold_out_modifier_is_rejected(self):
        room, _token = self.create()
        order, edit_token, _room = self.add(room["id"], {
            "person": "Alice", "drink": "Taro Slush"})
        store = tweaked_store(disabled_options=[("Taro Slush", "Boba")])
        with mock.patch("app.group_orders.menu.store_menu", return_value=store):
            with self.assertRaises(group_orders.SoldOut):
                self.update(
                    room["id"], order["id"], {"toppings": ["Boba"]},
                    order_token=edit_token)
            updated, _room = self.update(
                room["id"], order["id"], {"notes": "no straw"},
                order_token=edit_token)
        self.assertEqual(updated["notes"], "no straw")


class CartBuildAvailabilityTests(unittest.TestCase):
    def test_a_topping_that_sold_out_fails_its_lines_and_clusters(self):
        run = matched(
            "Name,Drink,Size,Toppings\n"
            "Alice,Taro Slush,Medium,Boba\n"
            "Bob,Taro Slush,Medium,Boba\n"
            "Cara,Thai Tea Milk Cap,Medium\n")
        items = [disable_live(raw_item("Taro Slush"), "Boba"),
                 raw_item("Thai Tea Milk Cap")]
        api = FakeApi(items)

        built = cart.build(run, api=api)

        self.assertEqual(built["cart"]["status"], "partial")
        failed = {entry["person"]: entry for entry in built["cart"]["failed"]}
        self.assertEqual(failed["Alice"]["code"], "sold_out")
        self.assertEqual(failed["Bob"]["code"], "sold_out")
        self.assertIn("sold out", failed["Alice"]["reason"])
        self.assertEqual([line["person"] for line in built["manifest"]], ["Cara"])
        self.assertIn("handoff_url", built)
        summary = built["cart"]["availability"]["sold_out_options"]
        self.assertEqual(len(summary), 1)
        self.assertEqual(summary[0]["asked"], "Boba")
        self.assertEqual(sorted(summary[0]["people"]), ["Alice", "Bob"])
        self.assertTrue(summary[0]["sold_out"])

    def test_a_removed_modifier_is_not_called_sold_out(self):
        run = matched("Name,Drink,Size,Toppings\nAlice,Taro Slush,Medium,Boba\n")
        item = raw_item("Taro Slush")
        toppings = next(group for group in item["option_groups"]
                        if group["name"] == "Choose Topping(s)")
        toppings["options"] = [option for option in toppings["options"]
                               if option["name"] != "Boba"]
        api = FakeApi([item])

        built = cart.build(run, api=api)

        self.assertEqual(built["cart"]["failed"][0]["code"], "menu_changed")

    def test_a_drink_that_sold_out_clusters_across_its_rows(self):
        run = matched("Name,Drink,Size\nAlice,Taro Slush,Medium\nBob,Taro Slush,Medium\n")
        api = FakeApi([raw_item("Taro Slush", sold_out=True)])

        built = cart.build(run, api=api)

        self.assertEqual(built["cart"]["status"], "failed")
        self.assertEqual(
            {entry["code"] for entry in built["cart"]["failed"]}, {"sold_out"})
        summary = built["cart"]["availability"]["sold_out_drinks"]
        self.assertEqual(len(summary), 1)
        self.assertEqual(summary[0]["drink"], "Taro Slush")
        self.assertEqual(sorted(summary[0]["people"]), ["Alice", "Bob"])
        self.assertEqual(api.create_calls, 0)

    def test_a_clean_build_reports_empty_availability(self):
        run = matched("Name,Drink,Size\nAlice,Taro Slush,Medium\n")
        api = FakeApi([raw_item("Taro Slush")])

        built = cart.build(run, api=api)

        self.assertEqual(built["cart"]["status"], "ready")
        self.assertEqual(built["cart"]["availability"],
                         {"sold_out_drinks": [], "sold_out_options": []})

    def test_match_availability_survives_the_pipeline(self):
        parsed = importer.import_bytes(
            b"Name,Drink\nAlice,Taro Slush\n", "orders.csv")
        enriched = pipeline.enrich(parsed.as_dict())
        self.assertIn("availability", enriched["match"])


if __name__ == "__main__":
    unittest.main()
