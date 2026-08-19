"""Tests for app/options.py — the user's words -> the store's option set.

Most of these run against a stub vocabulary so they say what they mean and do
not move when the menu snapshot is refreshed. The ones that must see the real
thing (StoreVocabularyTests, SampleFileTests) say so.
"""

from __future__ import annotations

import csv
import io
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import importer, options, schema, template  # noqa: E402
from tests.xlsx_fixture import build_xlsx  # noqa: E402

SAMPLE = Path(__file__).resolve().parent.parent / "data" / "sample-group-order.csv"

# A stand-in for one store's vocabulary, using the real Bay Ridge spellings —
# including the inconsistent sugar names and the "No Mango Jelly" removal option.
STUB = {
    "store": "Test Store",
    "restaurant_id": "test",
    "sizes": ["Medium", "Large", "Hot", "Cold"],
    "sugar": ["Regular Sugar 100%", "Less S 70%", "Half S 50%", "Little S 30%",
              "No S 0%", "Extra S 120%"],
    "ice": ["Less Ice", "More Ice", "No Ice", "Regular Ice"],
    "toppings": ["Boba", "Pudding", "OREO®", "Mango Jelly", "No Mango Jelly",
                 "Aloe Jelly", "Herbal Jelly", "Milk Cap", "Red Bean", "Crystal Boba",
                 "Chia Seeds", "Espresso Shot", "Protein Add On", "Nata Jelly",
                 "Brown Sugar Wow Boba", "Strawberry Popping Boba",
                 "Coffee Popping Boba", "Mango Popping Boba", "Strawberry Milk Cap",
                 "Matcha Milk Cap"],
    "milk": ["Soy Milk"],
    "drinks": ["Taro Slush", "Thai Tea Milk Cap", "Winter Melon Tea", "Matcha Milk",
               "Honey Green Tea"],
}


def stub() -> options.StoreOptions:
    return options.StoreOptions(STUB)


def row(**fields) -> schema.OrderRow:
    return schema.OrderRow(row_number=fields.pop("row_number", 2), **fields)


class StoreVocabularyTests(unittest.TestCase):
    """Against the committed snapshot — the numbers here are Task 1's findings."""

    def setUp(self):
        self.store = options.store_options()

    def test_sizes_collapse_to_four_labels(self):
        self.assertEqual(sorted(self.store.sizes), ["Cold", "Hot", "Large", "Medium"])

    def test_size_literals_are_not_the_vocabulary(self):
        # 16 literals like "Large .7" exist on the menu; none is a canonical label.
        for literal in ("Large .7", "Large 1", "Large.3", "Hot .5"):
            self.assertNotIn(literal, self.store.sizes)

    def test_sugar_is_percentages_not_the_stores_wording(self):
        self.assertEqual(self.store.sugar_levels, [0, 30, 50, 70, 100, 120])

    def test_ice_and_milk(self):
        self.assertEqual(sorted(self.store.ice),
                         ["Less Ice", "More Ice", "No Ice", "Regular Ice"])
        self.assertEqual(self.store.milk, ["Soy Milk"])

    def test_drinks_came_through(self):
        self.assertIn("Taro Slush", self.store.drinks)
        self.assertGreater(len(self.store.drinks), 100)


class SizeTests(unittest.TestCase):
    def setUp(self):
        self.store = stub()

    def test_casing_and_whitespace(self):
        for text in ("Large", "large", "  LARGE  ", "lArGe"):
            resolved = self.store.size(text)
            self.assertEqual(resolved.value, "Large", text)
            self.assertEqual(resolved.status, options.OK)

    def test_abbreviations(self):
        self.assertEqual(self.store.size("lg").value, "Large")
        self.assertEqual(self.store.size("L").value, "Large")
        self.assertEqual(self.store.size("m").value, "Medium")
        self.assertEqual(self.store.size("med").value, "Medium")

    def test_regular_means_medium(self):
        self.assertEqual(self.store.size("regular").value, "Medium")

    def test_size_inside_a_phrase(self):
        self.assertEqual(self.store.size("large cup please").value, "Large")

    def test_a_size_the_store_lacks_maps_to_the_nearest(self):
        resolved = self.store.size("small")
        self.assertEqual(resolved.value, "Medium")
        self.assertEqual(resolved.status, options.ASSUMED)
        self.assertIn("smallest is Medium", resolved.message)

    def test_hot_is_a_size_taken_from_a_temperature_column(self):
        resolved = self.store.size("", temperature="Hot")
        self.assertEqual(resolved.value, "Hot")
        self.assertEqual(resolved.status, options.ASSUMED)

    def test_iced_is_the_default_not_the_cold_size(self):
        # Only a handful of items have a Cold option; iced drinks are iced
        # already, so the right order sends no size modifier at all. That holds
        # whichever column the word turns up in, and it is never a warning.
        for resolved in (self.store.size("", temperature="Iced"),
                         self.store.size("iced")):
            self.assertEqual(resolved.value, "")
            self.assertEqual(resolved.status, options.BLANK)
            self.assertEqual(resolved.message, "")
        # "Cold" written out is a real option, though.
        self.assertEqual(self.store.size("cold").value, "Cold")

    def test_blank_is_not_a_problem(self):
        resolved = self.store.size("")
        self.assertEqual(resolved.status, options.BLANK)
        self.assertEqual(resolved.message, "")

    def test_nonsense_is_reported_with_the_real_options(self):
        resolved = self.store.size("gigantic")
        self.assertEqual(resolved.value, "")
        self.assertEqual(resolved.status, options.UNKNOWN)
        self.assertIn("Medium", resolved.message)


class SugarTests(unittest.TestCase):
    def setUp(self):
        self.store = stub()

    def test_percentages(self):
        for text, expected in (("50%", "50%"), ("50", "50%"), (" 0% ", "0%"),
                               ("100 %", "100%"), ("30% sugar", "30%")):
            self.assertEqual(self.store.sugar(text).value, expected, text)

    def test_words(self):
        for text, expected in (
            ("no sugar", "0%"), ("unsweetened", "0%"), ("none", "0%"),
            ("light", "30%"), ("little sugar", "30%"),
            ("half sweet", "50%"), ("half", "50%"),
            ("less sweet", "70%"), ("less", "70%"),
            ("regular", "100%"), ("normal sweetness", "100%"),
            ("extra sweet", "120%"),
        ):
            self.assertEqual(self.store.sugar(text).value, expected, text)

    def test_the_stores_own_wording_round_trips(self):
        # People copy the option names off the menu board.
        self.assertEqual(self.store.sugar("Less S 70%").value, "70%")
        self.assertEqual(self.store.sugar("Regular Sugar 100%").value, "100%")

    def test_a_level_the_store_doesnt_offer_is_flagged_not_snapped(self):
        resolved = self.store.sugar("25%")
        self.assertEqual(resolved.value, "")
        self.assertEqual(resolved.status, options.UNKNOWN)
        self.assertIn("30%", resolved.message)

    def test_blank(self):
        self.assertEqual(self.store.sugar("   ").status, options.BLANK)


class IceTests(unittest.TestCase):
    def setUp(self):
        self.store = stub()

    def test_levels(self):
        for text, expected in (
            ("no ice", "No Ice"), ("none", "No Ice"), ("0", "No Ice"),
            ("without ice", "No Ice"), ("NO ICE", "No Ice"),
            ("less", "Less Ice"), ("less ice", "Less Ice"), ("light ice", "Less Ice"),
            ("regular", "Regular Ice"), ("normal", "Regular Ice"),
            ("extra", "More Ice"), ("more ice", "More Ice"), ("lots of ice", "More Ice"),
        ):
            self.assertEqual(self.store.ice_level(text).value, expected, text)

    def test_the_word_ice_alone_is_no_choice(self):
        self.assertEqual(self.store.ice_level("ice").status, options.BLANK)

    def test_nonsense(self):
        resolved = self.store.ice_level("room temperature")
        self.assertEqual(resolved.status, options.UNKNOWN)
        self.assertIn("No Ice", resolved.message)


class MilkTests(unittest.TestCase):
    def setUp(self):
        self.store = stub()

    def test_soy(self):
        for text in ("soy", "Soy Milk", "soya", "soy milk please"):
            self.assertEqual(self.store.milk_option(text).value, "Soy Milk", text)

    def test_milk_the_store_doesnt_stock_is_named_not_swallowed(self):
        for text in ("oat milk", "almond", "lactose free"):
            resolved = self.store.milk_option(text)
            self.assertEqual(resolved.value, "", text)
            self.assertEqual(resolved.status, options.UNAVAILABLE, text)
            self.assertIn("Soy Milk", resolved.message)

    def test_asking_for_ordinary_milk_is_not_a_problem(self):
        # "none", "regular", "whole milk" in a Milk column all mean "don't
        # substitute anything" — the absence of a modifier, not an impossible ask.
        for text in ("none", "no", "regular", "whole milk", "2%", "skim"):
            resolved = self.store.milk_option(text)
            self.assertEqual(resolved.status, options.BLANK, text)
            self.assertEqual(resolved.message, "", text)

    def test_blank(self):
        self.assertEqual(self.store.milk_option("").status, options.BLANK)


class ToppingTests(unittest.TestCase):
    def setUp(self):
        self.store = stub()

    def test_exact_and_casing(self):
        self.assertEqual(self.store.topping("boba").value, "Boba")
        self.assertEqual(self.store.topping("  PUDDING ").value, "Pudding")

    def test_punctuation_in_the_menu_name_does_not_block_a_match(self):
        # The store writes it "OREO®"; nobody types the ®.
        self.assertEqual(self.store.topping("oreo").value, "OREO®")
        self.assertEqual(self.store.topping("Oreos").value, "OREO®")

    def test_common_names_for_the_same_thing(self):
        for text in ("pearls", "tapioca", "tapioca pearls", "black pearls", "bubbles"):
            self.assertEqual(self.store.topping(text).value, "Boba", text)
        self.assertEqual(self.store.topping("grass jelly").value, "Herbal Jelly")
        self.assertEqual(self.store.topping("aloe vera").value, "Aloe Jelly")
        self.assertEqual(self.store.topping("cheese foam").value, "Milk Cap")

    def test_a_count_on_the_topping_does_not_stop_the_match(self):
        for text in ("2x pudding", "x2 pudding", "2 pudding", "double pudding",
                     "triple pudding", "extra pudding", "pudding x2"):
            self.assertEqual(self.store.topping(text).value, "Pudding", text)

    def test_stripping_a_count_never_costs_a_synonym(self):
        # "extra shot" is a name, not a count plus "shot" — so the text is
        # matched as typed as well as with the count removed.
        self.assertEqual(self.store.topping("extra shot").value, "Espresso Shot")

    def test_an_explicit_removal_is_honoured_when_asked_for_exactly(self):
        self.assertEqual(self.store.topping("no mango jelly").value, "No Mango Jelly")

    def test_a_family_name_is_ambiguous_and_says_so(self):
        resolved = self.store.topping("popping boba")
        self.assertEqual(resolved.value, "")
        self.assertEqual(resolved.status, options.AMBIGUOUS)
        self.assertIn("Strawberry Popping Boba", resolved.message)

    def test_a_removal_option_never_wins_a_fuzzy_match(self):
        # "No Mango Jelly" is orderable, so it is in the vocabulary — but
        # matching "mango jelly" to it would invert the request.
        self.assertEqual(self.store.topping("mango jelly").value, "Mango Jelly")

    def test_typos_are_caught(self):
        resolved = self.store.topping("puding")
        self.assertEqual(resolved.value, "Pudding")
        self.assertEqual(resolved.status, options.ASSUMED)

    def test_something_the_store_doesnt_have(self):
        resolved = self.store.topping("rainbow sprinkles")
        self.assertEqual(resolved.value, "")
        self.assertEqual(resolved.status, options.UNKNOWN)


class DrinkTests(unittest.TestCase):
    def setUp(self):
        self.store = stub()

    def test_exact_ignoring_case_and_spacing(self):
        for text in ("Taro Slush", "taro slush", "  TARO   SLUSH "):
            self.assertEqual(self.store.drink(text).value, "Taro Slush", text)

    def test_a_near_miss_is_suggested_but_never_chosen(self):
        resolved = self.store.drink("Winter Mellon Tea")
        self.assertEqual(resolved.value, "")  # ordering the wrong drink costs money
        self.assertEqual(resolved.status, options.UNKNOWN)
        self.assertIn("Winter Melon Tea", resolved.message)

    def test_something_else_entirely(self):
        resolved = self.store.drink("flat white")
        self.assertEqual(resolved.status, options.UNKNOWN)
        self.assertIn("flat white", resolved.message)


class DrinkSuggestionTests(unittest.TestCase):
    """Nothing is auto-corrected, so the suggestions have to be worth reading."""

    def setUp(self):
        self.store = stub()

    def test_a_typo_puts_the_right_drink_first(self):
        self.assertEqual(self.store.drink_suggestions("Winter Mellon Tea")[0],
                         "Winter Melon Tea")
        self.assertEqual(self.store.drink_suggestions("taro slushie")[0], "Taro Slush")

    def test_half_a_name_finds_the_whole_one(self):
        self.assertIn("Matcha Milk", self.store.drink_suggestions("matcha"))
        self.assertIn("Taro Slush", self.store.drink_suggestions("taro"))

    def test_nothing_like_it_suggests_nothing(self):
        self.assertEqual(self.store.drink_suggestions("flat white"), [])
        self.assertEqual(self.store.drink_suggestions(""), [])

    def test_the_order_is_stable(self):
        first = self.store.drink_suggestions("winter melon")
        self.assertEqual(first, self.store.drink_suggestions("winter melon"))

    def test_suggestions_land_on_the_row_but_not_in_canonical(self):
        order = row(drink="Winter Mellon Tea")
        canonical = options.resolve_row(order, self.store)
        self.assertEqual(canonical["drink"], "")  # still not chosen for them
        self.assertEqual(order.suggestions["drink"][0], "Winter Melon Tea")

    def test_a_row_with_no_drink_has_nothing_to_suggest_from(self):
        order = row(drink="")
        options.resolve_row(order, self.store)
        self.assertEqual(order.suggestions, {})

    def test_a_matched_drink_needs_no_suggestions(self):
        order = row(drink="taro slush")
        options.resolve_row(order, self.store)
        self.assertEqual(order.suggestions, {})


class DrinkSearchTests(unittest.TestCase):
    """The type-ahead behind the "pick a drink" box. Real menu, real names."""

    def setUp(self):
        self.store = options.store_options()

    def test_a_prefix_reaches_the_drink(self):
        # No similarity score would rank "Taro Slush" highly against "ta";
        # a prefix has to count for more than a character ratio here.
        for query in ("ta", "tar", "taro"):
            self.assertIn("Taro Slush", self.store.search_drinks(query), query)

    def test_a_word_from_the_middle_finds_it(self):
        results = self.store.search_drinks("melon")
        self.assertIn("Winter Melon Tea", results)
        self.assertTrue(all("melon" in name.lower() for name in results))

    def test_initials_of_each_word(self):
        self.assertIn("Honey Oolong Tea", self.store.search_drinks("hon oo"))

    def test_only_a_few_come_back(self):
        # The whole point: a handful to pick from, not the 135-item menu.
        for query in ("tea", "milk", "a", "s"):
            self.assertLessEqual(len(self.store.search_drinks(query)), 6, query)
        self.assertEqual(len(self.store.search_drinks("tea", limit=3)), 3)

    def test_the_best_match_is_first(self):
        self.assertEqual(self.store.search_drinks("taro slush")[0], "Taro Slush")
        self.assertEqual(self.store.search_drinks("matcha milk")[0], "Matcha Milk")

    def test_typos_still_land(self):
        self.assertIn("Taro Slush", self.store.search_drinks("slushie"))

    def test_nothing_typed_and_nothing_like_it(self):
        self.assertEqual(self.store.search_drinks(""), [])
        self.assertEqual(self.store.search_drinks("   "), [])
        self.assertEqual(self.store.search_drinks("xyzzy"), [])
        # "boba" is a topping at this store, not a drink — so no drink matches.
        self.assertEqual(self.store.search_drinks("boba"), [])

    def test_results_are_real_menu_names(self):
        for name in self.store.search_drinks("tea"):
            self.assertIn(name, self.store.drinks)

    def test_the_order_is_stable(self):
        self.assertEqual(self.store.search_drinks("oolong"),
                         self.store.search_drinks("oolong"))


class RowEditTests(unittest.TestCase):
    """Accepting a suggestion, and filling in a row that had no drink."""

    def setUp(self):
        result = importer.import_bytes(SAMPLE.read_bytes(), "sample-group-order.csv")
        self.run = result.as_dict()
        self.numbers = {r["person"]: r["row_number"] for r in self.run["rows"]}

    def find(self, run, person):
        return [r for r in run["rows"] if r["person"] == person][0]

    def test_accepting_a_suggestion_resolves_the_row(self):
        updated = importer.apply_row_edit(self.run, self.numbers["Sam"],
                                          {"drink": "Winter Melon Lemonade"})
        sam = self.find(updated, "Sam")
        self.assertEqual(sam["canonical"]["drink"], "Winter Melon Lemonade")
        self.assertEqual(sam["drink"], "Winter Melon Lemonade")
        self.assertEqual(sam["suggestions"], {})

    def test_the_correction_says_what_it_replaced(self):
        updated = importer.apply_row_edit(self.run, self.numbers["Sam"],
                                          {"drink": "Winter Melon Lemonade"})
        notes = [i["message"] for i in self.find(updated, "Sam")["issues"]
                 if i["code"] == "edited:drink"]
        self.assertEqual(len(notes), 1)
        self.assertIn("Wintermelon Lemonaid", notes[0])

    def test_filling_in_a_blank_row_makes_it_orderable(self):
        blank = self.find(self.run, "Jo")
        self.assertFalse(blank["ok"])
        updated = importer.apply_row_edit(self.run, self.numbers["Jo"],
                                          {"drink": "Taro Slush"})
        jo = self.find(updated, "Jo")
        self.assertTrue(jo["ok"])
        self.assertEqual(jo["canonical"]["drink"], "Taro Slush")
        # Its other columns were always fine and are resolved now too.
        self.assertEqual(jo["canonical"]["size"], "Medium")
        self.assertEqual(jo["canonical"]["toppings"], ["Boba"])

    def test_the_totals_follow_the_correction(self):
        self.assertEqual(self.run["stats"]["errors"], 1)
        updated = importer.apply_row_edit(self.run, self.numbers["Jo"],
                                          {"drink": "Taro Slush"})
        self.assertEqual(updated["stats"]["errors"], 0)
        self.assertEqual(updated["stats"]["drinks"], self.run["stats"]["drinks"] + 1)

    def test_the_sheet_level_tally_disappears_when_the_last_one_is_fixed(self):
        codes = [i.get("code") for i in self.run["issues"]]
        self.assertIn("option:unmatched-drinks", codes)
        updated = importer.apply_row_edit(self.run, self.numbers["Sam"],
                                          {"drink": "Winter Melon Lemonade"})
        self.assertNotIn("option:unmatched-drinks",
                         [i.get("code") for i in updated["issues"]])

    def test_correcting_twice_does_not_pile_up_notes(self):
        once = importer.apply_row_edit(self.run, self.numbers["Sam"],
                                       {"drink": "Winter Melon Tea"})
        twice = importer.apply_row_edit(once, self.numbers["Sam"],
                                        {"drink": "Taro Slush"})
        sam = self.find(twice, "Sam")
        self.assertEqual(sam["canonical"]["drink"], "Taro Slush")
        for code in ("edited:drink", "option:unknown:milk"):
            self.assertLessEqual(sum(1 for i in sam["issues"] if i["code"] == code), 1)

    def test_the_rest_of_the_row_is_re_resolved_not_dropped(self):
        updated = importer.apply_row_edit(self.run, self.numbers["Sam"],
                                          {"drink": "Winter Melon Lemonade"})
        messages = " ".join(i["message"] for i in self.find(updated, "Sam")["issues"])
        self.assertIn("oat milk", messages)   # still flagged
        self.assertIn("25%", messages)

    def test_a_correction_that_is_also_wrong_is_flagged_again(self):
        updated = importer.apply_row_edit(self.run, self.numbers["Sam"],
                                          {"drink": "Still Not A Drink"})
        sam = self.find(updated, "Sam")
        self.assertEqual(sam["canonical"]["drink"], "")
        self.assertIn("option:unmatched-drinks",
                      [i.get("code") for i in updated["issues"]])

    def test_the_other_rows_are_untouched(self):
        before = self.find(self.run, "Alice Chen")
        updated = importer.apply_row_edit(self.run, self.numbers["Sam"],
                                          {"drink": "Winter Melon Lemonade"})
        self.assertEqual(self.find(updated, "Alice Chen"), before)

    def test_an_unknown_row_and_an_empty_choice_are_refused(self):
        with self.assertRaises(importer.RowNotFound):
            importer.apply_row_edit(self.run, 9999, {"drink": "Taro Slush"})
        with self.assertRaises(ValueError):
            importer.apply_row_edit(self.run, self.numbers["Sam"], {"drink": "   "})


class ResolveRowTests(unittest.TestCase):
    def setUp(self):
        self.store = stub()

    def test_a_messy_row_resolves_and_keeps_the_original_text(self):
        order = row(drink=" taro slush ", size="LG", sugar="half sweet",
                    ice="no ice", toppings=["oreo", "2x pudding"], milk="soy")
        canonical = options.resolve_row(order, self.store)
        self.assertEqual(canonical, {
            "drink": "Taro Slush", "size": "Large", "sugar": "50%", "ice": "No Ice",
            "toppings": ["OREO®", "Pudding"], "milk": "Soy Milk",
            # "2x pudding" is two puddings. Only counts above one are recorded.
            "topping_quantities": {"Pudding": 2},
        })
        # The raw fields are untouched, so a wrong guess stays recoverable.
        self.assertEqual(order.size, "LG")
        self.assertEqual(order.toppings, ["oreo", "2x pudding"])

    def test_a_clean_row_produces_no_notes(self):
        order = row(drink="Taro Slush", size="Large", sugar="50%", ice="Less Ice",
                    toppings=["Boba"])
        options.resolve_row(order, self.store)
        self.assertEqual(order.issues, [])

    def test_blanks_are_silent_and_mean_the_store_default(self):
        order = row(drink="Taro Slush")
        canonical = options.resolve_row(order, self.store)
        self.assertEqual(order.issues, [])
        self.assertEqual(canonical["size"], "")
        self.assertEqual(canonical["toppings"], [])

    def test_problems_are_flagged_without_making_the_row_unorderable(self):
        order = row(drink="Taro Slush", sugar="25%", milk="oat milk")
        options.resolve_row(order, self.store)
        self.assertTrue(order.ok)  # still going in the cart
        levels = {issue.level for issue in order.issues}
        self.assertEqual(levels, {"warning"})
        self.assertEqual(len(order.issues), 2)

    def test_the_same_topping_twice_is_ordered_once(self):
        order = row(drink="Taro Slush", toppings=["boba", "Boba", "pearls"])
        canonical = options.resolve_row(order, self.store)
        self.assertEqual(canonical["toppings"], ["Boba"])

    def test_issues_carry_the_row_number_and_field(self):
        order = row(row_number=7, drink="Taro Slush", size="gigantic")
        options.resolve_row(order, self.store)
        issue = order.issues[0]
        self.assertEqual(issue.row, 7)
        self.assertEqual(issue.field, "size")
        self.assertTrue(issue.code.startswith("option:"))


class AnnotateTests(unittest.TestCase):
    def test_unmatched_drinks_are_summarised_once(self):
        rows = [row(row_number=n, drink="Nonexistent Tea") for n in (2, 3, 4)]
        issues = options.annotate(rows, stub())
        summary = [i for i in issues if i.code == "option:unmatched-drinks"]
        self.assertEqual(len(summary), 1)
        self.assertIn("3 drinks", summary[0].message)

    def test_all_matching_means_no_summary(self):
        rows = [row(drink="Taro Slush"), row(drink="Matcha Milk")]
        self.assertEqual(options.annotate(rows, stub()), [])

    def test_without_a_snapshot_nothing_crashes(self):
        empty = options.StoreOptions({})
        order = row(drink="Taro Slush", size="Large", sugar="50%", toppings=["boba"])
        issues = options.annotate([order], empty)
        self.assertEqual(order.canonical["size"], "")
        self.assertTrue(any("no menu snapshot" in issue.message for issue in issues))


class SampleFileTests(unittest.TestCase):
    """The realistic sample in data/ — a real group order, filled in by hand."""

    def setUp(self):
        self.result = importer.import_bytes(SAMPLE.read_bytes(), "sample-group-order.csv")
        self.by_person = {r.person: r for r in self.result.rows}

    def test_it_reads_at_all(self):
        self.assertTrue(self.result.ok)
        self.assertEqual(self.result.stats["rows"], 9)
        self.assertEqual(self.result.stats["drinks"], 9)  # Dan ordered two

    def test_the_title_rows_above_the_header_are_skipped(self):
        self.assertEqual(self.result.column_map["Drink"], "What drink do you want?")

    def test_messy_values_come_out_in_the_stores_words(self):
        alice = self.by_person["Alice Chen"]
        self.assertEqual(alice.canonical, {
            "drink": "Taro Slush", "size": "Large", "sugar": "50%",
            "ice": "Less Ice", "toppings": ["Boba"], "milk": "",
        })
        bob = self.by_person["bob"]
        self.assertEqual(bob.drink, "thai tea milk cap")  # as typed
        self.assertEqual(bob.canonical["drink"], "Thai Tea Milk Cap")
        self.assertEqual(bob.canonical["size"], "Large")
        self.assertEqual(bob.canonical["toppings"], ["OREO®", "Pudding"])

    def test_an_inline_quantity_is_picked_up(self):
        dan = self.by_person["Dan"]
        self.assertEqual(dan.quantity, 2)
        self.assertEqual(dan.canonical["drink"], "Winter Melon Tea")
        self.assertEqual(dan.canonical["sugar"], "0%")

    def test_the_row_with_no_drink_is_flagged_and_kept(self):
        jo = self.by_person["Jo"]
        self.assertFalse(jo.ok)
        self.assertIn(jo, self.result.rows)  # flagged, not dropped
        self.assertEqual(self.result.stats["errors"], 1)

    def test_the_row_nobody_can_order_still_reaches_the_preview(self):
        sam = self.by_person["Sam"]
        messages = " ".join(issue.message for issue in sam.issues)
        self.assertIn("25%", messages)          # sugar level the store lacks
        self.assertIn("oat milk", messages)     # milk the store lacks
        self.assertIn("could be", messages)     # ambiguous popping boba
        self.assertIn("Wintermelon Lemonaid", messages)
        self.assertTrue(sam.ok)  # none of that stops the drink being ordered

    def test_unnamed_row_is_a_warning_not_a_failure(self):
        unnamed = [r for r in self.result.rows if not r.person][0]
        self.assertTrue(unnamed.ok)
        self.assertEqual(unnamed.canonical["drink"], "Honey Milk Tea")

    def test_the_same_sheet_as_xlsx_gives_the_same_answer(self):
        rows = list(csv.reader(io.StringIO(SAMPLE.read_text(encoding="utf-8"))))
        workbook = build_xlsx({"Orders": rows})
        from_excel = importer.import_bytes(workbook, "sample-group-order.xlsx")
        self.assertEqual([r.canonical for r in from_excel.rows],
                         [r.canonical for r in self.result.rows])
        self.assertEqual(from_excel.stats["drinks"], self.result.stats["drinks"])

    def test_every_row_is_json_serialisable_with_its_canonical_block(self):
        payload = self.result.as_dict()
        for entry in payload["rows"]:
            self.assertIn("canonical", entry)
            # topping_quantities is the one optional key: present only when
            # somebody asked for two of something.
            self.assertEqual(set(entry["canonical"]) - {"topping_quantities"},
                             {"drink", "size", "sugar", "ice", "toppings", "milk"})


class TemplateStillMatchesTests(unittest.TestCase):
    def test_the_template_resolves_cleanly_against_its_own_store(self):
        """Whatever the template tells people to type must survive parsing."""
        result = importer.import_bytes(template.template_csv().encode(), "template.csv")
        self.assertTrue(result.ok)
        for order in result.rows:
            problems = [i for i in order.issues if i.level == "warning"]
            self.assertEqual(problems, [], f"row {order.row_number}: {problems}")
            self.assertTrue(order.canonical["drink"], order.drink)


if __name__ == "__main__":
    unittest.main()
