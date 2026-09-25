"""Behavior tests for the browser-local whole-order template store (Task 28)."""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
TEMPLATES_JS = ROOT / "app" / "static" / "order-templates.js"
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
class OrderTemplateScriptTests(unittest.TestCase):
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
load({str(TEMPLATES_JS)!r});
function report(value) {{ print(JSON.stringify(value)); }}
{script}
""", encoding="utf-8")
            done = subprocess.run(
                [ENGINE, str(driver)], capture_output=True, text=True, timeout=30)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        lines = [line for line in done.stdout.splitlines() if line.startswith("{")]
        self.assertTrue(lines, done.stdout + done.stderr)
        return json.loads(lines[-1])

    def test_from_run_rows_keeps_every_person_with_the_full_modifier_set(self):
        result = self.drive("""
var rows = [
  {person: 'Alice', drink: 'Taro Slush', size: 'Large', sugar: '50%', ice: 'Less Ice',
   milk: '', temperature: '', toppings: 'Boba, Pudding', quantity: 2, notes: 'extra cold'},
  {person: 'Bob', drink: 'Winter Melon Tea', size: '', sugar: '', ice: '', milk: '',
   temperature: '', toppings: [], quantity: 1, notes: ''}
];
var template = BobaOrderTemplates.fromRunRows('Friday crew', rows, 'store-a');
BobaOrderTemplates.save(localStorage, template);
var loaded = BobaOrderTemplates.list(localStorage, 'store-a')[0];
report({name: loaded.name, entries: loaded.entries});
""")
        self.assertEqual(result["name"], "Friday crew")
        self.assertEqual(result["entries"], [
            {"person": "Alice", "drink": "Taro Slush", "size": "Large",
             "sugar": "50%", "ice": "Less Ice", "milk": "", "temperature": "",
             "toppings": ["Boba", "Pudding"], "quantity": 2, "notes": "extra cold"},
            {"person": "Bob", "drink": "Winter Melon Tea", "size": "",
             "sugar": "", "ice": "", "milk": "", "temperature": "",
             "toppings": [], "quantity": 1, "notes": ""},
        ])

    def test_from_run_rows_prefers_canonical_menu_values(self):
        result = self.drive("""
var rows = [
  {person: 'Alice', drink: 'taro slushh', size: 'big', sugar: 'half', ice: '',
   milk: '', temperature: '', toppings: 'boba', quantity: 1, notes: '',
   canonical: {drink: 'Taro Slush', size: 'Large', sugar: '50%', ice: '',
               milk: '', temperature: '', toppings: ['Boba']}}
];
var template = BobaOrderTemplates.fromRunRows('Friday crew', rows, 'store-a');
report({entries: template.entries});
""")
        self.assertEqual(result["entries"][0]["drink"], "Taro Slush")
        self.assertEqual(result["entries"][0]["size"], "Large")
        self.assertEqual(result["entries"][0]["sugar"], "50%")
        self.assertEqual(result["entries"][0]["toppings"], ["Boba"])

    def test_limit_is_per_store_and_editing_does_not_consume_a_slot(self):
        result = self.drive("""
function make(number, store) {
  return BobaOrderTemplates.fromRunRows('Crew ' + number, [
    {person: 'Alice', drink: 'Tea', quantity: 1}
  ], store);
}
var message = '';
var cap = BobaOrderTemplates.MAX_PER_STORE;
for (var index = 0; index < cap; index++) BobaOrderTemplates.save(localStorage, make(index, 'a'));
try { BobaOrderTemplates.save(localStorage, make(cap, 'a')); }
catch (error) { message = error.message; }
var first = BobaOrderTemplates.list(localStorage, 'a')[0];
var renamed = BobaOrderTemplates.fromRunRows('Renamed', first.entries, 'a', first);
BobaOrderTemplates.save(localStorage, renamed);
BobaOrderTemplates.save(localStorage, make(1, 'b'));
report({a: BobaOrderTemplates.list(localStorage, 'a').length,
        b: BobaOrderTemplates.list(localStorage, 'b').length,
        first: BobaOrderTemplates.list(localStorage, 'a')[0].name, message: message,
        cap: cap});
""")
        self.assertEqual(result["a"], result["cap"])
        self.assertGreater(result["cap"], 0)
        self.assertEqual(result["b"], 1)
        self.assertEqual(result["first"], "Renamed")
        self.assertTrue(result["message"], "expected a limit error")

    def test_templates_without_a_name_store_or_drink_are_rejected(self):
        result = self.drive("""
var problems = [];
try { BobaOrderTemplates.save(localStorage, BobaOrderTemplates.fromRunRows('', [
  {person: 'Alice', drink: 'Tea'}], 'store-a')); }
catch (error) { problems.push('name'); }
try { BobaOrderTemplates.save(localStorage, BobaOrderTemplates.fromRunRows('Crew', [], 'store-a')); }
catch (error) { problems.push('empty'); }
try { BobaOrderTemplates.save(localStorage, BobaOrderTemplates.fromRunRows('Crew', [
  {person: '', drink: 'Tea'}], 'store-a')); }
catch (error) { problems.push('person'); }
try { BobaOrderTemplates.save(localStorage, BobaOrderTemplates.fromRunRows('Crew', [
  {person: 'Alice', drink: '  '}], 'store-a')); }
catch (error) { problems.push('drink'); }
report({problems: problems, kept: BobaOrderTemplates.list(localStorage).length});
""")
        self.assertEqual(result, {"problems": ["name", "empty", "person", "drink"], "kept": 0})

    def test_solo_loading_restores_drinks_and_drops_names(self):
        result = self.drive("""
var template = BobaOrderTemplates.fromRunRows('Friday crew', [
  {person: 'Alice', drink: 'Taro Slush', size: 'Large', sugar: '50%', ice: '',
   milk: '', temperature: '', toppings: ['Boba'], quantity: 2, notes: 'x'},
  {person: 'Bob', drink: 'Tea', quantity: 1}
], 'store-a');
var drinks = BobaOrderTemplates.toSoloDrinks(template);
drinks[0].toppings.push('Pudding');
var again = BobaOrderTemplates.toSoloDrinks(template);
report({drinks: drinks, uniform: BobaOrderTemplates.uniformPerson(template),
        toppingsKept: again[0].toppings});
""")
        self.assertEqual(result["drinks"], [
            {"drink": "Taro Slush", "size": "Large", "sugar": "50%", "ice": "",
             "milk": "", "temperature": "", "toppings": ["Boba", "Pudding"],
             "quantity": 2, "notes": "x"},
            {"drink": "Tea", "size": "", "sugar": "", "ice": "",
             "milk": "", "temperature": "", "toppings": [],
             "quantity": 1, "notes": ""},
        ])
        self.assertNotIn("person", result["drinks"][0])
        self.assertEqual(result["uniform"], "")
        self.assertEqual(result["toppingsKept"], ["Boba"])

    def test_uniform_person_names_a_single_owner_template(self):
        result = self.drive("""
var solo = BobaOrderTemplates.fromRunRows('Mine', [
  {person: 'Mariam', drink: 'Taro Slush', quantity: 1},
  {person: 'mariam ', drink: 'Tea', quantity: 1}
], 'store-a');
var mixed = BobaOrderTemplates.fromRunRows('Crew', [
  {person: 'Mariam', drink: 'Taro Slush', quantity: 1},
  {person: 'Bob', drink: 'Tea', quantity: 1}
], 'store-a');
report({solo: BobaOrderTemplates.uniformPerson(solo),
        mixed: BobaOrderTemplates.uniformPerson(mixed)});
""")
        self.assertEqual(result, {"solo": "Mariam", "mixed": ""})

    def test_only_menu_compatible_entries_load_cleanly(self):
        result = self.drive("""
var menu = {restaurant_id: 'store-a', items: [{
  id: 'drink-1', name: 'Taro Slush', option_groups: [
    {name: 'Size', axis: 'size', required: true, min: 1, max: 1,
     multiselect: false, options: [{label: 'Medium'}, {label: 'Large'}]},
    {name: 'Toppings', axis: 'toppings', required: false, min: 0, max: 2,
     multiselect: true, options: [{label: 'Boba'}, {label: 'Pudding'}]}
  ]
}]};
var good = {person: 'Alice', drink: 'Taro Slush', size: 'Large', sugar: '',
            ice: '', milk: '', temperature: '', toppings: ['Boba'], quantity: 1, notes: ''};
var staleSize = {person: 'Alice', drink: 'Taro Slush', size: 'Small', sugar: '',
                 ice: '', milk: '', temperature: '', toppings: [], quantity: 1, notes: ''};
var gone = {person: 'Bob', drink: 'Ghost Melon', size: '', sugar: '',
            ice: '', milk: '', temperature: '', toppings: [], quantity: 1, notes: ''};
var exact = BobaOrderTemplates.entryCompatibility(good, menu);
report({exact: exact.ok, payload: exact.payload,
        staleSize: BobaOrderTemplates.entryCompatibility(staleSize, menu).ok,
        gone: BobaOrderTemplates.entryCompatibility(gone, menu).ok,
        wrongStore: BobaOrderTemplates.entryCompatibility(good,
          {restaurant_id: 'store-b', items: []}).ok});
""")
        self.assertTrue(result["exact"])
        self.assertEqual(result["payload"]["drink"], "Taro Slush")
        self.assertFalse(result["staleSize"])
        self.assertFalse(result["gone"])
        self.assertFalse(result["wrongStore"])

    def test_delete_and_malformed_storage_are_safe(self):
        result = self.drive("""
values[BobaOrderTemplates.STORAGE_KEY] = '{broken';
var before = BobaOrderTemplates.list(localStorage).length;
var template = BobaOrderTemplates.fromRunRows('Crew', [
  {person: 'Alice', drink: 'Tea', quantity: 1}], 'store-a');
BobaOrderTemplates.save(localStorage, template);
var removed = BobaOrderTemplates.remove(localStorage, template.id);
var missing = BobaOrderTemplates.remove(localStorage, 'no-such-id');
report({before: before, removed: removed, missing: missing,
        after: BobaOrderTemplates.list(localStorage).length});
""")
        self.assertEqual(
            result, {"before": 0, "removed": True, "missing": False, "after": 0})


if __name__ == "__main__":
    unittest.main()
