"""Task 5: live cart construction and safe handoff."""

from __future__ import annotations

import datetime
import json
import unittest

from app import cart, importer, menu, pipeline
from scripts import kft_api


def matched(csv_text: str) -> dict:
    parsed = importer.import_bytes(csv_text.encode(), "orders.csv")
    return pipeline.enrich(parsed.as_dict())


def raw_item(name: str, *, sold_out: bool = False) -> dict:
    source = next(item for item in menu.snapshot()["items"] if item["name"] == name)
    return {
        "id": source["id"],
        "name": source["name"],
        "category": source["category"],
        "description": source.get("description"),
        "display_price": source["base_price"],
        "can_order": not sold_out,
        "is_sold_out": sold_out,
        "prices": [
            {"name": tier, "price": source["base_price"], "is_default": index == 0}
            for index, tier in enumerate(source["price_tiers"])
        ],
        "option_groups": [
            {
                "name": group["group_name"],
                "min": group["min"],
                "max": group["max"] or 0,
                "multiselect": group["multiselect"],
                "quantities": group["allows_quantity"],
                "free_opts": group["free_choices"],
                "options": [
                    {
                        "name": option["name"], "price": option["price"],
                        "cal": option.get("calories"),
                        "is_default": option["is_default"],
                        "is_disabled": option["is_disabled"],
                    }
                    for option in group["options"]
                ],
            }
            for group in source["option_groups"]
        ],
    }


class FakeApi:
    def __init__(self, items, *, can_order=True, accepting=True, fail_ids=()):
        self.items = items
        self.can_order = can_order
        self.accepting = accepting
        self.fail_ids = set(fail_ids)
        self.create_calls = 0
        self.add_calls = []
        self.added = []

    def list_stores(self):
        return [{
            "id": menu.TARGET_STORE,
            "name": "5th Ave, Bk [8625 5th Ave]",
            "address": "8625 5th Ave", "city": "Brooklyn", "state": "NY", "zip": "11209",
            "tz": "America/New_York", "lead": 10, "takeout": True,
            "accepting_orders": self.accepting,
            "hours": {
                "m_open": 660, "m_close": 1195, "t_open": 660, "t_close": 1195,
                "w_open": 660, "w_close": 1195, "th_open": 660, "th_close": 1195,
                "f_open": 660, "f_close": 1195, "s_open": 660, "s_close": 1195,
                "su_open": 660, "su_close": 1195,
            },
        }]

    def get_menu(self, restaurant_id):
        self.restaurant_id = restaurant_id
        return {"name": "5th Ave, Bk [8625 5th Ave]",
                "can_order": self.can_order, "menu": self.items}

    def create_order(self, restaurant_id, order_type="takeout"):
        self.create_calls += 1
        return {"order_id": f"source-{self.create_calls}", "token": "never-persist-this"}

    def add_item(self, order_id, token, item_id, **kwargs):
        self.add_calls.append({"order_id": order_id, "token": token,
                               "item_id": item_id, **kwargs})
        if item_id in self.fail_ids:
            raise kft_api.ApiError("This drink just sold out", status=400)
        source = next(item for item in self.items if item["id"] == item_id)
        result = {
            "id": f"line-{len(self.added) + 1}", "name": source["name"],
            "quantity": kwargs["quantity"],
            "total_price": round(source["display_price"] * kwargs["quantity"], 2),
        }
        self.added.append(result)
        return result

    def quote_order(self, order_id, token):
        return {}  # this is what the live endpoint currently returns

    def get_order(self, order_id, token):
        subtotal = round(sum(item["total_price"] for item in self.added), 2)
        tax = round(subtotal * 0.08875, 2)
        return {"id": order_id, "items": self.added, "subtotal": subtotal,
                "tax": tax, "total_amount": round(subtotal + tax, 2)}

    @staticmethod
    def handoff_url(restaurant_id, order_id):
        return kft_api.handoff_url(restaurant_id, order_id)


class CartBuildTests(unittest.TestCase):
    def test_builds_every_mapped_line_and_returns_manifest_and_handoff(self):
        run = matched(
            "Name,Drink,Size,Sugar,Toppings,Notes\n"
            "Alice,Taro Slush,Large,50%,Boba,blue cup\n"
        )
        api = FakeApi([raw_item("Taro Slush")])

        built = cart.build(run, api=api)

        self.assertEqual(built["cart"]["status"], "ready")
        self.assertTrue(built["cart"]["review_ready"])
        self.assertEqual(built["cart"]["counts"]["added_drinks"], 1)
        self.assertIn("?order_id=source-1", built["handoff_url"])
        self.assertEqual(built["manifest"][0]["person"], "Alice")
        sent = api.add_calls[0]
        self.assertEqual(sent["size"], "Regular")
        self.assertEqual(sent["for_name"], "Alice")
        self.assertEqual(sent["notes"], "blue cup")
        self.assertEqual(sent["options"]["Choose A Size"][0]["name"], "Large .7")
        self.assertEqual(sent["options"]["Sugar Level"][0]["name"], "Half S 50%")
        self.assertNotIn("never-persist-this", json.dumps(built))
        self.assertEqual(built["cart"]["totals"]["total"], 6.48)

    def test_closed_store_still_builds_a_reviewable_cart_and_says_when_it_opens(self):
        run = matched("Name,Drink,Size\nAlice,Taro Slush,Medium\n")
        api = FakeApi([raw_item("Taro Slush")], can_order=False)
        ten_am_eastern = datetime.datetime(2026, 8, 25, 14, 0,
                                            tzinfo=datetime.timezone.utc)

        built = cart.build(run, api=api, now=ten_am_eastern)

        state = built["cart"]["store"]["state_now"]
        self.assertEqual(state["kind"], "closed")
        self.assertIn("today at 11:00 AM", state["message"])
        self.assertTrue(built["cart"]["review_ready"])
        self.assertEqual(api.create_calls, 1)

    def test_sold_out_item_is_reported_without_creating_an_empty_cart(self):
        run = matched("Name,Drink,Size\nAlice,Taro Slush,Medium\n")
        api = FakeApi([raw_item("Taro Slush", sold_out=True)])

        built = cart.build(run, api=api)

        self.assertEqual(built["cart"]["status"], "failed")
        self.assertEqual(built["cart"]["failed"][0]["code"], "sold_out")
        self.assertNotIn("handoff_url", built)
        self.assertEqual(api.create_calls, 0)

    def test_a_modifier_removed_since_preview_fails_that_line_instead_of_dropping_it(self):
        run = matched("Name,Drink,Size,Toppings\nAlice,Taro Slush,Medium,Boba\n")
        item = raw_item("Taro Slush")
        toppings = next(group for group in item["option_groups"]
                        if group["name"] == "Choose Topping(s)")
        toppings["options"] = [option for option in toppings["options"]
                               if option["name"] != "Boba"]
        api = FakeApi([item])

        built = cart.build(run, api=api)

        self.assertEqual(built["cart"]["failed"][0]["code"], "menu_changed")
        self.assertIn("Boba", built["cart"]["failed"][0]["reason"])
        self.assertEqual(api.create_calls, 0)

    def test_one_rejected_item_does_not_discard_the_rest_of_the_cart(self):
        run = matched(
            "Name,Drink,Size\n"
            "Alice,Taro Slush,Medium\n"
            "Bob,Thai Tea Milk Cap,Medium\n"
        )
        items = [raw_item("Taro Slush"), raw_item("Thai Tea Milk Cap")]
        api = FakeApi(items, fail_ids={items[1]["id"]})

        built = cart.build(run, api=api)

        self.assertEqual(built["cart"]["status"], "partial")
        self.assertEqual([line["person"] for line in built["manifest"]], ["Alice"])
        self.assertEqual(built["cart"]["failed"][0]["person"], "Bob")
        self.assertEqual(built["cart"]["failed"][0]["code"], "add_failed")
        self.assertIn("handoff_url", built)
        self.assertEqual(len(api.add_calls), 2)

    def test_store_that_disabled_pickup_is_not_given_a_handoff(self):
        run = matched("Name,Drink,Size\nAlice,Taro Slush,Medium\n")
        api = FakeApi([raw_item("Taro Slush")], accepting=False)

        built = cart.build(run, api=api)

        self.assertEqual(built["cart"]["store"]["state_now"]["kind"], "unavailable")
        self.assertEqual(built["cart"]["failed"][0]["code"], "store_unavailable")
        self.assertNotIn("handoff_url", built)
        self.assertEqual(api.create_calls, 0)

    def test_a_completed_build_is_idempotent_for_the_same_rows(self):
        run = matched("Name,Drink,Size\nAlice,Taro Slush,Medium\n")
        api = FakeApi([raw_item("Taro Slush")])

        first = cart.build(run, api=api)
        second = cart.build(first, api=api)

        self.assertEqual(second["handoff_url"], first["handoff_url"])
        self.assertEqual(api.create_calls, 1)


class LiveMenuAdapterTests(unittest.TestCase):
    def test_live_menu_uses_current_prices_options_and_availability(self):
        item = raw_item("Taro Slush")
        item["display_price"] = 6.25
        item["is_sold_out"] = True
        item["can_order"] = True
        store = menu.live_store_menu({"name": "Test", "menu": [item]}, menu.TARGET_STORE)

        found = store.by_id(item["id"])
        self.assertEqual(found.base_price, 6.25)
        self.assertTrue(found.sold_out)
        self.assertFalse(found.available)


if __name__ == "__main__":
    unittest.main()
