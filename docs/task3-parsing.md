# Task 3 — Spreadsheet Import & Parsing

Turning the uploaded sheet into structured order data: one object per row, in
the words the store actually uses.

    python3 -m unittest discover -s tests -t .     # 137 tests
    python3 -m app.server                          # then drop data/sample-group-order.csv on the page

---

## What was already here

The import page (`docs/task2-import-page.md`) had built most of the *structural*
half of this task ahead of time: format sniffing, encodings and delimiters, the
`.xlsx` reader, Google Sheets fetching, header detection, column mapping,
topping splitting, inline quantities, and row-level error reporting. That work
stands, and this task did not redo it.

What it deliberately did **not** do was look at the values. `row.size` came out
as `"LG"`, `row.sugar` as `"half sweet"`, and resolving those was left to
whatever came next. That is the gap this task closes, in a new module:

    app/options.py     the user's words -> this store's option set

`app/schema.py` stays menu-agnostic and structural; `app/options.py` owns the
vocabulary; `importer.parse_and_resolve()` runs the two in order so every entry
point — upload, Google Sheet, JSON — produces identically shaped output.

## Two levels of resolution, and why we stop at the first

Task 1 §5 is the constraint that shapes this whole module. Option *literals* are
per item, not per store:

    user text  ->  store canonical label  ->  that item's literal
    "lg"           "Large"                   "Large .7" | "Large 1" | "Large.3"

Bay Ridge has 16 distinct size literals that collapse to four labels, and which
literal is correct depends on the drink — the upcharge is baked into the option
name. So the second arrow cannot be walked until the drink is matched, and this
module resolves to the canonical label and stops. The matcher walks the rest
using each item's own `canonical` map in `data/menu-*.json`.

Sugar is the exception worth naming. The store's names are inconsistent English
(`Regular Sugar 100%` but `Less S 70%`), so per Task 1 we canonicalise to the
**percentage** and let the matcher find the option carrying it.

Confirmed against the snapshot, and matching Task 1's numbers exactly:

| Axis | Canonical form | Values |
|---|---|---|
| size | label | Medium, Large, Hot, Cold |
| sugar | percentage | 0%, 30%, 50%, 70%, 100%, 120% |
| ice | the store's own name | No Ice, Less Ice, Regular Ice, More Ice |
| toppings | the store's own name | 31 of them, `OREO®` included |
| milk | the store's own name | Soy Milk, and only Soy Milk |

## The shape

Raw values are never overwritten. The resolved ones land alongside, in
`row.canonical`:

```json
{
  "row_number": 6, "person": "bob", "quantity": 1,
  "drink": "thai tea milk cap", "size": "LG", "sugar": "half sweet",
  "ice": "No Ice", "toppings": ["oreo", "pudding"], "milk": "",
  "canonical": {
    "drink": "Thai Tea Milk Cap", "size": "Large", "sugar": "50%",
    "ice": "No Ice", "toppings": ["OREO®", "Pudding"], "milk": ""
  },
  "issues": [], "ok": true
}
```

Two fields per value because a guess should always be recoverable: if the
canonicalisation is wrong, the cart builder and the person reading the preview
can both still see what was actually typed. `app/pipeline.py` documents the full
contract.

**An empty canonical value means "no choice made, use the store default."** It
never means the row is broken. Sugar and ice are `min: 0` on every item, 66 Bay
Ridge items have no `Regular Ice` option at all, and 31 have no
`Regular Sugar 100%` — so for a lot of drinks the correct order is to send no
modifier, and an empty string is that instruction.

## What gets flagged, and what doesn't

Nothing this module finds is fatal. A drink nobody can identify still goes to
the preview; only a row with **no drink at all** is an error, and that rule
predates this task. The four things worth reporting:

* **unknown** — `25%` sugar, a size called "gigantic". Reported with the list of
  what the store does have, and *not* snapped to the nearest value. Silently
  turning 25% into 30% changes someone's order without telling them.
* **unavailable** — oat milk. We know exactly what they asked for and this store
  only has Soy Milk, so we say that rather than shrugging.
* **ambiguous** — "popping boba", when the store sells Strawberry, Coffee and
  Mango. Guessing would put something in the cart that nobody ordered.
* **assumed** — "small" → Medium (this store has no small), "puding" → Pudding,
  `hot` in a Hot/Iced column → the Hot size. Logged at info level: we did
  something helpful, and we're saying so.

The preview shows the resolved value with the original underneath, a dash where
no choice was made, and an underline on anything we couldn't place.

### Judgement calls

* **Drinks are never auto-corrected — they're offered.** A near miss produces
  suggestions (`"Wintermelon Lemonade"` → *did you mean Winter Melon Lemonade?*)
  but `canonical.drink` stays empty until somebody says yes. Every other axis is
  a modifier and costs pennies; picking the wrong *drink* costs a whole drink, so
  a person makes that call, not a fuzzy string match. See "Fixing a drink" below.
* **`iced` is not the `Cold` size.** Only 4 items have a Cold option; iced drinks
  are iced by default. An `Iced` temperature resolves to nothing, which is the
  correct iced order. `Hot`, per Task 1, really is a size.
* **`No Mango Jelly` never wins a fuzzy match.** Removal options are real and
  orderable, so they stay in the vocabulary, but matching "mango jelly" to
  "No Mango Jelly" would invert the request. They're excluded from fuzzy
  matching only.
* **Topping counts are stripped, not honoured.** `2x pudding` resolves to
  `Pudding`. Options carry their own quantity field (Task 1 §6) and the cart
  writer owns it; dropping the count silently would be wrong, so it isn't
  dropped — it's just not this stage's to spend.

## Fixing a drink

A row whose drink isn't on the menu — or that never had one — is the one thing
the parser genuinely cannot finish on its own, so the preview makes it fixable
in place rather than sending someone back to the spreadsheet.

Each such row gets the near misses as one-click buttons, plus a search box over
the store's menu. Picking either applies the change and redraws:

    POST /api/runs/<run_id>/rows/<n>   {"drink": "Winter Melon Lemonade"}
    GET  /api/drinks?q=taro&limit=6    the type-ahead behind the search box

`importer.apply_row_edit()` puts the row back through the same resolution the
import used, so a correction cannot produce a row the importer couldn't have.
It also re-resolves the row's *other* values and recomputes the sheet-level
tally — fixing the last unmatched drink makes the "1 drink didn't match" note
disappear, and filling in a blank row clears its error and updates the totals.
The correction is recorded on the row (`drink set to "X" — the sheet said "Y"`),
so the preview never quietly disagrees with the spreadsheet.

Two ranking functions, because they answer different questions:

| | Input | Ranks by |
|---|---|---|
| `drink_suggestions()` | a finished cell | character ratio, then shared whole words |
| `search_drinks()` | half-typed text | exact → prefix → substring → word-initials → ratio |

The second exists because no similarity score ranks `Taro Slush` highly against
`ta`. Both cap their output — the box offers a handful of near matches, not all
135 drinks.

## The sample file

`data/sample-group-order.csv` is a realistic group order — title rows above the
header, a Google Forms question as a column name, mixed casing and stray
whitespace, an inline `x2`, a missing name, a missing drink, a Venmo column
nobody asked for, and four things the store can't do. It reads as 9 rows / 9
drinks / 7 people, with 1 unorderable row flagged and kept.

`SampleFileTests` parses it, and also rebuilds it as a real `.xlsx` in memory to
prove both paths give byte-identical structured output.

## Tests

137, up from 49. The new ones are in `tests/test_options.py`, mostly against a
stub vocabulary so they say what they mean and don't move when the snapshot is
refreshed; `StoreVocabularyTests` and `SampleFileTests` run against the real
snapshot.

## Caveats

* The vocabulary comes from the **committed snapshot**, so it is Bay Ridge's.
  Point it at another store and the tables rebuild themselves from that store's
  menu — but per Task 1 the snapshot should be re-fetched per run rather than
  pinned, because options are addressed by name and a rename breaks the cart.
* Synonym tables are hand-written from how people actually fill these sheets in.
  They will miss things. A miss is a flagged row, not a wrong order, which is
  the failure mode to have.
* `annotate()` resolves against the store vocabulary as a whole, not per item, so
  it will happily canonicalise `More Ice` for a drink that has no ice group. The
  matcher drops modifiers the item doesn't offer — it has the per-item data and
  this stage does not.
