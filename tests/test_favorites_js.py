"""Behavior tests for the account-free, browser-local favorite drink store."""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
FAVORITES_JS = ROOT / "app" / "static" / "favorites.js"
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
class FavoritesScriptTests(unittest.TestCase):
    def drive(self, script: str) -> dict:
        with tempfile.TemporaryDirectory() as scratch:
            driver = Path(scratch) / "driver.js"
            driver.write_text(f"""
var window = this;
var values = {{}};
var localStorage = {{
  getItem: function (key) {{ return values[key] === undefined ? null : values[key]; }},
  setItem: function (key, value) {{ values[key] = String(value); }}
}};
window.localStorage = localStorage;
load({str(FAVORITES_JS)!r});
function report(value) {{ print(JSON.stringify(value)); }}
{script}
""", encoding="utf-8")
            done = subprocess.run(
                [ENGINE, str(driver)], capture_output=True, text=True, timeout=30)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        lines = [line for line in done.stdout.splitlines() if line.startswith("{")]
        self.assertTrue(lines, done.stdout + done.stderr)
        return json.loads(lines[-1])

    def test_round_trip_keeps_the_full_drink_and_modifier_set(self):
        result = self.drive("""
var payload = {
  drink: 'Taro Slush', size: 'Large', sugar: '50%', ice: '', milk: '',
  temperature: '', toppings: ['Boba', 'Pudding'], quantity: 2,
  notes: 'half boba'
};
var item = {id: 'drink-1'};
var favorite = BobaFavorites.fromOrder('Afternoon caffeine', payload, 'store-a', item);
BobaFavorites.save(localStorage, favorite);
var loaded = BobaFavorites.list(localStorage, 'store-a')[0];
report({name: loaded.name, drink: loaded.drink, size: loaded.size,
        sugar: loaded.sugar, toppings: loaded.toppings,
        quantity: loaded.quantity, notes: loaded.notes, item_id: loaded.item_id});
""")
        self.assertEqual(result, {
            "name": "Afternoon caffeine",
            "drink": "Taro Slush",
            "size": "Large",
            "sugar": "50%",
            "toppings": ["Boba", "Pudding"],
            "quantity": 2,
            "notes": "half boba",
            "item_id": "drink-1",
        })

    def test_limit_is_per_store_and_editing_does_not_consume_a_slot(self):
        result = self.drive("""
function make(number, store) {
  return BobaFavorites.fromOrder('Usual ' + number, {
    drink: 'Tea', size: '', sugar: '', ice: '', milk: '', temperature: '',
    toppings: [], quantity: 1, notes: ''
  }, store, {id: 'tea'});
}
var message = '';
for (var index = 0; index < 8; index++) BobaFavorites.save(localStorage, make(index, 'a'));
try { BobaFavorites.save(localStorage, make(9, 'a')); } catch (error) { message = error.message; }
var first = BobaFavorites.list(localStorage, 'a')[0];
var updated = BobaFavorites.fromOrder('Renamed', first, 'a', {id: 'tea'}, first);
BobaFavorites.save(localStorage, updated);
BobaFavorites.save(localStorage, make(1, 'b'));
report({a: BobaFavorites.list(localStorage, 'a').length,
        b: BobaFavorites.list(localStorage, 'b').length,
        first: BobaFavorites.list(localStorage, 'a')[0].name, message: message});
""")
        self.assertEqual(result["a"], 8)
        self.assertEqual(result["b"], 1)
        self.assertEqual(result["first"], "Renamed")
        self.assertIn("up to 8", result["message"])

    def test_only_current_menu_compatible_favorites_can_be_one_tap_added(self):
        result = self.drive("""
var menu = {restaurant_id: 'store-a', items: [{
  id: 'drink-1', name: 'Taro Slush', option_groups: [
    {name: 'Size', axis: 'size', required: true, min: 1, max: 1,
     multiselect: false, options: [{label: 'Medium'}, {label: 'Large'}]},
    {name: 'Toppings', axis: 'toppings', required: false, min: 0, max: 2,
     multiselect: true, options: [{label: 'Boba'}, {label: 'Pudding'}]}
  ]
}]};
function favorite(size, toppings) {
  return BobaFavorites.fromOrder('Mine', {
    drink: 'Taro Slush', size: size, sugar: '', ice: '', milk: '', temperature: '',
    toppings: toppings, quantity: 1, notes: ''
  }, 'store-a', {id: 'drink-1'});
}
var exact = BobaFavorites.compatibility(favorite('Large', ['Boba']), menu);
var oldSize = BobaFavorites.compatibility(favorite('Small', ['Boba']), menu);
var oldTopping = BobaFavorites.compatibility(favorite('Large', ['Aloe']), menu);
report({exact: exact.ok, payload: exact.payload, oldSize: oldSize.ok,
        oldSizeReason: oldSize.reason, oldTopping: oldTopping.ok});
""")
        self.assertTrue(result["exact"])
        self.assertEqual(result["payload"]["size"], "Large")
        self.assertEqual(result["payload"]["toppings"], ["Boba"])
        self.assertFalse(result["oldSize"])
        self.assertIn("Size", result["oldSizeReason"])
        self.assertFalse(result["oldTopping"])

    def test_delete_and_malformed_storage_are_safe(self):
        result = self.drive("""
values[BobaFavorites.STORAGE_KEY] = '{broken';
var before = BobaFavorites.list(localStorage).length;
var favorite = BobaFavorites.fromOrder('Mine', {
  drink: 'Tea', size: '', sugar: '', ice: '', milk: '', temperature: '',
  toppings: [], quantity: 1, notes: ''
}, 'store-a', {id: 'tea'});
BobaFavorites.save(localStorage, favorite);
var removed = BobaFavorites.remove(localStorage, favorite.id);
report({before: before, removed: removed, after: BobaFavorites.list(localStorage).length});
""")
        self.assertEqual(result, {"before": 0, "removed": True, "after": 0})


if __name__ == "__main__":
    unittest.main()
