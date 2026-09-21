"""Task 24: cart integrity verification against the read-back cart."""

from __future__ import annotations

import unittest

from app import verify


def line(row=1, person="Alice", drink="Taro Slush", qty=1, item="item-taro",
         options=(), cart_id="line-1", total=7.40):
    return {
        "row_number": row, "person": person, "drink": drink, "quantity": qty,
        "item_id": item,
        "options": [{"group": group, "axis": "toppings" if "Topping" in group else "size",
                     "name": name, "quantity": count}
                    for group, name, count in options],
        "cart_item_id": cart_id,
        "actual_total": total, "estimated_total": total,
    }


def actual(line_id="line-1", menu="item-taro", name="Taro Slush", qty="1",
           options=(), total=7.40, **extra):
    item = {
        "id": line_id, "menuitem": menu, "name": name, "quantity": qty,
        "options": [{"group_name": group, "option_name": opt, "quantity": count,
                     "name": f"{group}: {opt}"}
                    for group, opt, count in options],
        "total_price": total,
    }
    item.update(extra)
    return item


def read_back(*items, subtotal=None):
    if subtotal is None:
        subtotal = round(sum(item["total_price"] for item in items), 2)
    return {"id": "source-1", "items": list(items), "subtotal": subtotal}


SIZE = ("Choose A Size", "Large .7", 1)
BOBA = ("Choose Topping(s)", "Boba", 1)
HALF_SUGAR = ("Sugar Level", "Half S 50%", 1)


class MatchedTests(unittest.TestCase):
    def test_exact_match_with_string_quantities(self):
        expected = [line(options=(SIZE, BOBA, HALF_SUGAR))]
        order = read_back(actual(options=(SIZE, BOBA, HALF_SUGAR)))

        result = verify.verify(expected, order, order_id="source-1")

        self.assertEqual(result["status"], "matched")
        self.assertEqual(result["mismatches"], [])
        self.assertEqual(result["confirmed_drinks"], 1)
        self.assertEqual(result["source"], "source_order")

    def test_names_on_the_order_are_not_compared(self):
        # `for`/`notes` ride in our manifest; the clone drops them by design.
        expected = [line(options=(SIZE,))]
        order = read_back(actual(options=(SIZE,), **{"for": "ALICE", "notes": "for ALICE"}))

        self.assertEqual(verify.verify(expected, order)["status"], "matched")

    def test_two_people_with_the_same_drink_share_one_double_line(self):
        expected = [
            line(row=1, person="Alice", cart_id="line-1"),
            line(row=2, person="Bob", cart_id="line-2"),
        ]
        order = read_back(actual(line_id="line-9", qty="2", total=14.80))

        result = verify.verify(expected, order)

        self.assertEqual(result["status"], "matched")
        self.assertEqual(result["confirmed_drinks"], 2)

    def test_one_row_of_two_matches_two_single_lines(self):
        expected = [line(qty=2, total=14.80)]
        order = read_back(
            actual(line_id="line-1", qty="1", total=7.40),
            actual(line_id="line-2", qty="1", total=7.40))

        self.assertEqual(verify.verify(expected, order)["status"], "matched")

    def test_option_spelling_case_and_spacing_do_not_matter(self):
        expected = [line(options=(SIZE,))]
        order = read_back(actual(options=(("choose a size", "large  .7", 1),)))

        self.assertEqual(verify.verify(expected, order)["status"], "matched")


class MissingTests(unittest.TestCase):
    def test_a_line_with_nothing_in_the_cart_is_missing(self):
        expected = [
            line(row=1, person="Alice", options=(SIZE,)),
            line(row=2, person="Bob", drink="Thai Tea", item="item-thai",
                 options=(("Choose A Size", "Medium", 1),), cart_id="line-2", total=6.30),
        ]
        order = read_back(actual(options=(SIZE,)), subtotal=7.40)

        result = verify.verify(expected, order)

        self.assertEqual(result["status"], "mismatched")
        self.assertEqual(len(result["mismatches"]), 1)
        mismatch = result["mismatches"][0]
        self.assertEqual(mismatch["kind"], "missing")
        self.assertEqual(mismatch["person"], "Bob")
        self.assertEqual(mismatch["row_number"], 2)
        self.assertTrue(mismatch["absent"])
        self.assertIn("not in the cart", mismatch["detail"])

    def test_a_partially_covered_row_reports_short_quantity(self):
        expected = [line(qty=3, total=22.20)]
        order = read_back(actual(qty="1", total=7.40), subtotal=7.40)

        result = verify.verify(expected, order)

        self.assertEqual(result["status"], "mismatched")
        (mismatch,) = result["mismatches"]
        self.assertEqual(mismatch["kind"], "short_quantity")
        self.assertIn("ordered 3 but the cart has only 1", mismatch["detail"])


class ModifierTests(unittest.TestCase):
    def test_a_dropped_topping_is_flagged_with_both_sides(self):
        expected = [line(options=(SIZE, BOBA, HALF_SUGAR))]
        order = read_back(actual(options=(SIZE, HALF_SUGAR)))

        result = verify.verify(expected, order)

        self.assertEqual(result["status"], "mismatched")
        (mismatch,) = result["mismatches"]
        self.assertEqual(mismatch["kind"], "modifiers")
        self.assertEqual(mismatch["person"], "Alice")
        self.assertIn("Boba", mismatch["expected"])
        self.assertNotIn("Boba", mismatch["actual"])
        self.assertIn("fix it in the Kung Fu Tea cart before paying", mismatch["detail"])

    def test_a_substituted_size_is_flagged(self):
        expected = [line(options=(SIZE,))]
        order = read_back(actual(
            options=(("Choose A Size", "Medium", 1),)), subtotal=6.70)

        result = verify.verify(expected, order)

        self.assertEqual(result["status"], "mismatched")
        self.assertEqual(result["mismatches"][0]["kind"], "modifiers")

    def test_a_doubled_topping_count_is_flagged(self):
        expected = [line(options=(SIZE, ("Choose Topping(s)", "Boba", 2)))]
        order = read_back(actual(options=(SIZE, BOBA)))

        result = verify.verify(expected, order)

        self.assertEqual(result["status"], "mismatched")
        self.assertEqual(result["mismatches"][0]["kind"], "modifiers")

    def test_line_id_continuity_is_checked_not_assumed(self):
        # Same server line id, but the modifiers changed underneath it.
        expected = [line(options=(SIZE, BOBA))]
        order = read_back(actual(options=(SIZE,)))

        result = verify.verify(expected, order)

        self.assertEqual(result["status"], "mismatched")
        self.assertEqual(result["mismatches"][0]["kind"], "modifiers")


class ExtrasAndPriceTests(unittest.TestCase):
    def test_an_extra_line_nobody_ordered_is_flagged(self):
        expected = [line(options=(SIZE,))]
        order = read_back(
            actual(options=(SIZE,)),
            actual(line_id="line-9", menu="item-mystery", name="Mystery Drink",
                   options=(), total=5.00))

        result = verify.verify(expected, order)

        self.assertEqual(result["status"], "mismatched")
        (mismatch,) = result["mismatches"]
        self.assertEqual(mismatch["kind"], "unexpected")
        self.assertEqual(mismatch["drink"], "Mystery Drink")
        self.assertIsNone(mismatch["person"])

    def test_a_changed_line_price_is_flagged(self):
        expected = [line(options=(SIZE,), total=7.40)]
        order = read_back(actual(options=(SIZE,), total=7.90), subtotal=7.90)

        result = verify.verify(expected, order)

        self.assertEqual(result["status"], "mismatched")
        kinds = {mismatch["kind"] for mismatch in result["mismatches"]}
        self.assertIn("price", kinds)

    def test_a_drifted_subtotal_is_flagged_when_lines_agree(self):
        expected = [line(options=(SIZE,), total=7.40)]
        order = read_back(actual(options=(SIZE,), total=7.40), subtotal=8.15)

        result = verify.verify(expected, order)

        self.assertEqual(result["status"], "mismatched")
        (mismatch,) = result["mismatches"]
        self.assertEqual(mismatch["kind"], "totals")
        self.assertIn("$8.15", mismatch["detail"])

    def test_rounding_noise_is_not_drift(self):
        expected = [line(options=(SIZE,), total=7.40)]
        order = read_back(actual(options=(SIZE,), total=7.40), subtotal=7.41)

        self.assertEqual(verify.verify(expected, order)["status"], "matched")


class UnverifiedTests(unittest.TestCase):
    def test_no_order_is_unverified_never_matched(self):
        for bad in (None, {}, {"id": "source-1"}, {"items": None}, {"items": {}}):
            with self.subTest(bad=bad):
                result = verify.verify([line()], bad)
                self.assertEqual(result["status"], "unverified")
                self.assertIn("could not be read back", result["note"])

    def test_non_dict_items_are_ignored_not_fatal(self):
        real = actual(options=(SIZE,))
        order = {"id": "source-1", "items": ["oops", real], "subtotal": 7.40}
        expected = [line(options=(SIZE,))]

        # "oops" is skipped; the real line still verifies.
        self.assertEqual(verify.verify(expected, order)["status"], "matched")


if __name__ == "__main__":
    unittest.main()
