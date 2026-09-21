"""Behavior tests for the first-time organizer onboarding state."""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
ONBOARDING_JS = ROOT / "app" / "static" / "onboarding.js"
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
class OnboardingScriptTests(unittest.TestCase):
    def drive(self, script: str) -> dict:
        with tempfile.TemporaryDirectory() as scratch:
            driver = Path(scratch) / "driver.js"
            driver.write_text(f"""
var window = this;
var values = {{}};
var localStorage = {{
  getItem: function (key) {{ return values[key] === undefined ? null : values[key]; }},
  setItem: function (key, value) {{ values[key] = String(value); }},
  removeItem: function (key) {{ delete values[key]; }}
}};
window.localStorage = localStorage;
load({str(ONBOARDING_JS)!r});
function report(value) {{ print(JSON.stringify(value)); }}
{script}
""", encoding="utf-8")
            done = subprocess.run(
                [ENGINE, str(driver)], capture_output=True, text=True, timeout=30)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        lines = [line for line in done.stdout.splitlines() if line.startswith("{")]
        self.assertTrue(lines, done.stdout + done.stderr)
        return json.loads(lines[-1])

    def test_both_moments_show_by_default(self):
        result = self.drive("report({entry: BobaOnboarding.shouldShow('entry'), "
                            "handoff: BobaOnboarding.shouldShow('handoff')});")
        self.assertEqual(result, {"entry": True, "handoff": True})

    def test_dismissing_one_moment_leaves_the_other(self):
        result = self.drive("""
BobaOnboarding.dismiss('entry');
report({entry: BobaOnboarding.shouldShow('entry'),
        handoff: BobaOnboarding.shouldShow('handoff')});
""")
        self.assertEqual(result, {"entry": False, "handoff": True})

    def test_dismiss_returns_true_and_persists(self):
        result = self.drive("""
var dismissed = BobaOnboarding.dismiss('handoff');
var stored = JSON.parse(localStorage.getItem(BobaOnboarding.STORAGE_KEY));
report({dismissed: dismissed, stored: stored['dismissed:handoff'],
        shown: BobaOnboarding.shouldShow('handoff')});
""")
        self.assertEqual(result, {"dismissed": True, "stored": True, "shown": False})

    def test_reopen_restores_one_moment_only(self):
        result = self.drive("""
BobaOnboarding.dismiss('entry');
BobaOnboarding.dismiss('handoff');
BobaOnboarding.show('entry');
report({entry: BobaOnboarding.shouldShow('entry'),
        handoff: BobaOnboarding.shouldShow('handoff')});
""")
        self.assertEqual(result, {"entry": True, "handoff": False})

    def test_reset_restores_both_moments(self):
        result = self.drive("""
BobaOnboarding.dismiss('entry');
BobaOnboarding.dismiss('handoff');
BobaOnboarding.reset();
report({entry: BobaOnboarding.shouldShow('entry'),
        handoff: BobaOnboarding.shouldShow('handoff')});
""")
        self.assertEqual(result, {"entry": True, "handoff": True})

    def test_unknown_kind_never_shows_and_does_not_store(self):
        result = self.drive("""
var shown = BobaOnboarding.shouldShow('tour');
var dismissed = BobaOnboarding.dismiss('tour');
report({shown: shown, dismissed: dismissed,
        stored: localStorage.getItem(BobaOnboarding.STORAGE_KEY)});
""")
        self.assertEqual(result, {"shown": False, "dismissed": False, "stored": None})

    def test_corrupt_or_denied_storage_fails_open(self):
        result = self.drive("""
localStorage.setItem(BobaOnboarding.STORAGE_KEY, 'not-json{{{');
var corrupt = BobaOnboarding.shouldShow('entry');
var denied = {getItem: function () { throw new Error('denied'); },
              setItem: function () { throw new Error('denied'); }};
report({corrupt: corrupt, denied: BobaOnboarding.shouldShow('handoff', denied),
        dismissed: BobaOnboarding.dismiss('handoff', denied)});
""")
        self.assertEqual(result, {"corrupt": True, "denied": True, "dismissed": False})


if __name__ == "__main__":
    unittest.main()
