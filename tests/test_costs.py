"""Payment splitting stays exact to the cent across menu and cart prices."""

from __future__ import annotations

import unittest

from app import costs


class CostBreakdownTests(unittest.TestCase):
    def test_shared_costs_are_proportional_and_reconcile_to_the_cent(self):
        split = costs.breakdown([
            {"person": "Alice", "quantity": 1, "amount": 10},
            {"person": "Bob", "quantity": 2, "amount": 20},
        ], totals={"tax": 2.01, "fees": {"service_fee": 1}, "total": 33.01},
            source="cart_total", estimated=False)

        self.assertEqual(split["shared_total"], 3.01)
        self.assertEqual(split["by_person"], [
            {"person": "Alice", "drinks": 1, "lines": 1, "subtotal": 10.0,
             "shared": 1.0, "total": 11.0},
            {"person": "Bob", "drinks": 2, "lines": 1, "subtotal": 20.0,
             "shared": 2.01, "total": 22.01},
        ])
        self.assertAlmostEqual(sum(person["total"] for person in split["by_person"]), 33.01)

    def test_final_paid_amount_can_include_tip_and_a_discount(self):
        cart = {
            "added": [
                {"person": "Alice", "quantity": 1, "actual_total": 10},
                {"person": "Bob", "quantity": 1, "actual_total": 10},
            ],
            "totals": {"subtotal": 20, "tax": 2, "total": 22},
        }
        split = costs.from_cart(cart, tip=3, total_paid=24)

        self.assertEqual(split["tip"], 3.0)
        self.assertEqual(split["adjustment"], -1.0)
        self.assertEqual([entry["total"] for entry in split["by_person"]], [12.0, 12.0])

    def test_unpriced_drinks_are_counted_but_not_charged(self):
        split = costs.breakdown([
            {"person": "Alice", "quantity": 2, "amount": None},
        ])
        self.assertEqual(split["unpriced_drinks"], 2)
        self.assertEqual(split["total"], 0.0)
        self.assertEqual(split["by_person"], [])


if __name__ == "__main__":
    unittest.main()
