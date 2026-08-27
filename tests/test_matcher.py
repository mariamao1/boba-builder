"""Tests for app/matcher.py, app/menu.py and app/mapping.py — Task 4.

Most of these run against a small hand-built menu (`STUB_MENU`) so they say what
they mean and don't move when the snapshot is refreshed. It is built from real
Bay Ridge shapes, though, and every awkwardness in it is one the live menu has:

* two items with the same name, different ids  (14 of them at Bay Ridge)
* a size list of `["Medium", "Large .7"]`      (the upcharge is in the name)
* an item with no ice group at all             (slushes)
* an item with no `Regular Sugar 100%`         (31 items)
* a required `Included Topping` group          (9 items)
* an item sold in exactly one size             (the 4 `Cold` items)

The ones that must see the committed snapshot say so: `RealMenuTests`,
`MappingHealthTests`, `SampleOrderTests`.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import importer, mapping, matcher, menu as menu_module, pipeline  # noqa: E402

SAMPLE = Path(__file__).resolve().parent.parent / "data" / "sample-group-order.csv"


def group(name, axis, options, **flags):
    return {
        "group_name": name,
        "axis": axis,
        "required": flags.get("required", False),
        "min": 1 if flags.get("required") else 0,
        "max": flags.get("max", 1),
        "multiselect": flags.get("multiselect", False),
        "allows_quantity": flags.get("allows_quantity", False),
        "options": [{"name": option, "price": price, "is_default": option == flags.get("default"),
                     "is_disabled": False}
                    for option, price in options],
    }


SIZES = group("Choose A Size", "size", [("Medium", 0), ("Large .7", 0.7)], required=True)
SUGAR = group("Sugar Level", "sugar",
              [("Regular Sugar 100%", 0), ("Less S 70%", 0), ("Half S 50%", 0),
               ("No S 0%", 0)])
ICE = group("Ice Level", "ice", [("Less Ice", 0), ("No Ice", 0), ("More Ice", 0)])
TOPPINGS = group("Choose Topping(s)", "toppings",
                 [("Boba", 0.75), ("Pudding", 0.75), ("OREO®", 0.95)],
                 max=None, multiselect=True, allows_quantity=True)
MILK = group("Milk Alternative", "milk", [("Soy Milk", 0.5)])


def item(item_id, name, groups, price=5.0, category="Tea", **flags):
    return {
        "id": item_id, "name": name, "category": category, "base_price": price,
        "price_tiers": ["Regular"], "default_price_tier": "Regular",
        "available": flags.get("available", True), "sold_out": flags.get("sold_out", False),
        "option_groups": groups,
    }


STUB_MENU = {
    "restaurant_id": "test-store",
    "restaurant_name": "Test Store",
    "items": [
        item("a1", "Taro Slush", [SIZES, SUGAR, TOPPINGS], price=5.95, category="Slush"),
        item("b2", "Honey Green Tea", [SIZES, SUGAR, ICE, TOPPINGS, MILK], price=4.5),
        # No 100% sugar: asking for "regular" here is the recipe, not a request.
        item("c3", "Osmanthus Oolong",
             [group("Choose A Size", "size", [("Cold", 0)], required=True),
              group("Sugar Level", "sugar",
                    [("Less S 70%", 0), ("Half S 50%", 0)], required=True),
              group("Ice Level", "ice", [("Regular Ice", 0), ("Less Ice", 0)],
                    required=True)],
             price=6.59),
        # An included topping you must say yes or no to.
        item("d4", "Mango Slush",
             [SIZES, group("Included Topping", "toppings",
                           [("No Mango Jelly", 0), ("Mango Jelly", 0)], required=True)],
             price=6.25, category="Slush"),
        # Optional sugar and ice, neither offering the "regular" level: the
        # shape 66 Bay Ridge items have. Asking for regular here is the recipe.
        item("j0", "Peppermint Milk Tea",
             [SIZES,
              group("Sugar Level", "sugar", [("Less S 70%", 0), ("Half S 50%", 0)]),
              group("Ice Level", "ice", [("Less Ice", 0), ("No Ice", 0), ("More Ice", 0)])],
             price=5.4),
        # The same name twice, as the promotional cross-listings do it.
        item("f6", "Matcha Milk", [SIZES, SUGAR], price=6.0, category="Matcha Series"),
        item("e5", "Matcha Milk", [SIZES, SUGAR], price=6.0, category="Milk Strike"),
        item("g7", "Green Tea Can", [], price=2.5, category="Bottled"),
        item("h8", "Peach Oolong Tea", [SIZES, SUGAR, ICE], price=5.0, sold_out=True,
             available=False),
        item("i9", "$10 Gift Card", [], price=10.0, category="Gift Cards"),
    ],
}


def store() -> menu_module.StoreMenu:
    return menu_module.StoreMenu(STUB_MENU, mapping.load())


def run_for(rows: list[dict], columns: dict | None = None) -> dict:
    """A saved-run shaped dict, as the importer would have produced it."""
    return {
        "column_map": columns if columns is not None else {
            "Name": "Name", "Drink": "Drink", "Size": "Size", "Sugar": "Sugar",
            "Ice": "Ice", "Toppings": "Toppings", "Milk": "Milk"},
        "issues": [],
        "rows": [dict({"row_number": index + 2, "person": "Someone", "quantity": 1,
                       "toppings": [], "issues": [], "ok": True, "canonical": {}},
                      **row)
                 for index, row in enumerate(rows)],
    }


def one(row: dict, columns: dict | None = None) -> dict:
    """Match a single row and hand back its `match` block."""
    return matcher.match(run_for([row], columns), store())["rows"][0]


class MenuLookupTests(unittest.TestCase):
    def setUp(self):
        self.store = store()

    def test_exact_name(self):
        found = self.store.find("Taro Slush")
        self.assertEqual(found.item.id, "a1")
        self.assertEqual(found.how, menu_module.Match.EXACT)

    def test_case_and_spacing_are_the_same_name(self):
        for text in ("taro slush", "  TARO   SLUSH ", "Taro-Slush"):
            self.assertEqual(self.store.find(text).item.id, "a1", text)

    def test_a_missing_space_is_the_same_name_not_a_guess(self):
        # "Wintermelon" for "Winter Melon" is how everybody types it.
        found = self.store.find("honeygreen tea")
        self.assertEqual(found.item.id, "b2")
        self.assertEqual(found.how, menu_module.Match.NORMALISED)

    def test_a_configured_alias_resolves(self):
        aliases = {"drinks": {"aliases": {"green tea please": "Honey Green Tea"}}}
        configured = menu_module.StoreMenu(
            STUB_MENU, mapping.Mapping({**mapping.load().data, **aliases}))
        found = configured.find("Green Tea Please")
        self.assertEqual(found.item.id, "b2")
        self.assertEqual(found.how, menu_module.Match.ALIAS)

    def test_duplicate_names_pick_the_lowest_id_every_time(self):
        # Task 1 §5: cross-listings are identical, but the choice has to be
        # stable or the same sheet builds two different carts.
        for _ in range(3):
            self.assertEqual(self.store.find("Matcha Milk").item.id, "e5")

    def test_a_near_miss_is_not_a_match(self):
        self.assertFalse(self.store.find("Taro Slushie"))
        self.assertIn("Taro Slush", self.store.suggestions("Taro Slushie"))

    def test_sold_out_and_gift_cards_are_not_drinks(self):
        for name in ("Peach Oolong Tea", "$10 Gift Card"):
            self.assertFalse(self.store.find(name), name)
            self.assertIsNotNone(self.store.unorderable(name), name)

    def test_nothing_at_all(self):
        self.assertFalse(self.store.find(""))
        self.assertEqual(self.store.suggestions(""), [])


class ItemVocabularyTests(unittest.TestCase):
    """The second arrow: a store-level label -> this item's literal."""

    def setUp(self):
        self.store = store()

    def test_a_size_label_becomes_the_items_own_literal(self):
        taro = self.store.find("Taro Slush").item
        _group, option = taro.literal("size", "Large")
        self.assertEqual(option["name"], "Large .7")
        self.assertEqual(option["price"], 0.7)

    def test_the_literal_is_never_the_label(self):
        taro = self.store.find("Taro Slush").item
        self.assertEqual(taro.labels("size"), ["Medium", "Large"])
        self.assertEqual(taro.options("size"), ["Medium", "Large .7"])

    def test_sugar_matches_on_the_percentage_not_the_wording(self):
        taro = self.store.find("Taro Slush").item
        _group, option = taro.literal("sugar", "50%")
        self.assertEqual(option["name"], "Half S 50%")
        self.assertEqual(taro.labels("sugar"), ["100%", "70%", "50%", "0%"])

    def test_the_group_name_travels_with_the_option(self):
        # Task 1 §6: the cart addresses options by group name and option name.
        taro = self.store.find("Taro Slush").item
        found_group, _option = taro.literal("toppings", "Boba")
        self.assertEqual(found_group.group_name, "Choose Topping(s)")

    def test_an_axis_the_item_hasnt_got(self):
        taro = self.store.find("Taro Slush").item
        self.assertFalse(taro.has("ice"))
        self.assertIsNone(taro.literal("ice", "Less Ice"))


class SizeMatchTests(unittest.TestCase):
    def test_the_literal_carries_the_upcharge(self):
        row = one({"drink": "Taro Slush", "canonical": {"drink": "Taro Slush",
                                                        "size": "Large"}})
        self.assertEqual(row["match"]["options"][0]["name"], "Large .7")
        self.assertEqual(row["match"]["total"], 6.65)

    def test_a_size_this_drink_hasnt_got_is_reported_with_what_it_has(self):
        row = one({"drink": "Osmanthus Oolong",
                   "canonical": {"drink": "Osmanthus Oolong", "size": "Large"}})
        problem = row["match"]["unmapped"][0]
        self.assertEqual(problem["axis"], "size")
        self.assertEqual(problem["asked"], "Large")
        self.assertIn("doesn't come in Large", problem["why"])
        self.assertEqual(row["match"]["choices"]["size"], ["Cold"])

    def test_a_required_group_is_filled_even_when_the_request_cant_be(self):
        # Size is min 1. Warning about it and then posting a line with no size
        # would just move the failure to the store, so something is ordered.
        row = one({"drink": "Osmanthus Oolong",
                   "canonical": {"drink": "Osmanthus Oolong", "size": "Large"}})
        sent = [o for o in row["match"]["options"] if o["axis"] == "size"]
        self.assertEqual(sent[0]["name"], "Cold")
        self.assertIn("it only comes Cold", row["match"]["unmapped"][0]["why"])

    def test_an_optional_group_is_left_out_rather_than_guessed(self):
        # Sugar is min 0 here, so the honest answer to "0%, which we haven't
        # got" is to send nothing and say so.
        row = one({"drink": "Peppermint Milk Tea",
                   "canonical": {"drink": "Peppermint Milk Tea", "sugar": "0%"}})
        self.assertNotIn("sugar", [o["axis"] for o in row["match"]["options"]])
        self.assertEqual(row["match"]["choices"]["sugar"], ["70%", "50%"])

    def test_a_required_size_nobody_gave_falls_back_to_the_usual(self):
        # "Choose A Size" is min 1 on 136 of 154 items: leaving it out is a
        # rejected order, not a default.
        row = one({"drink": "Taro Slush", "canonical": {"drink": "Taro Slush"}})
        sent = [o for o in row["match"]["options"] if o["axis"] == "size"]
        self.assertEqual(sent[0]["name"], "Medium")
        self.assertIn("ordered Medium", " ".join(i["message"] for i in row["issues"]))

    def test_the_fallback_is_quiet_when_the_sheet_had_no_size_column(self):
        row = one({"drink": "Taro Slush", "canonical": {"drink": "Taro Slush"}},
                  columns={"Name": "Name", "Drink": "Drink"})
        self.assertEqual([o["name"] for o in row["match"]["options"]], ["Medium"])
        self.assertEqual([i for i in row["issues"] if i["field"] == "size"], [])


class SugarAndIceMatchTests(unittest.TestCase):
    def test_the_percentage_finds_the_stores_own_wording(self):
        row = one({"drink": "Honey Green Tea",
                   "canonical": {"drink": "Honey Green Tea", "sugar": "70%",
                                 "ice": "No Ice"}})
        sent = {o["axis"]: o["name"] for o in row["match"]["options"]}
        self.assertEqual(sent["sugar"], "Less S 70%")
        self.assertEqual(sent["ice"], "No Ice")

    def test_asking_for_the_recipe_default_sends_nothing_and_is_not_a_warning(self):
        # Task 1 §5: 31 items have no Regular Sugar 100% and 66 no Regular Ice.
        # That is what the drink already is, so the right order omits it.
        row = one({"drink": "Peppermint Milk Tea",
                   "canonical": {"drink": "Peppermint Milk Tea", "sugar": "100%",
                                 "ice": "Regular Ice"}})
        self.assertEqual(row["match"]["unmapped"], [])
        self.assertEqual([o["axis"] for o in row["match"]["options"]], ["size"])
        notes = [i for i in row["issues"] if i["field"] in ("sugar", "ice")]
        self.assertEqual([note["level"] for note in notes], ["info", "info"])
        self.assertIn("how Peppermint Milk Tea comes", notes[0]["message"])

    def test_a_level_this_drink_hasnt_got_offers_the_ones_it_has(self):
        row = one({"drink": "Osmanthus Oolong",
                   "canonical": {"drink": "Osmanthus Oolong", "sugar": "0%"}})
        self.assertEqual(row["match"]["choices"]["sugar"], ["70%", "50%"])
        self.assertEqual([i["level"] for i in row["issues"] if i["field"] == "sugar"],
                         ["warning"])

    def test_a_drink_with_no_ice_group_says_so_without_warning(self):
        # A slush has no ice level. Nothing can be done about it, so it is a
        # note, not a decision to make.
        row = one({"drink": "Taro Slush",
                   "canonical": {"drink": "Taro Slush", "ice": "Less Ice"}})
        note = [i for i in row["issues"] if i["field"] == "ice"][0]
        self.assertEqual(note["level"], "info")
        self.assertIn("no ice level choice", note["message"])
        self.assertEqual(row["match"]["unmapped"], [])

    def test_a_dropped_request_counts_as_needing_attention(self):
        result = matcher.match(run_for([{
            "drink": "Taro Slush",
            "ice": "Less Ice",
            "canonical": {"drink": "Taro Slush", "ice": "Less Ice"},
        }]), store())

        self.assertEqual(result["match"]["needs_attention"], 1)

    def test_required_sugar_and_ice_are_filled_in(self):
        row = one({"drink": "Osmanthus Oolong", "canonical": {"drink": "Osmanthus Oolong"}})
        sent = {o["axis"]: o["name"] for o in row["match"]["options"]}
        self.assertEqual(sent["size"], "Cold")
        self.assertEqual(sent["sugar"], "Less S 70%")   # first it offers
        self.assertEqual(sent["ice"], "Regular Ice")    # the mapping's usual


class ToppingMatchTests(unittest.TestCase):
    def test_toppings_come_through_with_their_group(self):
        row = one({"drink": "Honey Green Tea", "toppings": ["boba"],
                   "canonical": {"drink": "Honey Green Tea", "toppings": ["Boba"]}})
        sent = [o for o in row["match"]["options"] if o["axis"] == "toppings"]
        self.assertEqual(sent[0]["group"], "Choose Topping(s)")
        self.assertEqual(sent[0]["name"], "Boba")

    def test_a_count_is_spent_here_not_dropped(self):
        row = one({"drink": "Honey Green Tea", "toppings": ["2x pudding"],
                   "canonical": {"drink": "Honey Green Tea", "toppings": ["Pudding"],
                                 "topping_quantities": {"Pudding": 2}}})
        sent = [o for o in row["match"]["options"] if o["axis"] == "toppings"][0]
        self.assertEqual(sent["quantity"], 2)
        self.assertEqual(sent["price"], 1.5)

    def test_a_topping_this_drink_doesnt_take(self):
        row = one({"drink": "Mango Slush", "toppings": ["boba"],
                   "canonical": {"drink": "Mango Slush", "toppings": ["Boba"]}})
        problem = row["match"]["unmapped"][0]
        self.assertEqual(problem["asked"], "Boba")
        self.assertIn("doesn't take Boba", problem["why"])
        # The rest of the row still orders.
        self.assertEqual(row["match"]["status"], matcher.READY)

    def test_only_the_topping_that_didnt_fit_is_dropped(self):
        row = one({"drink": "Honey Green Tea", "toppings": ["boba", "red bean"],
                   "canonical": {"drink": "Honey Green Tea",
                                 "toppings": ["Boba", "Red Bean"]}})
        sent = [o["name"] for o in row["match"]["options"] if o["axis"] == "toppings"]
        self.assertEqual(sent, ["Boba"])
        self.assertEqual([u["asked"] for u in row["match"]["unmapped"]], ["Red Bean"])

    def test_an_included_topping_is_chosen_when_the_sheet_is_silent(self):
        # min 1: the drink is defined by it, so "nothing" isn't an answer.
        row = one({"drink": "Mango Slush", "canonical": {"drink": "Mango Slush"}})
        sent = [o for o in row["match"]["options"] if o["axis"] == "toppings"][0]
        self.assertEqual(sent["name"], "Mango Jelly")   # never the "No ..." one
        self.assertIn("comes with Mango Jelly",
                      " ".join(i["message"] for i in row["issues"]))

    def test_an_included_topping_the_sheet_did_name_is_not_doubled(self):
        row = one({"drink": "Mango Slush", "toppings": ["no mango jelly"],
                   "canonical": {"drink": "Mango Slush", "toppings": ["No Mango Jelly"]}})
        sent = [o["name"] for o in row["match"]["options"] if o["axis"] == "toppings"]
        self.assertEqual(sent, ["No Mango Jelly"])


class MilkMatchTests(unittest.TestCase):
    def test_soy_milk_where_the_drink_offers_it(self):
        row = one({"drink": "Honey Green Tea",
                   "canonical": {"drink": "Honey Green Tea", "milk": "Soy Milk"}})
        sent = [o for o in row["match"]["options"] if o["axis"] == "milk"][0]
        self.assertEqual((sent["group"], sent["name"], sent["price"]),
                         ("Milk Alternative", "Soy Milk", 0.5))

    def test_a_drink_with_no_milk_alternative(self):
        row = one({"drink": "Taro Slush",
                   "canonical": {"drink": "Taro Slush", "milk": "Soy Milk"}})
        self.assertNotIn("milk", [o["axis"] for o in row["match"]["options"]])
        self.assertIn("no milk alternative choice",
                      " ".join(i["message"] for i in row["issues"]))


class UnmatchedDrinkTests(unittest.TestCase):
    def test_a_drink_nobody_can_place_waits_for_a_person(self):
        row = one({"drink": "Blueberry Explosion"})
        self.assertEqual(row["match"]["status"], matcher.NEEDS_DRINK)
        self.assertNotIn("item", row["match"])

    def test_a_near_miss_is_offered_and_not_applied(self):
        row = one({"drink": "Taro Slushie"})
        self.assertEqual(row["match"]["status"], matcher.NEEDS_DRINK)
        self.assertIn("Taro Slush", row["match"]["choices"]["drink"])
        self.assertIn("did you mean", " ".join(i["message"] for i in row["issues"]))

    def test_a_sold_out_drink_says_it_is_sold_out(self):
        row = one({"drink": "Peach Oolong Tea"})
        self.assertIn("is sold out today",
                      " ".join(i["message"] for i in row["issues"]))

    def test_a_row_the_import_already_failed_is_left_alone(self):
        row = one({"drink": "", "ok": False})
        self.assertEqual(row["match"], {"status": matcher.SKIPPED})

    def test_the_sheet_says_how_many_are_outstanding(self):
        run = matcher.match(run_for([
            {"drink": "Taro Slush", "canonical": {"drink": "Taro Slush"}},
            {"drink": "Blueberry Explosion"},
        ]), store())
        self.assertEqual(run["match"]["needs_drink"], 1)
        self.assertEqual(run["match"]["ready"], 1)
        self.assertIn("1 drink still needs picking",
                      " ".join(i["message"] for i in run["issues"]))


class RunShapeTests(unittest.TestCase):
    def setUp(self):
        self.run = matcher.match(run_for([
            {"drink": "Taro Slush", "quantity": 2,
             "canonical": {"drink": "Taro Slush", "size": "Large", "sugar": "50%"}},
            {"drink": "Honey Green Tea", "canonical": {"drink": "Honey Green Tea"}},
        ]), store())

    def test_the_totals_add_up(self):
        first = self.run["rows"][0]["match"]
        self.assertEqual(first["unit_price"], 5.95)
        self.assertEqual(first["extras_price"], 0.7)
        self.assertEqual(first["total"], 13.3)          # (5.95 + 0.70) x 2
        self.assertEqual(self.run["match"]["subtotal"], 17.8)
        self.assertEqual(self.run["match"]["drinks"], 3)

    def test_the_price_tier_is_the_tier_not_the_size(self):
        # Task 1 §6: posting "Large .7" as `size` is a 400.
        self.assertEqual(self.run["rows"][0]["match"]["price_tier"], "Regular")

    def test_matching_twice_changes_nothing(self):
        again = matcher.match(self.run, store())
        self.assertEqual(again["rows"], self.run["rows"])
        self.assertEqual(again["issues"], self.run["issues"])

    def test_it_leaves_the_import_alone(self):
        row = self.run["rows"][0]
        self.assertEqual(row["drink"], "Taro Slush")
        self.assertEqual(row["canonical"]["size"], "Large")

    def test_no_menu_at_all_is_reported_not_crashed(self):
        empty = matcher.match(run_for([{"drink": "Taro Slush"}]),
                              menu_module.StoreMenu({}, mapping.load()))
        self.assertEqual(empty["rows"][0]["match"]["status"], matcher.SKIPPED)
        self.assertIn("no menu snapshot",
                      " ".join(i["message"] for i in empty["issues"]))


class ReviewScreenTests(unittest.TestCase):
    """What the review & edit controls are built from, and what they can change."""

    def test_a_matched_row_offers_its_own_drinks_options(self):
        row = one({"drink": "Taro Slush", "canonical": {"drink": "Taro Slush"}})
        available = row["match"]["available"]
        self.assertEqual(available["size"], ["Medium", "Large"])   # labels, not literals
        self.assertEqual(available["sugar"], ["100%", "70%", "50%", "0%"])
        self.assertIn("OREO®", available["toppings"])
        # A slush has no ice level and no milk alternative: an empty list, so the
        # screen shows "not on this drink" rather than a dropdown of the store's.
        self.assertEqual(available["ice"], [])
        self.assertEqual(available["milk"], [])

    def test_the_store_vocabulary_is_the_fallback_before_a_drink_is_picked(self):
        run = matcher.match(run_for([{"drink": "Blueberry Explosion"}]), store())
        vocabulary = run["match"]["vocabulary"]
        self.assertEqual(vocabulary["size"], ["Medium", "Large", "Cold"])
        self.assertEqual(vocabulary["sugar"], ["0%", "50%", "70%", "100%"])
        self.assertIn("Soy Milk", vocabulary["milk"])
        # And the row itself has nothing to offer, so the page uses the above.
        self.assertNotIn("available", run["rows"][0]["match"])


class RowEditingTests(unittest.TestCase):
    """The edits the review screen makes, through the same door as everything else."""

    def setUp(self):
        result = importer.import_bytes(SAMPLE.read_bytes(), "sample-group-order.csv")
        self.run = result.as_dict()
        self.numbers = {row["person"]: row["row_number"] for row in self.run["rows"]}

    def row(self, run, number):
        return [row for row in run["rows"] if row["row_number"] == number][0]

    def messages(self, run, number):
        return " · ".join(issue["message"] for issue in self.row(run, number)["issues"])

    def test_the_quantity_stepper(self):
        number = self.numbers["Alice Chen"]
        updated = importer.apply_row_edit(self.run, number, {"quantity": 3})
        self.assertEqual(self.row(updated, number)["quantity"], 3)
        self.assertIn('quantity set to "3"', self.messages(updated, number))
        # And the line price follows it.
        matched = pipeline.enrich(updated)
        self.assertEqual(self.row(matched, number)["match"]["quantity"], 3)

    def test_a_quantity_nobody_meant(self):
        number = self.numbers["Alice Chen"]
        with self.assertRaises(ValueError):
            importer.apply_row_edit(self.run, number, {"quantity": 0})
        with self.assertRaises(ValueError):
            importer.apply_row_edit(self.run, number, {"quantity": "lots"})
        capped = importer.apply_row_edit(self.run, number, {"quantity": 500})
        self.assertEqual(self.row(capped, number)["quantity"], importer.MAX_QUANTITY)

    def test_naming_the_unnamed_row_clears_the_warning(self):
        number = self.numbers[""]
        self.assertIn("no name", self.messages(self.run, number))
        updated = importer.apply_row_edit(self.run, number, {"person": "Kim"})
        self.assertEqual(self.row(updated, number)["person"], "Kim")
        self.assertNotIn("no name", self.messages(updated, number))
        self.assertIn('name set to "Kim"', self.messages(updated, number))

    def test_a_topping_count_survives_editing_the_others(self):
        # The screen sends the count back with the name, so adding Boba to
        # Tomás's row doesn't quietly halve his pudding.
        number = self.numbers["Tomás"]
        updated = pipeline.enrich(importer.apply_row_edit(
            self.run, number, {"toppings": ["2x Pudding", "Red Bean", "Boba"]}))
        sent = {option["name"]: option["quantity"]
                for option in self.row(updated, number)["match"]["options"]
                if option["axis"] == "toppings"}
        self.assertEqual(sent, {"Pudding": 2, "Red Bean": 1, "Boba": 1})

    def test_editing_the_quantity_drops_what_the_import_said_about_it(self):
        number = self.numbers["Dan"]   # "Winter Melon Tea x2"
        self.assertIn("read a quantity of 2", self.messages(self.run, number))
        updated = importer.apply_row_edit(self.run, number, {"quantity": 1})
        self.assertNotIn("read a quantity of 2", self.messages(updated, number))

    def test_a_note_about_the_sheet_itself_survives_an_edit(self):
        # "we moved this value out of the wrong column" is still true afterwards.
        run = importer.import_json(
            '[{"Name": "Ana", "Drink": "Taro Slush", "Ice": "boba"}]').as_dict()
        number = run["rows"][0]["row_number"]
        self.assertIn("moved", self.messages(run, number))
        updated = importer.apply_row_edit(run, number, {"ice": "No Ice"})
        self.assertIn("moved", self.messages(updated, number))

    def test_several_fields_at_once(self):
        number = self.numbers["Sam"]
        updated = pipeline.enrich(importer.apply_row_edit(self.run, number, {
            "drink": "Winter Melon Lemonade", "sugar": "70%", "milk": "",
            "toppings": ["Mango Popping Boba"], "quantity": 2}))
        row = self.row(updated, number)
        self.assertEqual(row["match"]["status"], matcher.READY)
        self.assertEqual(row["match"]["unmapped"], [])
        self.assertEqual(row["quantity"], 2)

    def test_an_unknown_field_is_refused(self):
        number = self.numbers["Sam"]
        with self.assertRaises(ValueError):
            importer.apply_row_edit(self.run, number, {"price": "0.00"})


class MappingConfigTests(unittest.TestCase):
    def test_the_shipped_file_loads_without_complaint(self):
        config = mapping.load()
        self.assertEqual(config.problems, [])
        self.assertEqual(config.size_words["lg"], "Large")
        self.assertEqual(config.sugar_words["half sweet"], 50)

    def test_an_overlay_adds_to_a_table_without_restating_it(self):
        base = mapping.load()
        merged = mapping.Mapping(mapping._merge(
            base.data, {"size": {"words": {"ginormous": "Large"}}}))
        self.assertEqual(merged.size_words["ginormous"], "Large")
        self.assertEqual(merged.size_words["lg"], "Large")   # still there

    def test_a_missing_file_degrades_instead_of_exploding(self):
        config = mapping.load_from(Path("/nowhere/mapping.json"))
        self.assertEqual(config.size_words, {})
        self.assertTrue(config.problems)

    def test_a_stale_target_is_reported(self):
        broken = mapping.Mapping(mapping._merge(
            mapping.load().data, {"toppings": {"words": {"nubs": "Tapioca Nubs"}}}))
        problems = mapping.check(broken, {"drinks": ["Taro Slush"], "toppings": ["Boba"]})
        self.assertTrue(any("Tapioca Nubs" in problem for problem in problems))


class MappingHealthTests(unittest.TestCase):
    """Against the committed snapshot: does the vocabulary still fit the menu?"""

    def test_every_mapping_target_still_exists(self):
        problems = mapping.check()
        self.assertEqual(problems, [], "\n".join(problems))


class RealMenuTests(unittest.TestCase):
    """Against the committed snapshot — these numbers are Task 1's findings."""

    def setUp(self):
        self.store = menu_module.store_menu()

    def test_the_snapshot_loads(self):
        self.assertEqual(self.store.restaurant_id, "650c9c3cd73592bc0e0bd50a")
        self.assertGreater(len(self.store.drinks), 100)

    def test_gift_cards_are_not_drinks(self):
        self.assertNotIn("$10 Gift Card", self.store.names)

    def test_the_famous_size_literals_resolve_per_item(self):
        # The same word, three different strings, because the upcharge differs.
        seen = set()
        for name in ("Taro Slush", "Thai Tea Milk Cap", "Honey Green Tea"):
            found = self.store.find(name)
            _group, option = found.item.literal("size", "Large")
            seen.add(option["name"])
            self.assertTrue(option["name"].startswith("Large"))
        self.assertGreater(len(seen), 1)

    def test_duplicate_names_are_stable(self):
        # 14 Bay Ridge names map to two items each.
        first = menu_module.StoreMenu(menu_module.snapshot(), mapping.load())
        second = menu_module.StoreMenu(menu_module.snapshot(), mapping.load())
        self.assertEqual(first.find("Matcha Milk").item.id,
                         second.find("Matcha Milk").item.id)

    def test_a_missing_space_still_finds_the_drink(self):
        self.assertEqual(self.store.find("wintermelon lemonade").item.name,
                         "Winter Melon Lemonade")


class SampleOrderTests(unittest.TestCase):
    """The whole path, on the messy sample sheet, against the real menu."""

    def setUp(self):
        result = importer.import_bytes(SAMPLE.read_bytes(), "sample-group-order.csv")
        self.run = pipeline.enrich(result.as_dict())
        self.rows = {row["person"]: row for row in self.run["rows"]}

    def test_the_pipeline_runs_the_match_but_not_the_cart(self):
        self.assertIn("match", self.run)
        self.assertNotIn("handoff_url", self.run)

    def test_most_of_the_sheet_matched(self):
        summary = self.run["match"]
        self.assertEqual(summary["ready"], 7)
        self.assertEqual(summary["needs_drink"], 1)   # Sam's misspelling
        self.assertEqual(summary["skipped"], 1)       # Jo's empty row
        self.assertGreater(summary["subtotal"], 0)

    def test_a_row_comes_out_postable(self):
        alice = self.rows["Alice Chen"]["match"]
        self.assertEqual(alice["item"]["name"], "Taro Slush")
        self.assertEqual(alice["price_tier"], "Regular")
        sent = {option["axis"]: option["name"] for option in alice["options"]}
        self.assertEqual(sent["size"], "Large .7")
        self.assertEqual(sent["sugar"], "Half S 50%")
        self.assertEqual(sent["toppings"], "Boba")

    def test_the_misspelled_drink_is_offered_not_guessed(self):
        sam = self.rows["Sam"]["match"]
        self.assertEqual(sam["status"], matcher.NEEDS_DRINK)
        self.assertIn("Winter Melon Lemonade", sam["choices"]["drink"])

    def test_correcting_a_drink_makes_the_row_orderable(self):
        number = self.rows["Sam"]["row_number"]
        updated = pipeline.enrich(
            importer.apply_row_edit(self.run, number, {"drink": "Winter Melon Lemonade"}))
        sam = [row for row in updated["rows"] if row["row_number"] == number][0]
        self.assertEqual(sam["match"]["status"], matcher.READY)
        self.assertEqual(sam["match"]["matched_by"], "chosen")
        self.assertEqual(updated["match"]["needs_drink"], 0)

    def test_correcting_an_option_the_drink_cant_do(self):
        # Dan asked for no sugar; Winter Melon Tea starts at 30%.
        number = self.rows["Dan"]["row_number"]
        before = self.rows["Dan"]["match"]
        self.assertEqual([u["axis"] for u in before["unmapped"]], ["sugar"])
        updated = pipeline.enrich(
            importer.apply_row_edit(self.run, number, {"sugar": "30%"}))
        dan = [row for row in updated["rows"] if row["row_number"] == number][0]
        self.assertEqual(dan["match"]["unmapped"], [])
        self.assertIn("Little S 30%",
                      [option["name"] for option in dan["match"]["options"]])

    def test_a_correction_does_not_pile_up_notes(self):
        number = self.rows["Dan"]["row_number"]
        once = importer.apply_row_edit(self.run, number, {"sugar": "30%"})
        twice = pipeline.enrich(importer.apply_row_edit(once, number, {"sugar": "50%"}))
        dan = [row for row in twice["rows"] if row["row_number"] == number][0]
        edits = [i for i in dan["issues"] if i["code"] == "edited:sugar"]
        self.assertEqual(len(edits), 1)
        self.assertIn("50%", edits[0]["message"])

    def test_nothing_the_matcher_says_is_fatal(self):
        for row in self.run["rows"]:
            if row["row_number"] == self.rows["Jo"]["row_number"]:
                continue   # the empty row was already an error before matching
            self.assertTrue(row["ok"], row)


if __name__ == "__main__":
    unittest.main()
