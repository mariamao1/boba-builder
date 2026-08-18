# Task 2 — Import & Upload page

The front door. Someone drops in the spreadsheet their group filled out, we read
it, show them what we got, and hand a normalised order list to Tasks 3–4.

    python3 -m app.server            # http://127.0.0.1:8000
    python3 -m unittest discover -s tests -t .

No dependencies, no build step, nothing to install.

---

## Why stdlib

Same wall Task 1 hit, re-checked at the start of this task: there is no `pip` on
this machine (`No module named pip.__main__`) and no `node`/`npm`. Flask,
FastAPI, openpyxl and pandas are all unavailable. So:

* the server is `http.server.ThreadingHTTPServer`,
* the `.xlsx` reader is `zipfile` + `ElementTree` (`app/xlsx.py`) — an xlsx is a
  zip of XML, and the subset needed to read an order list is about 200 lines,
* the front end is plain HTML/CSS/JS, served as static files.

For a tool one person runs on their laptop to build one cart, this is not a
compromise; it also means Task 5 can be demoed on any machine with Python.

## Shape

```
app/
  server.py     routes, multipart parsing, JSON API
  importer.py   bytes/link -> normalised rows      <- the data path
  xlsx.py       minimal .xlsx reader
  sheets.py     Google Sheets link -> CSV bytes
  schema.py     column mapping, cleaning, validation
  template.py   template CSV + menu hints, built from Task 1's snapshot
  runs.py       one JSON file per import, under .runs/
  pipeline.py   the seam where Tasks 3-4 plug in
  static/       index.html, preview.html, app.css, app.js, preview.js
tests/          42 tests: unit + real-HTTP end to end
```

Routes:

| Route | What |
| --- | --- |
| `GET /` | the upload page |
| `GET /preview/<run_id>` | check what we read, then continue |
| `GET /template.csv` | the template, with real menu items in it |
| `POST /api/import` | file upload, or `{"sheet_url": …}` |
| `GET /api/runs/<id>` | the parsed order as JSON |
| `POST /api/runs/<id>/process` | push it into the pipeline |
| `GET /api/menu-hints` | drinks/sizes/sugars for the page's "what can I type" |

## The handoff to Tasks 3–4

`app/pipeline.py` holds the contract in full; the short version is that a run is

```json
{"run_id": "…", "source": {…}, "column_map": {…}, "stats": {…}, "issues": [],
 "rows": [{"row_number": 2, "person": "Alice", "drink": "Taro Slush",
           "size": "Large", "sugar": "50%", "ice": "Less ice",
           "toppings": ["Boba"], "milk": "", "temperature": "", "quantity": 1,
           "notes": "", "extra": {"Venmo": "@alice"}, "issues": [], "ok": true}]}
```

To plug in, add a module — nothing in the web layer needs to change:

* Task 3: `app/matcher.py` with `match(run: dict) -> dict`
* Task 4: `app/cart.py` with `build(matched: dict) -> dict`

`pipeline.status()` notices them and the preview page's stage list turns green.
Until then `POST /process` returns `202` with which stage is missing, and the
page says so plainly instead of pretending. You can also run a saved import from
the command line: `python3 -m app.pipeline .runs/<id>.json`.

**Values are the user's own words, deliberately.** Task 2 does not resolve
"Large" to `"Large .7"`. Per Task 1 §5, the size/sugar/ice vocabularies are per
*item* — Bay Ridge alone has 16 distinct size literals collapsing to 4 labels —
so "Large" cannot be resolved until the drink is matched. That is Task 3's job,
and `data/menu-*.json` already carries a per-item `canonical` map for it.

## Being forgiving

Every one of these is covered by a test:

* **Any header row.** The header does not have to be row 1 — we scan the first
  15 rows and pick the one that looks most like a header, so title rows, blank
  rows and instruction blocks above the table are fine.
* **Any header wording.** `Name` / `Who's it for?` / `Naem`; `Drink` /
  `What drink do you want?` / `Beverage`. Matching is exact-synonym, then
  substring (Google Forms turns questions into headers), then transposed-letter,
  then close-match. `Timestamp`, `Email Address` and `Paid?` are recognised as
  *not* order columns and skipped.
* **Only Name and Drink are required.** Missing optional columns produce a note,
  not a failure.
* **Unknown columns are kept**, not dropped, in `row.extra`.
* **Values in the wrong column are put right.** Toppings typed into the Ice
  column move to Toppings; Sugar and Ice filled in the wrong order are swapped
  back; an ice level sitting in Toppings moves out. Each move is reported on the
  row, and if a whole column is mislabelled it is summarised once at the top
  ("your Ice column held toppings on 3 rows"). The classifier checks toppings
  first, so `Ice Cream` and `Brown Sugar Wow Boba` are read as toppings and not
  as ice or sugar; genuinely ambiguous values (`Less`, `regular`) are left where
  the user put them, because the column is the only clue there is. If both
  columns are filled and they clash, we flag it and change nothing — nothing is
  ever silently dropped or overwritten.
* **Quantities** from a `Qty` column, or from `Taro Slush x2` / `2x Taro Slush` /
  `Taro Slush (2)` in the drink cell.
* **Toppings** split on `,` `;` `/` `|` `+` `&` `and`; `none`/`n/a`/`-` become
  empty. `None` in an *Ice* column is left alone — there it is a real choice.
* **Encodings and delimiters** sniffed: UTF-8, BOM, cp1252, latin-1; comma, tab,
  semicolon, pipe.
* **Multi-tab workbooks**: we pick the tab that looks like the order list and say
  which ones we ignored.
* **Row-level, not file-level, failure.** A row with no drink is flagged and
  skipped; the other seven still go through. A row with no name is a warning —
  it can still be ordered, it just won't be labelled.
* **Formats we can't read** get told what to do instead: `.xls` → "Save As
  .xlsx or .csv"; Numbers/ODS → "export as .xlsx"; a Drive link → "open it in
  Sheets and copy the link from there"; a private sheet → the exact Share
  setting to change.

Caps: 8 MB, 400 orders, 50 stored runs (they contain people's names, so old ones
are deleted rather than kept).

## Notes on the two riskier bits

**Google Sheets links.** Google exports any sheet as CSV, so a link is just a
fetch of `…/export?format=csv&gid=…`. `/d/e/2PACX…` published links need the
`/pub?output=csv` endpoint instead; both are handled. A sheet that isn't shared
answers **200 with an HTML sign-in page** rather than an error, so the HTML is
sniffed and turned into "set General access to Anyone with the link".

**This endpoint fetches a URL the browser supplies**, so it is an SSRF surface.
Only `docs.google.com` is allowed, before the request and again after redirects.
The server binds to `127.0.0.1` by default.

## Open, and not ours to close

Task 1 flagged one unverified thing and asked Task 2 to do it first: nobody has
actually *clicked* a `?order_id=` handoff link. It is still unverified. Chrome
will not start on this machine — the same `bootstrap_check_in … Permission
denied (1100)` Mach-port block, re-tested at the start of this task. The harness
(`scripts/verify_handoff.py`) is still the way to close it, on any machine where
Chrome runs. It does not block the import page.
