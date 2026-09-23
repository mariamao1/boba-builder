"""Task 27: one person ordering just for themselves through the shared pipeline."""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from app import menu, pipeline, runs, server, solo

ROOT = Path(__file__).resolve().parent.parent
SOLO_JS = ROOT / "app" / "static" / "solo.js"
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


class SoloRunTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.original_run_dir = runs.RUN_DIR
        runs.RUN_DIR = Path(self.temporary.name) / "runs"

    def tearDown(self):
        runs.RUN_DIR = self.original_run_dir
        self.temporary.cleanup()

    def test_create_run_goes_through_the_shared_pipeline(self):
        run_id, saved = solo.create_run({
            "drink": "Taro Slush", "size": "Large", "sugar": "50%",
            "toppings": ["Boba"], "quantity": 1,
        })
        self.assertEqual(saved["source"]["kind"], "solo")
        self.assertEqual(saved["source"]["restaurant_id"], menu.TARGET_STORE)
        self.assertEqual(len(saved["rows"]), 1)
        self.assertEqual(saved["rows"][0]["person"], "You")
        enriched = pipeline.enrich(saved)
        match = enriched["rows"][0]["match"]
        self.assertEqual(match["status"], "ready")
        self.assertEqual(match["item"]["name"], "Taro Slush")
        # And it round-trips through run storage like any other run.
        self.assertEqual(runs.load(run_id)["source"]["kind"], "solo")

    def test_person_defaults_to_you_but_a_name_is_kept(self):
        _run_id, saved = solo.create_run({"drink": "Taro Slush"})
        self.assertEqual(saved["rows"][0]["person"], "You")
        _run_id, saved = solo.create_run({"drink": "Taro Slush", "person": "  Mariam "})
        self.assertEqual(saved["rows"][0]["person"], "Mariam")

    def test_drink_is_required(self):
        for payload in ({}, {"drink": ""}, {"drink": "   "}, {"size": "Large"}):
            with self.assertRaises(solo.SoloOrderError):
                solo.create_run(payload)

    def test_quantity_bounds_match_the_importer_ceiling(self):
        with self.assertRaises(solo.SoloOrderError):
            solo.create_run({"drink": "Taro Slush", "quantity": 0})
        with self.assertRaises(solo.SoloOrderError):
            solo.create_run({"drink": "Taro Slush", "quantity": 21})
        with self.assertRaises(solo.SoloOrderError):
            solo.create_run({"drink": "Taro Slush", "quantity": "lots"})
        _run_id, saved = solo.create_run({"drink": "Taro Slush", "quantity": "2"})
        self.assertEqual(saved["rows"][0]["quantity"], 2)

    def test_unknown_store_is_a_404(self):
        with self.assertRaises(solo.UnknownSoloStore) as raised:
            solo.create_run({"drink": "Taro Slush"}, restaurant_id="no-such-store")
        self.assertEqual(raised.exception.status, 404)

    def test_unmatched_drink_still_creates_a_run_for_preview_reconciliation(self):
        run_id, saved = solo.create_run({"drink": "Not A Real Drink At All"})
        enriched = pipeline.enrich(saved)
        self.assertNotEqual(enriched["rows"][0]["match"]["status"], "ready")
        self.assertIsNotNone(runs.load(run_id))

    def test_create_run_accepts_multiple_drinks_for_one_person(self):
        run_id, saved = solo.create_run({
            "person": "Mariam",
            "drinks": [
                {"drink": "Taro Slush", "size": "Large",
                 "toppings": ["Boba"], "quantity": 1},
                {"drink": "Winter Melon Tea", "sugar": "50%", "quantity": 2},
            ],
        })
        self.assertEqual(saved["source"]["kind"], "solo")
        self.assertEqual(len(saved["rows"]), 2)
        self.assertEqual([row["person"] for row in saved["rows"]],
                         ["Mariam", "Mariam"])
        enriched = pipeline.enrich(saved)
        self.assertEqual([row["match"]["status"] for row in enriched["rows"]],
                         ["ready", "ready"])
        self.assertIsNotNone(runs.load(run_id))

    def test_drinks_must_be_a_nonempty_list(self):
        for payload in ({"drinks": []}, {"drinks": "tea"}, {"drinks": [{}]}):
            with self.assertRaises(solo.SoloOrderError):
                solo.create_run(payload)

    def test_multi_drink_error_names_the_drink(self):
        with self.assertRaises(solo.SoloOrderError) as raised:
            solo.create_run({"drinks": [{"drink": "Taro Slush"}, {"size": "Large"}]})
        self.assertIn("drink 2", str(raised.exception))


class SoloHttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.original_run_dir = runs.RUN_DIR
        runs.RUN_DIR = Path(cls.temporary.name) / "runs"
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.base = f"http://127.0.0.1:{cls.httpd.server_address[1]}"
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        runs.RUN_DIR = cls.original_run_dir
        cls.temporary.cleanup()

    def request(self, method: str, path: str, payload=None):
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json"} if payload is not None else {}
        request = urllib.request.Request(
            self.base + path, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                body = response.read()
                try:
                    return response.status, json.loads(body)
                except json.JSONDecodeError:
                    return response.status, body
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            try:
                return exc.code, json.loads(raw)
            except json.JSONDecodeError:
                return exc.code, raw

    def test_solo_order_creates_a_previewable_run(self):
        status, created = self.request("POST", "/api/solo-orders", {
            "drink": "Taro Slush", "size": "Large", "sugar": "50%",
            "toppings": ["Boba"], "quantity": 1,
        })
        self.assertEqual(status, 201, created)
        self.assertTrue(created["ok"])
        self.assertTrue(created["preview_url"].startswith("/preview/"))

        status, fetched = self.request("GET", f"/api/runs/{created['run_id']}")
        self.assertEqual(status, 200, fetched)
        self.assertEqual(fetched["run"]["source"]["kind"], "solo")
        self.assertEqual(fetched["run"]["rows"][0]["match"]["status"], "ready")

    def test_solo_order_rejects_a_missing_drink(self):
        status, body = self.request("POST", "/api/solo-orders", {"size": "Large"})
        self.assertEqual(status, 400)
        self.assertFalse(body["ok"])

    def test_solo_order_accepts_multiple_drinks(self):
        status, created = self.request("POST", "/api/solo-orders", {
            "person": "Mariam",
            "drinks": [
                {"drink": "Taro Slush", "quantity": 1},
                {"drink": "Winter Melon Tea", "sugar": "50%", "quantity": 2},
            ],
        })
        self.assertEqual(status, 201, created)
        self.assertTrue(created["preview_url"].startswith("/preview/"))

        status, fetched = self.request("GET", f"/api/runs/{created['run_id']}")
        self.assertEqual(status, 200, fetched)
        rows = fetched["run"]["rows"]
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(row["match"]["status"] == "ready" for row in rows))
        self.assertEqual({row["person"] for row in rows}, {"Mariam"})

    def test_solo_page_is_served(self):
        status, body = self.request("GET", "/solo")
        self.assertEqual(status, 200)
        self.assertIn("solo.js", body.decode("utf-8"))


@unittest.skipUnless(ENGINE, "no JavaScript engine on this machine")
class SoloScriptTests(unittest.TestCase):
    """The one-tap reorder mapping: a favorite's full modifier set must survive
    the trip into a solo-order payload, or the Task 27 advantage is fiction."""

    def drive(self, script: str) -> dict:
        with tempfile.TemporaryDirectory() as scratch:
            driver = Path(scratch) / "driver.js"
            driver.write_text(f"""
var window = this;
load({str(SOLO_JS)!r});
function report(value) {{ print(JSON.stringify(value)); }}
{script}
""", encoding="utf-8")
            done = subprocess.run(
                [ENGINE, str(driver)], capture_output=True, text=True, timeout=30)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        lines = [line for line in done.stdout.splitlines() if line.startswith("{")]
        self.assertTrue(lines, done.stdout + done.stderr)
        return json.loads(lines[-1])

    def test_quick_payload_keeps_the_complete_modifier_set(self):
        result = self.drive("""
var favorite = {
  drink: 'Taro Slush', size: 'Large', sugar: '50%', ice: 'Less Ice', milk: '',
  temperature: '', toppings: ['Boba', 'Pudding'], quantity: 2, notes: 'extra cold'
};
var payload = BobaSolo.quickPayload(favorite);
report({payload: payload, problem: BobaSolo.validatePayload(payload)});
""")
        self.assertEqual(result["problem"], "")
        self.assertEqual(result["payload"], {
            "drink": "Taro Slush", "size": "Large", "sugar": "50%",
            "ice": "Less Ice", "milk": "", "temperature": "",
            "toppings": ["Boba", "Pudding"], "quantity": 2, "notes": "extra cold",
        })

    def test_quick_payload_does_not_alias_the_favorite_toppings(self):
        result = self.drive("""
var favorite = {drink: 'Tea', toppings: ['Boba']};
var payload = BobaSolo.quickPayload(favorite);
payload.toppings.push('Pudding');
report({favorite: favorite.toppings, payload: payload.toppings});
""")
        self.assertEqual(result["favorite"], ["Boba"])
        self.assertEqual(result["payload"], ["Boba", "Pudding"])

    def test_build_order_payload_carries_every_drink(self):
        result = self.drive("""
var drinks = [
  {drink: 'Taro Slush', size: 'Large', sugar: '', ice: '', milk: '',
   temperature: '', toppings: [], quantity: 1, notes: ''},
  {drink: 'Winter Melon Tea', size: '', sugar: '50%', ice: '', milk: '',
   temperature: '', toppings: ['Boba'], quantity: 2, notes: ''}
];
report(BobaSolo.buildOrderPayload('store-a', 'Mariam', drinks));
""")
        self.assertEqual(result, {
            "restaurant_id": "store-a",
            "person": "Mariam",
            "drinks": [
                {"drink": "Taro Slush", "size": "Large", "sugar": "",
                 "ice": "", "milk": "", "temperature": "", "toppings": [],
                 "quantity": 1, "notes": ""},
                {"drink": "Winter Melon Tea", "size": "", "sugar": "50%",
                 "ice": "", "milk": "", "temperature": "", "toppings": ["Boba"],
                 "quantity": 2, "notes": ""},
            ],
        })

    def test_validate_rejects_what_the_server_would_reject(self):
        result = self.drive("""
report({
  blank: BobaSolo.validatePayload({drink: '  '}),
  zero: BobaSolo.validatePayload({drink: 'Tea', quantity: 0}),
  tooMany: BobaSolo.validatePayload({drink: 'Tea', quantity: 21}),
  words: BobaSolo.validatePayload({drink: 'Tea', quantity: 'lots'}),
  fine: BobaSolo.validatePayload({drink: 'Tea', quantity: 1})
});
""")
        self.assertEqual(result["fine"], "")
        for key in ("blank", "zero", "tooMany", "words"):
            self.assertTrue(result[key], key)
