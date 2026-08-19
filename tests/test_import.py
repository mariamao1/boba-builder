"""Task 2 tests: the import path, end to end and in pieces.

    python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import datetime
import io
import json
import sys
import threading
import unittest
import urllib.error
import urllib.request
import uuid
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import importer, schema, server, sheets, template, xlsx  # noqa: E402
from tests.xlsx_fixture import build_xlsx  # noqa: E402

TIDY_CSV = (
    "Name,Drink,Size,Sugar,Ice,Toppings,Milk,Notes\n"
    "Alice,Taro Slush,Large,50%,Less ice,Boba,,\n"
    "Bob,Thai Tea Milk Cap,Medium,100%,Regular ice,,,extra cold\n"
)

MESSY_CSV = (
    "Friday boba run!!,,,,\n"
    ",,,,\n"
    "Timestamp,Who's it for?,What drink do you want?,Sugar Level,Ice level,Venmo\n"
    "8/14/2026 9:02,alice ,Taro Slush x2,half sweet,less,@alice\n"
    ",,,,,\n"
    "8/14/2026 9:05,,Winter Melon Tea,,no ice,\n"
    "8/14/2026 9:07,Chen,,30%,,\n"
)


class HeaderMappingTests(unittest.TestCase):
    def test_exact_and_fuzzy_headers(self):
        self.assertEqual(schema.match_header("Name"), "person")
        self.assertEqual(schema.match_header("  SUGAR  LEVEL "), "sugar")
        self.assertEqual(schema.match_header("Who's it for?"), "person")
        self.assertEqual(schema.match_header("What drink do you want?"), "drink")
        self.assertEqual(schema.match_header("Toppings / add-ons"), "toppings")
        self.assertEqual(schema.match_header("Naem"), "person")  # typo

    def test_non_order_headers_are_ignored(self):
        for header in ("Timestamp", "Email Address", "Paid?", "Total"):
            self.assertIsNone(schema.match_header(header), header)

    def test_header_row_found_below_junk(self):
        rows = [["Friday boba run!!"], [], ["Name", "Drink", "Size"], ["Alice", "Taro Slush", "L"]]
        index, mapping = schema.find_header_row(rows)
        self.assertEqual(index, 2)
        self.assertEqual(mapping, {0: "person", 1: "drink", 2: "size"})

    def test_no_header_row(self):
        index, mapping = schema.find_header_row([["a", "b"], ["c", "d"]])
        self.assertEqual((index, mapping), (-1, {}))


class ValueCleaningTests(unittest.TestCase):
    def test_topping_splitting(self):
        self.assertEqual(schema.split_toppings("Boba, Pudding"), ["Boba", "Pudding"])
        self.assertEqual(schema.split_toppings("Boba and Aloe / Red Bean"),
                         ["Boba", "Aloe", "Red Bean"])
        self.assertEqual(schema.split_toppings("Brown Sugar Wow Boba"), ["Brown Sugar Wow Boba"])
        for empty in ("", "none", "N/A", "no toppings", "-"):
            self.assertEqual(schema.split_toppings(empty), [], empty)

    def test_inline_quantity(self):
        cases = {
            "Taro Slush x2": ("Taro Slush", 2),
            "Taro Slush X2": ("Taro Slush", 2),
            "2x Taro Slush": ("Taro Slush", 2),
            "Taro Slush (2)": ("Taro Slush", 2),
            "Taro Slush": ("Taro Slush", None),
            "Slush x30": ("Slush x30", None),  # out of range, left alone
        }
        for text, expected in cases.items():
            self.assertEqual(schema.extract_inline_quantity(text), expected, text)

    def test_quantity_words(self):
        self.assertEqual(schema.parse_quantity("two"), 2)
        self.assertEqual(schema.parse_quantity("3 drinks"), 3)
        self.assertIsNone(schema.parse_quantity(""))

    def test_ice_none_is_a_real_choice(self):
        # "none" means No Ice, and must not be blanked the way toppings are.
        rows = [["Name", "Drink", "Ice"], ["Alice", "Taro Slush", "None"]]
        parsed, _map, _issues = schema.parse_table(rows)
        self.assertEqual(parsed[0].ice, "None")


class MisplacedValueTests(unittest.TestCase):
    """Values typed into the wrong column — the commonest way a sheet is messy."""

    def parse(self, csv_text):
        return importer.import_bytes(csv_text.encode(), "orders.csv")

    def test_classifier(self):
        cases = {
            "Boba": "toppings",
            "Brown Sugar Wow Boba": "toppings",   # "sugar" in a topping name
            "Ice Cream": "toppings",              # "ice" in a topping name
            "less ice": "ice",
            "Half S 50%": "sugar",
            "half sweet": "sugar",
            "Large": "size",
            "Hot": "size",
            "Soy Milk": "milk",
            "Less": None,        # could be ice or sugar; leave it alone
            "regular": None,
            "extra cold": None,  # a note about ice, not the Cold size
        }
        for value, expected in cases.items():
            self.assertEqual(schema.classify_token(value), expected, value)

    def test_toppings_in_the_ice_column_move(self):
        result = self.parse("Name,Drink,Ice,Toppings\n"
                            "Alice,Taro Slush,Boba,\n"
                            'Bob,Taro Slush,"boba, pudding",\n')
        self.assertEqual(result.rows[0].toppings, ["Boba"])
        self.assertEqual(result.rows[0].ice, "")
        self.assertEqual(result.rows[1].toppings, ["boba", "pudding"])
        self.assertTrue(any(i.code == "moved:ice>toppings" for i in result.rows[0].issues))

    def test_a_whole_mislabelled_column_is_summarised_once(self):
        result = self.parse("Name,Drink,Ice\n"
                            "Alice,Taro Slush,Boba\n"
                            "Bob,Taro Slush,Pudding\n"
                            "Chen,Taro Slush,Red Bean\n")
        summary = [i for i in result.issues if i.code == "moved:ice>toppings"]
        self.assertEqual(len(summary), 1)
        self.assertIn("on 3 rows", summary[0].message)

    def test_swapped_columns_are_swapped_back(self):
        result = self.parse("Name,Drink,Sugar,Ice\nChen,Taro Slush,Less Ice,30%\n")
        row = result.rows[0]
        self.assertEqual((row.sugar, row.ice), ("30%", "Less Ice"))
        self.assertTrue(any(i.code == "swapped:ice>sugar" for i in row.issues))

    def test_ice_value_in_the_toppings_column_moves_back(self):
        result = self.parse("Name,Drink,Ice,Toppings\nAlice,Taro Slush,,\"Pudding, no ice\"\n")
        row = result.rows[0]
        self.assertEqual(row.toppings, ["Pudding"])
        self.assertEqual(row.ice, "no ice")

    def test_a_clash_is_flagged_not_overwritten(self):
        result = self.parse("Name,Drink,Ice,Toppings\n"
                            'Dana,Taro Slush,No Ice,"Pudding, less ice"\n')
        row = result.rows[0]
        self.assertEqual(row.ice, "No Ice")           # what they put in the Ice column wins
        self.assertIn("less ice", row.toppings)       # and nothing is silently dropped
        self.assertTrue(any(i.level == "warning" for i in row.issues))

    def test_a_correct_sheet_is_left_completely_alone(self):
        result = self.parse(
            "Name,Drink,Size,Sugar,Ice,Toppings,Milk\n"
            "Alice,Taro Slush,Large,Half S 50%,Less Ice,Brown Sugar Wow Boba,\n"
            "Bob,Thai Tea Milk Cap,Medium,100%,No Ice,Boba,Soy Milk\n")
        for row in result.rows:
            self.assertEqual([i.message for i in row.issues], [], row.person)
        self.assertEqual(result.rows[0].toppings, ["Brown Sugar Wow Boba"])
        self.assertEqual(result.rows[1].milk, "Soy Milk")


class CsvImportTests(unittest.TestCase):
    def test_tidy_csv(self):
        result = importer.import_bytes(TIDY_CSV.encode(), "orders.csv")
        self.assertTrue(result.ok, result.issues)
        self.assertEqual(result.stats["drinks"], 2)
        self.assertEqual(result.stats["people"], 2)
        first = result.rows[0]
        self.assertEqual((first.person, first.drink, first.size), ("Alice", "Taro Slush", "Large"))
        self.assertEqual(first.toppings, ["Boba"])
        self.assertEqual(first.row_number, 2)
        self.assertEqual(result.rows[1].notes, "extra cold")

    def test_messy_csv_survives(self):
        result = importer.import_bytes(MESSY_CSV.encode(), "orders.csv")
        self.assertTrue(result.rows)
        alice, nameless, chen = result.rows

        self.assertEqual(alice.person, "alice")
        self.assertEqual(alice.drink, "Taro Slush")
        self.assertEqual(alice.quantity, 2)
        self.assertEqual(alice.sugar, "half sweet")  # left verbatim for Task 3
        self.assertEqual(alice.extra.get("Venmo"), "@alice")

        self.assertTrue(nameless.ok)  # usable, just unlabelled
        self.assertTrue(any(i.field == "person" for i in nameless.issues))

        self.assertFalse(chen.ok)  # no drink
        self.assertEqual(result.stats["drinks"], 3)  # 2 for alice + 1 unnamed

    def test_blank_rows_are_dropped(self):
        result = importer.import_bytes(MESSY_CSV.encode(), "orders.csv")
        self.assertEqual(len(result.rows), 3)

    def test_missing_required_column_is_fatal(self):
        data = b"Sugar,Ice\n50%,less\n"
        result = importer.import_bytes(data, "orders.csv")
        self.assertFalse(result.ok)
        self.assertTrue(any(i.level == "error" for i in result.issues))

    def test_semicolon_and_tab_delimiters(self):
        semi = b"Name;Drink;Size\nAlice;Taro Slush;Large\nBob;Milk Tea;Medium\n"
        tabbed = b"Name\tDrink\tSize\nAlice\tTaro Slush\tLarge\nBob\tMilk Tea\tMedium\n"
        for data in (semi, tabbed):
            result = importer.import_bytes(data, "orders.csv")
            self.assertTrue(result.ok, result.issues)
            self.assertEqual(result.rows[0].drink, "Taro Slush")

    def test_cp1252_and_bom(self):
        result = importer.import_bytes(
            "Name,Drink\nRené,Taro Slush\n".encode("cp1252"), "orders.csv")
        self.assertEqual(result.rows[0].person, "René")
        result = importer.import_bytes(
            "﻿Name,Drink\nAlice,Taro Slush\n".encode("utf-8-sig"), "orders.csv")
        self.assertEqual(result.rows[0].person, "Alice")

    def test_legacy_xls_is_rejected_with_advice(self):
        data = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 64
        with self.assertRaises(importer.UnreadableFile) as caught:
            importer.import_bytes(data, "orders.xls")
        self.assertIn("Save As", str(caught.exception))

    def test_empty_file(self):
        with self.assertRaises(importer.UnreadableFile):
            importer.import_bytes(b"", "orders.csv")

    def test_row_cap(self):
        rows = "Name,Drink\n" + "".join(f"P{i},Taro Slush\n" for i in range(schema.MAX_ROWS + 5))
        result = importer.import_bytes(rows.encode(), "orders.csv")
        self.assertEqual(len(result.rows), schema.MAX_ROWS)
        self.assertTrue(any("were ignored" in i.message for i in result.issues))


class XlsxTests(unittest.TestCase):
    def test_inline_strings_and_numbers(self):
        data = build_xlsx({"Sheet1": [
            ["Name", "Drink", "Qty"],
            ["Alice", "Taro Slush", 2],
        ]})
        result = importer.import_bytes(data, "orders.xlsx")
        self.assertTrue(result.ok, result.issues)
        self.assertEqual(result.rows[0].quantity, 2)
        self.assertEqual(result.source["format"], "xlsx")

    def test_shared_strings_and_dates(self):
        data = build_xlsx(
            {"Sheet1": [
                [("shared", 0), ("shared", 1), ("shared", 2)],
                [("date", 46234), ("shared", 3), ("shared", 4)],
            ]},
            shared_strings=["Timestamp", "Name", "Drink", "Alice", "Taro Slush"])
        worksheets = xlsx.read_sheets(data)
        self.assertEqual(worksheets[0][1][0][0], "Timestamp")
        self.assertIsInstance(worksheets[0][1][1][0], datetime.date)

        result = importer.import_bytes(data, "orders.xlsx")
        self.assertEqual(result.rows[0].person, "Alice")

    def test_sparse_cells_keep_their_column(self):
        # A row where B is empty: the reader must not shift C left.
        data = build_xlsx({"Sheet1": [["Name", "Size", "Drink"], ["Alice", "", "Taro Slush"]]})
        result = importer.import_bytes(data, "orders.xlsx")
        self.assertEqual(result.rows[0].drink, "Taro Slush")
        self.assertEqual(result.rows[0].size, "")

    def test_picks_the_orders_tab(self):
        data = build_xlsx({
            "Instructions": [["Fill in the Orders tab"], ["one row each"]],
            "Orders": [["Name", "Drink"], ["Alice", "Taro Slush"]],
        })
        result = importer.import_bytes(data, "orders.xlsx")
        self.assertEqual(result.source["sheet"], "Orders")
        self.assertEqual(result.rows[0].drink, "Taro Slush")
        self.assertTrue(any("Instructions" in i.message for i in result.issues))

    def test_not_a_workbook(self):
        with self.assertRaises(importer.UnreadableFile):
            importer.import_bytes(b"PK\x03\x04not really a zip", "orders.xlsx")


class SheetLinkTests(unittest.TestCase):
    def test_edit_link(self):
        self.assertEqual(
            sheets.export_url("https://docs.google.com/spreadsheets/d/ABC123/edit#gid=77"),
            "https://docs.google.com/spreadsheets/d/ABC123/export?format=csv&gid=77")

    def test_sharing_link_defaults_to_first_tab(self):
        self.assertEqual(
            sheets.export_url("https://docs.google.com/spreadsheets/d/ABC123/edit?usp=sharing"),
            "https://docs.google.com/spreadsheets/d/ABC123/export?format=csv&gid=0")

    def test_published_link_uses_pub_endpoint(self):
        url = sheets.export_url("https://docs.google.com/spreadsheets/d/e/2PACX-xyz/pubhtml")
        self.assertIn("/d/e/2PACX-xyz/pub?output=csv", url)

    def test_bare_id_and_missing_scheme(self):
        self.assertIn("/d/1BxiMVs0XRA5nFMdKvBd/export",
                      sheets.export_url("1BxiMVs0XRA5nFMdKvBd"))
        self.assertIn("/export", sheets.export_url("docs.google.com/spreadsheets/d/ABC/edit"))

    def test_rejects_non_google_hosts(self):
        for bad in ("https://example.com/orders.csv", "http://127.0.0.1:8000/secret",
                    "file:///etc/passwd", "https://drive.google.com/file/d/ABC/view"):
            with self.assertRaises(sheets.SheetError, msg=bad):
                sheets.export_url(bad)

    def test_empty_link(self):
        with self.assertRaises(sheets.SheetError):
            sheets.export_url("   ")


class TemplateTests(unittest.TestCase):
    def test_template_imports_cleanly(self):
        """The template we hand out must survive our own importer."""
        result = importer.import_bytes(template.template_csv().encode(), "template.csv")
        self.assertTrue(result.ok, result.issues)
        self.assertEqual(result.stats["rows"], 3)
        self.assertTrue(all(row.ok for row in result.rows))
        self.assertEqual([row.person for row in result.rows], ["Alice", "Bob", "Chen"])

    def test_template_examples_are_real_menu_items(self):
        hints = template.menu_hints()
        if not hints.get("store"):
            self.skipTest("no menu snapshot in data/")
        result = importer.import_bytes(template.template_csv().encode(), "template.csv")
        for row in result.rows:
            self.assertIn(row.drink, hints["drinks"])

    def test_menu_hints_shape(self):
        hints = template.menu_hints()
        for key in ("drinks", "sizes", "sugar", "ice", "toppings"):
            self.assertTrue(hints[key], key)


class MultipartTests(unittest.TestCase):
    def test_file_and_text_fields(self):
        boundary = "----x"
        body = (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="file"; filename="orders.csv"\r\n'
            "Content-Type: text/csv\r\n\r\n"
            "Name,Drink\r\nAlice,Taro Slush\r\n"
            f"\r\n--{boundary}\r\n"
            'Content-Disposition: form-data; name="sheet_url"\r\n\r\n'
            "https://example.com\r\n"
            f"--{boundary}--\r\n"
        ).encode()
        fields = server.parse_multipart(body, f"multipart/form-data; boundary={boundary}")
        self.assertEqual(fields["file"]["filename"], "orders.csv")
        self.assertEqual(fields["file"]["value"], b"Name,Drink\r\nAlice,Taro Slush\r\n")
        self.assertEqual(fields["sheet_url"]["value"], b"https://example.com")

    def test_binary_payload_is_not_mangled(self):
        blob = build_xlsx({"Sheet1": [["Name", "Drink"], ["Alice", "Taro Slush"]]})
        boundary = "----y"
        body = (f"--{boundary}\r\n"
                'Content-Disposition: form-data; name="file"; filename="o.xlsx"\r\n\r\n'
                ).encode() + blob + f"\r\n--{boundary}--\r\n".encode()
        fields = server.parse_multipart(body, f"multipart/form-data; boundary={boundary}")
        self.assertEqual(fields["file"]["value"], blob)


class ServerTests(unittest.TestCase):
    """End to end over real HTTP, on a throwaway port."""

    @classmethod
    def setUpClass(cls):
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.base = f"http://127.0.0.1:{cls.httpd.server_address[1]}"
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def get(self, path):
        with urllib.request.urlopen(self.base + path, timeout=10) as response:
            return response.status, response.read(), response.headers

    def post_file(self, name, data):
        boundary = uuid.uuid4().hex
        body = (f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="file"; filename="{name}"\r\n\r\n'
                ).encode() + data + f"\r\n--{boundary}--\r\n".encode()
        request = urllib.request.Request(
            self.base + "/api/import", data=body, method="POST",
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def test_pages_and_assets_serve(self):
        for path in ("/", "/static/app.css", "/static/app.js", "/static/preview.js",
                     "/favicon.ico", "/preview/abc123"):
            status, body, _headers = self.get(path)
            self.assertEqual(status, 200, path)
            self.assertTrue(body, path)

    def test_template_download(self):
        status, body, headers = self.get("/template.csv")
        self.assertEqual(status, 200)
        self.assertIn("attachment", headers["Content-Disposition"])
        self.assertTrue(body.decode().startswith("Name,Drink,"))

    def test_upload_creates_a_run(self):
        status, payload = self.post_file("orders.csv", TIDY_CSV.encode())
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        run_id = payload["run_id"]
        self.assertEqual(payload["preview_url"], f"/preview/{run_id}")

        status, body, _headers = self.get(f"/api/runs/{run_id}")
        self.assertEqual(status, 200)
        run = json.loads(body)["run"]
        self.assertEqual(run["stats"]["drinks"], 2)
        self.assertEqual(run["rows"][0]["person"], "Alice")

    def test_the_api_hands_over_resolved_options_and_flagged_rows(self):
        """What the preview page and the cart builder actually read."""
        messy = ("Name,Drink,Size,Sugar,Ice,Toppings,Milk\n"
                 "Alice, taro slush ,LG,half sweet,no ice,oreo,\n"
                 "Bob,Taro Slush,medium,25%,less,,oat milk\n")
        status, payload = self.post_file("orders.csv", messy.encode())
        self.assertEqual(status, 200)
        rows = payload["run"]["rows"]

        self.assertEqual(rows[0]["canonical"], {
            "drink": "Taro Slush", "size": "Large", "sugar": "50%",
            "ice": "No Ice", "toppings": ["OREO®"], "milk": "",
        })
        self.assertEqual(rows[0]["drink"], "taro slush")  # raw text survives too

        # Bob's two impossible values are reported on the row, and he is kept.
        self.assertTrue(rows[1]["ok"])
        messages = " ".join(issue["message"] for issue in rows[1]["issues"])
        self.assertIn("25%", messages)
        self.assertIn("oat milk", messages)

    def post_json(self, path, payload):
        request = urllib.request.Request(
            self.base + path, data=json.dumps(payload).encode(), method="POST",
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def test_correcting_a_drink_from_a_suggestion(self):
        sheet = ("Name,Drink,Size\n"
                 "Alice,Taro Slushie,large\n"
                 "Bob,,medium\n")
        _status, payload = self.post_file("orders.csv", sheet.encode())
        run_id = payload["run_id"]
        alice, bob = payload["run"]["rows"]

        # Unmatched: suggested, not chosen for them. Blank: nothing to suggest.
        self.assertEqual(alice["canonical"]["drink"], "")
        self.assertIn("Taro Slush", alice["suggestions"]["drink"])
        self.assertEqual(bob["suggestions"], {})
        self.assertFalse(bob["ok"])

        status, out = self.post_json(
            f"/api/runs/{run_id}/rows/{alice['row_number']}", {"drink": "Taro Slush"})
        self.assertEqual(status, 200)
        self.assertEqual(out["run"]["rows"][0]["canonical"]["drink"], "Taro Slush")

        # Filling in the blank row clears the error, and it all persists.
        status, out = self.post_json(
            f"/api/runs/{run_id}/rows/{bob['row_number']}", {"drink": "Matcha Milk"})
        self.assertEqual(status, 200)
        self.assertEqual(out["run"]["stats"]["errors"], 0)

        _status, body, _headers = self.get(f"/api/runs/{run_id}")
        saved = json.loads(body)["run"]
        self.assertEqual([r["canonical"]["drink"] for r in saved["rows"]],
                         ["Taro Slush", "Matcha Milk"])

    def test_drink_search_returns_a_short_list(self):
        status, body, _headers = self.get("/api/drinks?q=taro")
        self.assertEqual(status, 200)
        drinks = json.loads(body)["drinks"]
        self.assertIn("Taro Slush", drinks)
        self.assertLessEqual(len(drinks), 6)

        _status, body, _headers = self.get("/api/drinks?q=tea&limit=3")
        self.assertEqual(len(json.loads(body)["drinks"]), 3)

        for path in ("/api/drinks", "/api/drinks?q=", "/api/drinks?q=xyzzy",
                     "/api/drinks?q=tea&limit=notanumber"):
            _status, body, _headers = self.get(path)
            self.assertIsInstance(json.loads(body)["drinks"], list, path)

    def test_a_correction_to_a_row_or_run_that_isnt_there(self):
        _status, payload = self.post_file("orders.csv", TIDY_CSV.encode())
        run_id = payload["run_id"]
        self.assertEqual(self.post_json(f"/api/runs/{run_id}/rows/999",
                                        {"drink": "Taro Slush"})[0], 404)
        self.assertEqual(self.post_json(f"/api/runs/{run_id}/rows/2", {})[0], 400)
        self.assertEqual(self.post_json("/api/runs/deadbeef/rows/2",
                                        {"drink": "Taro Slush"})[0], 404)

    def test_bad_upload_reports_the_reason(self):
        status, payload = self.post_file("orders.csv", b"Sugar,Ice\n50%,less\n")
        self.assertEqual(status, 200)  # the import ran; the sheet is the problem
        self.assertFalse(payload["ok"])
        self.assertTrue(any("missing required column" in issue["message"]
                            for issue in payload["run"]["issues"]))

    def test_unreadable_upload_is_a_400(self):
        status, payload = self.post_file("orders.xls",
                                         b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 32)
        self.assertEqual(status, 400)
        self.assertIn("Save As", payload["error"])

    def test_process_reports_the_missing_stage(self):
        _status, payload = self.post_file("orders.csv", TIDY_CSV.encode())
        run_id = payload["run_id"]
        request = urllib.request.Request(
            f"{self.base}/api/runs/{run_id}/process", data=b"", method="POST")
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                code, data = response.status, json.loads(response.read())
        except urllib.error.HTTPError as exc:
            code, data = exc.code, json.loads(exc.read())
        # The cart stage isn't built yet, so this is a clean "pending", not an
        # error — and the match that did run comes back with it.
        self.assertIn(code, (200, 202))
        if code == 202:
            self.assertTrue(data["pending"])
            self.assertEqual(data["stage"]["name"], "cart")

    def test_unknown_run(self):
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.get("/api/runs/deadbeef")
        self.assertEqual(caught.exception.code, 404)

    def test_static_path_traversal_is_blocked(self):
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.get("/static/../../app/server.py")
        self.assertEqual(caught.exception.code, 404)

    def test_sheet_link_validation_happens_before_any_fetch(self):
        request = urllib.request.Request(
            self.base + "/api/import",
            data=json.dumps({"sheet_url": "http://127.0.0.1:1/secret"}).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(request, timeout=10)
        self.assertEqual(caught.exception.code, 400)
        self.assertIn("Google Sheets link", json.loads(caught.exception.read())["error"])


if __name__ == "__main__":
    unittest.main()
