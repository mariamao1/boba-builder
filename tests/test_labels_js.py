"""DOM checks for the shared pickup-label renderer (app/static/labels.js)."""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
SCRIPT = ROOT / "app" / "static" / "labels.js"
SHIM = HERE / "preview_dom.js"
JSC_CANDIDATES = (
    "jsc",
    "/System/Library/Frameworks/JavaScriptCore.framework/Versions/A/Helpers/jsc",
)


def find_engine() -> str | None:
    for candidate in JSC_CANDIDATES:
        found = shutil.which(candidate) or (candidate if Path(candidate).exists() else None)
        if found:
            return found
    return None


ENGINE = find_engine()

PAYLOAD = {
    "ok": True,
    "title": "Friday tea",
    "cups": 3,
    "unplaced": 1,
    "text": "Friday tea (3 cups)\nAlice:\n  1/2 Taro Milk Tea — Large · 50% sugar",
    "labels": [
        {"seq": 1, "cups": 3, "person": "Alice", "cup": 1, "person_cups": 2,
         "drink": "Taro Milk Tea", "spec": "Large · 50% sugar", "notes": "no straw",
         "row_number": 2},
        {"seq": 2, "cups": 3, "person": "Alice", "cup": 2, "person_cups": 2,
         "drink": "Taro Milk Tea", "spec": "Large · 50% sugar", "notes": "",
         "row_number": 2},
        {"seq": 3, "cups": 3, "person": "Bob", "cup": 1, "person_cups": 1,
         "drink": "Mango Slush", "spec": "Store defaults", "notes": "",
         "row_number": 3},
    ],
}


@unittest.skipUnless(ENGINE, "no JavaScript engine on this machine")
class PickupLabelsScriptTests(unittest.TestCase):
    def drive(self, checks: str) -> dict:
        with tempfile.TemporaryDirectory() as scratch:
            driver = Path(scratch) / "driver.js"
            driver.write_text(f"""
load({str(SHIM)!r});
eval(readFile({str(SCRIPT)!r}));
var holder = document.createElement('div');
BobaLabels.render(holder, {json.dumps(PAYLOAD)});
function countByClass(className) {{
  var found = 0;
  holder.walk(function (node) {{
    if (node.className === className) found += 1;
  }});
  return found;
}}
function hasText(wanted) {{
  return holder.textContent.indexOf(wanted) >= 0;
}}
function firstCheck() {{
  var hit = null;
  holder.walk(function (node) {{
    if (!hit && node.className === 'pickup-check') hit = node;
  }});
  return hit;
}}
{checks}
""", encoding="utf-8")
            done = subprocess.run([ENGINE, str(driver)], capture_output=True,
                                  text=True, timeout=30)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        lines = [line for line in done.stdout.splitlines() if line.startswith("{")]
        self.assertTrue(lines, done.stdout + done.stderr)
        return json.loads(lines[-1])

    def test_one_card_per_cup_grouped_by_person(self):
        seen = self.drive("""
report({
  cards: countByClass('label-card'),
  groups: countByClass('label-group'),
  alice: hasText('Alice'),
  spec: hasText('Large · 50% sugar'),
  notes: hasText('Note: no straw'),
  multi: hasText('Cup 1 of 2'),
  seq: hasText('#3'),
  copy: hasText('Copy labels'),
  print: hasText('Print labels'),
  progress: hasText('0 of 3 handed out'),
  unplaced: hasText('1 drink was not placed and has no label.'),
});
""")
        self.assertEqual(seen, {
            "cards": 3, "groups": 2, "alice": True, "spec": True,
            "notes": True, "multi": True, "seq": True, "copy": True,
            "print": True, "progress": True, "unplaced": True,
        })

    def test_checking_a_cup_off_advances_the_progress(self):
        seen = self.drive("""
var box = firstCheck();
box.checked = true;
box.dispatch('change');
report({progress: hasText('1 of 3 handed out')});
""")
        self.assertEqual(seen, {"progress": True})


if __name__ == "__main__":
    unittest.main()
