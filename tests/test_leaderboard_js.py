"""Behavior tests for the shared leaderboard helpers."""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
LEADERBOARD_JS = ROOT / "app" / "static" / "leaderboard.js"
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
class LeaderboardScriptTests(unittest.TestCase):
    def drive(self, script: str) -> dict:
        with tempfile.TemporaryDirectory() as scratch:
            driver = Path(scratch) / "driver.js"
            driver.write_text(f"""
var window = this;
function FakeNode(tag) {{
  this.tagName = tag;
  this.children = [];
  this.className = '';
  this.hidden = false;
  this.attrs = {{}};
  this.listeners = {{}};
  this._text = '';
}}
FakeNode.prototype.append = function () {{
  for (var i = 0; i < arguments.length; i++) this.children.push(arguments[i]);
}};
Object.defineProperty(FakeNode.prototype, 'textContent', {{
  get: function () {{
    var out = this._text;
    for (var i = 0; i < this.children.length; i++) out += this.children[i].textContent;
    return out;
  }},
  set: function (value) {{ this._text = value; this.children = []; }}
}});
FakeNode.prototype.setAttribute = function (name, value) {{ this.attrs[name] = value; }};
FakeNode.prototype.addEventListener = function (name, fn) {{
  (this.listeners[name] = this.listeners[name] || []).push(fn);
}};
var fakeDocument = {{ createElement: function (tag) {{ return new FakeNode(tag); }} }};
load({str(LEADERBOARD_JS)!r});
function report(value) {{ print(JSON.stringify(value)); }}
{script}
""", encoding="utf-8")
            done = subprocess.run(
                [ENGINE, str(driver)], capture_output=True, text=True, timeout=30)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        lines = [line for line in done.stdout.splitlines() if line.startswith("{")]
        self.assertTrue(lines, done.stdout + done.stderr)
        return json.loads(lines[-1])

    def test_normalize_matches_the_python_key(self):
        result = self.drive("""
report({
  a: BobaLeaderboard.normalize('Taro  Milk Tea'),
  b: BobaLeaderboard.normalize('  TARO milk TEA '),
  c: BobaLeaderboard.normalize(null)
});
""")
        self.assertEqual(result, {"a": "taro milk tea", "b": "taro milk tea", "c": ""})

    def test_quick_picks_keep_only_menu_matches(self):
        result = self.drive("""
var entries = [
  {drink: 'Taro Milk Tea', drinks: 9, orders: 7},
  {drink: 'Off Menu Special', drinks: 8, orders: 8},
  {drink: 'TARO MILK TEA', drinks: 1, orders: 1},
  {drink: 'Matcha Milk', drinks: 2, orders: 2}
];
var menu = [{name: 'Taro Milk Tea'}, {name: 'Matcha Milk'}];
var picks = BobaLeaderboard.quickPicks(entries, menu, 5);
report({names: picks.map(function (pick) { return pick.drink; }),
        counts: picks.map(function (pick) { return pick.drinks; })});
""")
        self.assertEqual(result["names"], ["Taro Milk Tea", "Matcha Milk"])
        self.assertEqual(result["counts"], [9, 2])

    def test_board_renders_ranks_counts_and_empty_state(self):
        result = self.drive("""
var list = new FakeNode('ol');
BobaLeaderboard.renderBoard(fakeDocument, list, {entries: [
  {drink: 'Taro Milk Tea', drinks: 9, orders: 7},
  {drink: 'Matcha Milk', drinks: 2, orders: 2}
]});
var rows = list.children.map(function (row) { return row.textContent; });
var empty = new FakeNode('ol');
BobaLeaderboard.renderBoard(fakeDocument, empty, {entries: []});
report({rows: rows, empty: empty.textContent,
        label: list.children[0].attrs['aria-label']});
""")
        self.assertEqual(result["rows"], ["1Taro Milk Tea×9", "2Matcha Milk×2"])
        self.assertIn("No group orders yet", result["empty"])
        self.assertIn("Number 1", result["label"])
        self.assertIn("9 cups", result["label"])

    def test_quick_picks_hide_the_panel_without_matches(self):
        result = self.drive("""
var panel = new FakeNode('section');
var list = new FakeNode('div');
var chosen = [];
BobaLeaderboard.renderQuickPicks(fakeDocument, panel, list, [], function (item) {
  chosen.push(item.name);
});
var hiddenWhenEmpty = panel.hidden;
var picks = [{drink: 'Taro Milk Tea', drinks: 9, orders: 7, item: {name: 'Taro Milk Tea'}}];
BobaLeaderboard.renderQuickPicks(fakeDocument, panel, list, picks, function (item) {
  chosen.push(item.name);
});
list.children[0].listeners.click[0]();
report({hiddenWhenEmpty: hiddenWhenEmpty, shown: !panel.hidden,
        chip: list.children[0].textContent, picked: chosen});
""")
        self.assertEqual(result, {
            "hiddenWhenEmpty": True,
            "shown": True,
            "chip": "Taro Milk Tea×9",
            "picked": ["Taro Milk Tea"],
        })


if __name__ == "__main__":
    unittest.main()
