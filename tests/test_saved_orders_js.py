"""Small DOM checks for the Saved Orders list and detail page."""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
SCRIPT = ROOT / "app" / "static" / "saved-orders.js"
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


@unittest.skipUnless(ENGINE, "no JavaScript engine on this machine")
class SavedOrdersScriptTests(unittest.TestCase):
    def drive(self, path: str, payload: dict, checks: str) -> dict:
        with tempfile.TemporaryDirectory() as scratch:
            driver = Path(scratch) / "driver.js"
            driver.write_text(f"""
load({str(SHIM)!r});
window.location.pathname = {path!r};
reply = {json.dumps(payload)};
eval(readFile({str(SCRIPT)!r}));
drainMicrotasks();
function savedByText(className, wanted) {{
  var hit = null;
  nodes['saved-body'].walk(function (node) {{
    if (!hit && node.className === className && node.textContent === wanted) hit = node;
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

    def test_empty_state_explains_how_to_save_an_order(self):
        seen = self.drive("/saved-orders", {"ok": True, "orders": []}, """
report({heading: !!savedByText('', 'No saved orders yet'),
        action: !!savedByText('btn primary', 'Start an order')});
""")
        self.assertEqual(seen, {"heading": True, "action": True})

    def test_detail_shows_source_manifest_totals_and_actions(self):
        payload = {
            "ok": True,
            "order": {
                "id": "abcdefghijklmnopqrstuvwx",
                "label": "Launch tea",
                "order_date": "2026-09-08",
                "source": {"type": "group_link", "kind": "group_order"},
                "store": {"name": "KFT Bay Ridge"},
                "counts": {"people": 1, "order_lines": 1, "placed_drinks": 2,
                           "not_placed_drinks": 0},
                "totals": {"total": 14.48},
                "items": [{"person": "Alice", "drink": "Taro Slush", "quantity": 2,
                           "actual_total": 13.30,
                           "options": [{"name": "Boba", "quantity": 1}]}],
                "failed": [],
                "skipped": [],
            },
        }
        seen = self.drive("/saved-orders/abcdefghijklmnopqrstuvwx", payload, """
report({
  title: !!savedByText('', 'Launch tea'),
  source: nodes['saved-body'].textContent.indexOf('Group link') >= 0,
  person: !!savedByText('', 'Alice — 2× Taro Slush'),
  modifier: nodes['saved-body'].textContent.indexOf('Boba') >= 0,
  total: !!savedByText('grand', '$14.48'),
  repeat: !!savedByText('btn primary', 'Use for a new cart'),
  export: !!savedByText('btn', 'Export CSV'),
});
""")
        self.assertEqual(seen, {
            "title": True,
            "source": True,
            "person": True,
            "modifier": True,
            "total": True,
            "repeat": True,
            "export": True,
        })


if __name__ == "__main__":
    unittest.main()
