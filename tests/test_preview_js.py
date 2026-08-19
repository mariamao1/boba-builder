"""Tests for the review & edit screen — app/static/preview.js.

The page is where "which control is wired to what" lives, and that is a real
place for bugs: the first cut of the quantity stepper handed focus back to
whichever search box you had last touched, which popped its typeahead list open.
Nothing in the Python suite could see that.

So these drive the actual file through a small DOM shim (`preview_dom.js`) in
whatever JavaScript engine the machine has — macOS ships JavaScriptCore — and
skip cleanly when there isn't one. They assert two things per interaction: what
was saved, and what got the focus afterwards.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import importer, pipeline  # noqa: E402

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
PREVIEW_JS = ROOT / "app" / "static" / "preview.js"
SHIM_JS = HERE / "preview_dom.js"
SAMPLE = ROOT / "data" / "sample-group-order.csv"

# macOS keeps its JavaScript engine here; `jsc` on PATH covers everyone else.
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


def sample_run() -> dict:
    """The messy sample, imported and matched, exactly as the page receives it."""
    result = importer.import_bytes(SAMPLE.read_bytes(), "sample-group-order.csv")
    run = pipeline.enrich(result.as_dict())
    run["run_id"] = "testrun"
    return {"ok": True, "run": run, "stages": pipeline.status()}


@unittest.skipUnless(ENGINE, "no JavaScript engine on this machine")
class PreviewScriptTest(unittest.TestCase):
    """Base: run a snippet against the real preview.js and read back its report."""

    editing = True

    @classmethod
    def setUpClass(cls):
        cls.payload = json.dumps(sample_run())

    def drive(self, script: str) -> dict:
        """Run `script` with the page loaded, and return what it reported."""
        with tempfile.TemporaryDirectory() as scratch:
            run_path = Path(scratch) / "run.json"
            run_path.write_text(self.payload, encoding="utf-8")
            driver = Path(scratch) / "driver.js"
            driver.write_text(f"""
load({str(SHIM_JS)!r});
reply = JSON.parse(readFile({str(run_path)!r}));
eval(loadPreview(readFile({str(PREVIEW_JS)!r}), {{editing: {str(self.editing).lower()}}}));
render(reply.run, reply.stages);
focused = [];  posted = [];      // ignore the first paint
{script}
""", encoding="utf-8")
            done = subprocess.run([ENGINE, str(driver)], capture_output=True,
                                  text=True, timeout=60)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        lines = [line for line in done.stdout.splitlines() if line.startswith("{")]
        self.assertTrue(lines, f"the page reported nothing:\n{done.stdout}{done.stderr}")
        return json.loads(lines[-1])

    def interact(self, steps: str) -> dict:
        return self.drive(steps + """
report({focused: focused, posted: posted.map(function (p) {
  return {url: p.url, body: p.body ? JSON.parse(p.body) : null};
})});
""")


class QuantityStepperTests(PreviewScriptTest):
    def test_the_stepper_saves_only_the_quantity(self):
        seen = self.interact("byFocus('5-qty-up').dispatch('click'); drainMicrotasks();")
        self.assertEqual([entry["body"] for entry in seen["posted"]], [{"quantity": 2}])
        self.assertTrue(seen["posted"][0]["url"].endswith("/rows/5"))

    def test_the_stepper_leaves_every_other_control_alone(self):
        # The bug this is here for: the toppings box had focus a moment ago, and
        # clicking + used to hand it back — which drops its typeahead open.
        seen = self.interact("""
byFocus('5-toppings').dispatch('focus');
byFocus('5-qty-up').dispatch('click');
drainMicrotasks();
""")
        self.assertEqual(seen["focused"], ["5-qty-up"])
        self.assertNotIn("5-toppings", seen["focused"])

    def test_minus_is_off_at_one_and_plus_at_twenty(self):
        seen = self.drive("""
report({
  down: byFocus('5-qty-down').disabled,      // row 5 is a single drink
  downOnTwo: byFocus('8-qty-down').disabled, // row 8 is "Winter Melon Tea x2"
  up: byFocus('5-qty-up').disabled,
});
""")
        self.assertTrue(seen["down"])
        self.assertFalse(seen["downOnTwo"])
        self.assertFalse(seen["up"])


class DropdownTests(PreviewScriptTest):
    def test_every_dropdown_shows_the_value_the_row_actually_has(self):
        """The whole point of the screen: you can see what is currently chosen.

        Not a layout test — a browser would be needed for that — but it holds
        the half that is checkable here: the right option is the selected one,
        so nothing but width can hide it.
        """
        seen = self.drive("""
var out = {};
['5-size', '5-sugar', '6-ice', '8-sugar', '7-ice'].forEach(function (key) {
  var select = byFocus(key);
  out[key] = select.children.filter(function (o) { return o.selected; })
                            .map(function (o) { return o.textContent; });
});
report(out);
""")
        self.assertEqual(seen["5-size"], ["Large"])
        self.assertEqual(seen["5-sugar"], ["50%"])
        self.assertEqual(seen["6-ice"], ["No Ice"])
        # Asked for and not on this drink: still selected, still named, and said
        # so — rather than the dropdown snapping to something nobody chose.
        self.assertEqual(seen["8-sugar"], ["0% — not on this drink"])
        self.assertEqual(seen["7-ice"], ["Regular Ice — not on this drink"])

    def test_the_review_table_is_sized_by_its_controls_not_the_column(self):
        # Ten dropdowns sharing 100% of a reading column come out ~30px wide,
        # which renders a <select> as an arrow and none of its value.
        seen = self.drive("""
var card = null, table = null;
nodes.body.walk(function (n) {
  if (!card && String(n.className).indexOf('card wide') === 0) card = n;
  if (!table && String(n.className).indexOf('orders editing') === 0) table = n;
});
report({card: !!card, table: !!table});
""")
        self.assertTrue(seen["card"], "the review card should break out of the reading width")
        self.assertTrue(seen["table"], "the review table should size to its contents")

    def test_a_dropdown_saves_its_own_axis_and_keeps_its_place(self):
        seen = self.interact("""
var select = byFocus('5-size');
select.value = 'Medium';
select.dispatch('change');
drainMicrotasks();
""")
        self.assertEqual([entry["body"] for entry in seen["posted"]], [{"size": "Medium"}])
        self.assertEqual(seen["focused"], ["5-size"])

    def test_clearing_a_dropdown_asks_for_the_store_default(self):
        seen = self.interact("""
var select = byFocus('5-sugar');
select.value = '';
select.dispatch('change');
drainMicrotasks();
""")
        self.assertEqual([entry["body"] for entry in seen["posted"]], [{"sugar": ""}])

    def test_a_drink_without_that_axis_has_no_dropdown_at_all(self):
        # Row 5 is a Taro Slush: no ice group, no milk alternative.
        seen = self.drive("report({ice: !!byFocus('5-ice'), milk: !!byFocus('5-milk'), "
                          "size: !!byFocus('5-size')});")
        self.assertFalse(seen["ice"])
        self.assertFalse(seen["milk"])
        self.assertTrue(seen["size"])


class ToppingTests(PreviewScriptTest):
    def test_adding_one_sends_the_whole_list(self):
        seen = self.interact("""
var box = byFocus('5-toppings');
box.value = 'Pudding';
box.dispatch('change');
drainMicrotasks();
""")
        self.assertEqual([entry["body"] for entry in seen["posted"]],
                         [{"toppings": ["Boba", "Pudding"]}])

    def test_removing_one_keeps_the_counts_on_the_others(self):
        # Row 11 is Tomás: "2x pudding, red bean".
        seen = self.interact("""
byText('chip topping', 'Red Bean✕').dispatch('click');
drainMicrotasks();
""")
        self.assertEqual([entry["body"] for entry in seen["posted"]],
                         [{"toppings": ["2x Pudding"]}])

    def test_the_add_box_does_not_grab_the_focus_back(self):
        # It commits on blur, so the caret has already gone where it was sent.
        seen = self.interact("""
var box = byFocus('5-toppings');
box.value = 'Pudding';
box.dispatch('change');
drainMicrotasks();
""")
        self.assertEqual(seen["focused"], [])

    def test_an_unchanged_box_saves_nothing(self):
        seen = self.interact("byFocus('5-toppings').dispatch('change'); drainMicrotasks();")
        self.assertEqual(seen["posted"], [])


class DrinkAndNameTests(PreviewScriptTest):
    def test_the_drink_box_starts_on_the_matched_name(self):
        seen = self.drive("report({value: byFocus('5-drink').value});")
        self.assertEqual(seen["value"], "Taro Slush")

    def test_picking_a_drink_saves_it(self):
        seen = self.interact("""
var box = byFocus('10-drink');
box.value = 'Winter Melon Lemonade';
box.dispatch('change');
drainMicrotasks();
""")
        self.assertEqual([entry["body"] for entry in seen["posted"]],
                         [{"drink": "Winter Melon Lemonade"}])

    def test_tabbing_past_an_untouched_drink_box_saves_nothing(self):
        seen = self.interact("byFocus('5-drink').dispatch('change'); drainMicrotasks();")
        self.assertEqual(seen["posted"], [])

    def test_naming_the_unnamed_row(self):
        seen = self.interact("""
var box = byFocus('9-person');
box.value = 'Kim';
box.dispatch('change');
drainMicrotasks();
""")
        self.assertEqual([entry["body"] for entry in seen["posted"]], [{"person": "Kim"}])
        self.assertEqual(seen["focused"], [])   # the tab that got them out stands


class ReadOnlyViewTests(PreviewScriptTest):
    editing = False

    def test_the_table_is_text_until_you_ask_to_edit(self):
        seen = self.drive("report({drink: !!byFocus('5-drink'), qty: !!byFocus('5-qty-up')});")
        self.assertFalse(seen["drink"])
        self.assertFalse(seen["qty"])

    def test_and_it_stays_in_the_reading_column(self):
        seen = self.drive("""
var wide = 0;
nodes.body.walk(function (n) { if (String(n.className).indexOf('wide') >= 0) wide++; });
report({wide: wide});
""")
        self.assertEqual(seen["wide"], 0)

    def test_the_toggle_is_there_and_says_what_it_does(self):
        seen = self.drive("""
var toggle = byText('btn', 'Review & edit');
report({found: !!toggle});
""")
        self.assertTrue(seen["found"])

    def test_a_fix_it_button_still_works_without_edit_mode(self):
        # Row 8 asked for no sugar; Winter Melon Tea starts at 30%.
        seen = self.interact("byText('chip', '30%').dispatch('click'); drainMicrotasks();")
        self.assertEqual([entry["body"] for entry in seen["posted"]], [{"sugar": "30%"}])


if __name__ == "__main__":
    unittest.main()
