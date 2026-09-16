"""Behavior tests for participant-side Surprise Me selections."""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from app import menu as menu_module


ROOT = Path(__file__).resolve().parent.parent
RANDOMIZER_JS = ROOT / "app" / "static" / "randomizer.js"
JSC_CANDIDATES = (
    "jsc",
    "/System/Library/Frameworks/JavaScriptCore.framework/Versions/A/Helpers/jsc",
)


def find_engine() -> str | None:
    for candidate in JSC_CANDIDATES:
        found = shutil.which(candidate) or (candidate if Path(candidate).exists() else None)
        if found:
            return str(found)
    return None


ENGINE = find_engine()


@unittest.skipUnless(ENGINE, "no JavaScript engine on this machine")
class RandomizerScriptTests(unittest.TestCase):
    def drive(self, script: str) -> dict:
        with tempfile.TemporaryDirectory() as scratch:
            driver = Path(scratch) / "driver.js"
            driver.write_text(f"""
var window = this;
load({str(RANDOMIZER_JS)!r});
function report(value) {{ print(JSON.stringify(value)); }}
{script}
""", encoding="utf-8")
            done = subprocess.run(
                [ENGINE, str(driver)], capture_output=True, text=True, timeout=30)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        lines = [line for line in done.stdout.splitlines() if line.startswith("{")]
        self.assertTrue(lines, done.stdout + done.stderr)
        return json.loads(lines[-1])

    def test_every_roll_obeys_the_items_group_constraints(self):
        result = self.drive("""
var menu = {items: [{
  name: 'Tea', category: 'Milk Tea', option_groups: [
    {key: 'size', axis: 'size', required: true, min: 1, max: 1, multiselect: false,
     options: [{label: 'Medium'}, {label: 'Large'}]},
    {key: 'ice', axis: 'ice', required: false, min: 0, max: 1, multiselect: false,
     options: [{label: 'Less Ice'}, {label: 'No Ice'}]},
    {key: 'toppings', axis: 'toppings', required: false, min: 0, max: null, multiselect: true,
     options: [{label: 'Boba'}, {label: 'Pudding'}, {label: 'Jelly'}]},
    {key: 'required-extras', axis: 'other', required: true, min: 3, max: null, multiselect: true,
     options: [{label: 'A'}, {label: 'B'}, {label: 'C'}, {label: 'D'}]}
  ]
}]};
var state = 17;
function seeded() { state = (state * 48271) % 2147483647; return state / 2147483647; }
var valid = true;
var largestOptionalToppingSet = 0;
var smallestOptionalToppingSet = 99;
for (var index = 0; index < 200; index++) {
  var result = BobaRandomizer.pick(menu, '', seeded);
  valid = valid && BobaRandomizer.isValidSelection(result.item, result.selections);
  largestOptionalToppingSet = Math.max(largestOptionalToppingSet, result.selections.toppings.length);
  smallestOptionalToppingSet = Math.min(smallestOptionalToppingSet, result.selections.toppings.length);
}
report({valid: valid, largestOptionalToppingSet: largestOptionalToppingSet,
        smallestOptionalToppingSet: smallestOptionalToppingSet});
""")
        self.assertTrue(result["valid"])
        self.assertGreaterEqual(result["smallestOptionalToppingSet"], 1)
        self.assertLessEqual(result["largestOptionalToppingSet"], 2)

    def test_category_filter_is_respected_and_food_is_not_a_drink_surprise(self):
        result = self.drive("""
function item(name, category) {
  return {name: name, category: category, option_groups: []};
}
var menu = {items: [
  item('Taro Slush', 'Slush'),
  item('Milk Tea', 'Milk Tea'),
  item('Egg Waffle', 'Featured Food')
]};
var picked = BobaRandomizer.pick(menu, 'Milk Tea', function () { return 0; });
report({picked: picked.item.name,
        all: BobaRandomizer.eligibleItems(menu, '').map(function (entry) { return entry.name; }),
        food: BobaRandomizer.eligibleItems(menu, 'Featured Food').length});
""")
        self.assertEqual(result["picked"], "Milk Tea")
        self.assertEqual(result["all"], ["Taro Slush", "Milk Tea"])
        self.assertEqual(result["food"], 0)

    def test_impossible_menu_items_are_never_selected(self):
        result = self.drive("""
var menu = {items: [
  {name: 'Broken Tea', category: 'Tea', option_groups: [
    {key: 'size', required: true, min: 1, max: 1, multiselect: false, options: []}
  ]},
  {name: 'Orderable Tea', category: 'Tea', option_groups: [
    {key: 'size', required: true, min: 1, max: 1, multiselect: false,
     options: [{label: 'Medium'}]}
  ]}
]};
var picked = BobaRandomizer.pick(menu, '', function () { return 0; });
report({name: picked.item.name, size: picked.selections.size,
        valid: BobaRandomizer.isValidSelection(picked.item, picked.selections)});
""")
        self.assertEqual(result, {
            "name": "Orderable Tea", "size": "Medium", "valid": True,
        })

    def test_every_current_drink_can_produce_a_valid_draft(self):
        current_menu = json.dumps(menu_module.participant_menu())
        result = self.drive(f"""
var menu = {current_menu};
var candidates = BobaRandomizer.eligibleItems(menu, '');
var invalid = [];
candidates.forEach(function (item) {{
  var rolled = BobaRandomizer.pick({{items: [item]}}, '', function () {{ return 0.91; }});
  if (!rolled || !BobaRandomizer.isValidSelection(rolled.item, rolled.selections)) {{
    invalid.push(item.name);
  }}
}});
report({{count: candidates.length, invalid: invalid,
        pickedFood: candidates.some(function (item) {{ return /food/i.test(item.category); }})}});
""")
        self.assertGreater(result["count"], 100)
        self.assertEqual(result["invalid"], [])
        self.assertFalse(result["pickedFood"])


if __name__ == "__main__":
    unittest.main()
