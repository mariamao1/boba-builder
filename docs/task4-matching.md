# Task 4 — Menu Mapping & Normalization

Turning the parsed sheet into the store's exact menu items and the exact
modifier strings those items accept.

    python3 -m unittest discover -s tests -t .   # 224 tests
    python3 -m app.mapping                       # does the vocabulary still fit the menu?
    python3 -m app.matcher .runs/<id>.json       # match a saved import from the CLI
    python3 -m app.server                        # then drop data/sample-group-order.csv on the page

---

## The one constraint that shapes all of it

Task 1 §5: **Kung Fu Tea defines its option groups per item, not per store.**
The upcharge is baked into the option name, so one logical size has sixteen
literal spellings at Bay Ridge and which one is correct depends on the drink.

    "lg"  ->  "Large"  ->  "Large .7"   on a Taro Slush
                          "Large 1"     on the next drink along
                          "Large.3"     on another
    app/options.py (Task 3)  app/matcher.py (this task)

Task 3 stopped at the middle column, because that is as far as anything can get
before the drink is known. This task walks the second arrow, and it can only do
that with the menu in hand — which is also why it is the stage that finally
knows the price, the item id, and whether this particular drink has an ice level
at all.

## What was added

| | |
|---|---|
| `data/mapping.json` | every synonym, alias and policy, as data |
| `app/mapping.py` | loads it, merges an overlay, and checks it against the menu |
| `app/menu.py` | the menu as objects: item lookup, per-item option vocabularies |
| `app/matcher.py` | `match(run) -> run` — the pipeline stage |

`app/options.py` kept its job and lost its hardcoded tables; they moved to
`data/mapping.json` and it reads them from there. `app/schema.py` is still
menu-agnostic. Nothing else changed shape.

## Three levels of a value, and which one to use

```json
"drink": "  thai tea milk cap ",             // the sheet, verbatim
"canonical": { "drink": "Thai Tea Milk Cap", "size": "Large" },
"match": {
  "item": { "id": "65949f...", "name": "Thai Tea Milk Cap", "price": 6.30 },
  "price_tier": "Regular",
  "options": [
    { "group": "Choose A Size", "axis": "size", "name": "Large .5",
      "quantity": 1, "price": 0.5 }
  ]
}
```

`match.options` is the postable one. Each entry carries its **group name**
because the cart addresses options by display name and has no ids for them
(Task 1 §6) — `options[Choose A Size][0][name]=Large .5`. `price_tier` goes in
the request's top-level `size` field; posting the size *option* there is a 400.
Full contract in `app/pipeline.py`.

## Matching a drink: three tiers, and a hard stop

| Tier | Example | Applied? |
|---|---|---|
| exact | `Taro Slush` | yes |
| same name, spelled differently | `wintermelon lemonade` → `Winter Melon Lemonade` | yes |
| an alias in `mapping.json` | `classic milk tea` → `KF Milk Tea` | yes |
| anything looser | `Wintermelon Lemonaid` | **no — offered** |

All three applied tiers are the *same name*: case, spacing, punctuation, or a
name a human wrote down in the config on purpose. Below that line the matcher
stops and asks, because a wrong modifier costs pennies and a wrong drink costs a
whole drink. Near misses come back as `match.choices.drink` and the preview
turns them into one-click buttons.

Two menu facts handled here rather than by whoever hits them next:

* **Names aren't unique.** 14 Bay Ridge names map to two items each
  (cross-listings into promotional categories, identical price and options).
  The tie breaks on the lowest id, so the same sheet builds the same cart twice
  running.
* **Not everything on the menu is a drink.** Gift cards are menu items here.
  They're excluded by `drinks.exclude` in the config, and a sold-out item gets
  "is sold out today" rather than "no drink called that".

## Matching a modifier: it decides, and says what it decided

The opposite policy, because these are cheap and reversible.

| Outcome | When | Level | Fixable in the UI |
|---|---|---|---|
| matched | the item has it | — | — |
| **not offered here** | `Large` on a drink sold only `Cold` | warning | yes — its own options as buttons |
| **already the recipe** | `Regular Ice` on one of the 66 items with no such option | info | nothing to fix |
| **no such axis** | `Less Ice` on a slush | info | nothing to fix |
| **required, unstated** | no size on any of the 136 items where size is `min 1` | info | — |

An unhonourable request on a **required** group is the one place those two
columns cross: dropping it would produce a line with no size, which the store
rejects, so the item's default goes in anyway and the warning says which —
*"Osmanthus Oolong doesn't come in Large — it only comes Cold, so that's what's
ordered."* On an optional group the same request sends nothing at all, because
there the honest answer is available.

The split between warning and info is deliberate: **warn when there is a
decision to make, inform when there isn't.** A drink that offers `70%` and `50%`
but not the `0%` somebody asked for is a question for a person, and the page
shows the two real answers. A slush with no ice level is not a question — the
row says so and the value is struck through in the preview so nobody thinks it
went into the cart.

Three of these deserve their own note:

* **"Already the recipe" is not an unavailable option.** 31 items have no
  `Regular Sugar 100%` and 66 have no `Regular Ice`. Per Task 1 §5, that means
  regular *is* what you get; the correct order sends no modifier. Treating it as
  a failure would put a warning on a perfectly ordinary drink.
* **Required groups are filled in, not left out.** `Choose A Size` is `min 1` on
  136 items, 9 items have a required `Included Topping` (`Mango Slush` must say
  Mango Jelly or No Mango Jelly), and 4 items require sugar *and* ice. Omitting
  those is a rejected order, not a default. The item's own `is_default` wins;
  failing that the store's usual from `matching.required_group_fallback`;
  failing that the cheapest option — and never a `No ...` removal option while
  anything else exists, because defaulting to "without" changes the drink.
* **Topping counts are spent here.** Task 3 read `2x pudding` as `Pudding` and
  said the count belonged to whoever wrote the cart. It rides along on
  `canonical.topping_quantities` and becomes `quantity: 2` on the option — but
  only where the group sets `quantities: true`, and only for a written number or
  `double`/`triple`. "Extra boba" stays at one: `extra` is also half of
  `extra shot`, and guessing there charges somebody for a topping.

## Fixing things in the page

Two ways in, one door. Anything unmappable is a button where it went wrong —
*"Winter Melon Tea doesn't do 0% sugar"* with `100% / 70% / 50% / 30%` beside
it. And **Review & edit**, at the top of the order, turns the whole table into
controls for the times when nothing is wrong and somebody just changed their
mind:

| Column | Control |
|---|---|
| Name | text box |
| Drink | search box, typeahead over the menu (`/api/drinks`, server-ranked) |
| Size / Sugar / Ice / Milk | dropdown of **what that drink offers**, plus "store default" |
| Toppings | one removable chip each, plus a typeahead to add more |
| Qty | − / + |

The dropdowns are the point: they come from `match.available`, which is the
matched item's own vocabulary, so a Taro Slush offers Medium and Large and says
*"not on this drink"* where the ice would be. A row whose drink nobody has
picked yet falls back to `match.vocabulary` — everything the store sells —
and narrows the moment a drink is chosen. A value the sheet asked for that the
drink hasn't got stays selected and labelled *"— not on this drink"* rather than
the dropdown silently snapping to something nobody chose.

Every control posts the same thing:

    POST /api/runs/<run_id>/rows/<n>
      {"drink": "Winter Melon Lemonade"}      pick the drink
      {"sugar": "30%"}                        pick a level this drink has
      {"toppings": ["Boba", "2x Pudding"]}    the whole list, counts included
      {"milk": ""}                            leave it out, take the default
      {"quantity": 3}                         1..20; 0 and "lots" are refused
      {"person": "Kim"}                       and the "no name" warning goes

`importer.apply_row_edit()` handles all of them: it records what changed
(`sugar level set to "30%" — the sheet said "no sugar"`), re-resolves the row
through `options.py`, and the match is re-derived on the way out. A correction
cannot produce a row the importer couldn't have.

Two details that are easy to get wrong and are tested:

* **Topping counts round-trip.** The chips send `2x Pudding` back, not
  `Pudding`, so adding Boba to a row doesn't quietly halve somebody's pudding.
* **Editing a field clears what the import said about it** — "no drink in this
  row", "no name, so this drink will be unlabelled", "read a quantity of 2 from
  the drink name" — but keeps notes *about the sheet* ("moved this from Ice to
  Toppings"), which are still true.

Every save redraws the page from the server's answer rather than patching the
cell, because one change moves the line price, the subtotal, the row's notes and
sometimes a sheet-level warning.

Which makes **where the focus lands afterwards** a design question, and the
first answer was wrong: the page remembered the last control you had focused and
restored it after any save, so clicking `+` handed focus to whichever search box
you had touched before — and a focused typeahead drops its list open. Focus is
now asked for per save and never remembered. The `+`/`−` buttons and the
dropdowns keep their own, so you can click `+` three times without hunting for
it; the text boxes ask for nothing, because they commit on blur and grabbing the
caret back would fight the Tab that got you out of them.

`tests/test_preview_js.py` drives the real `preview.js` through a DOM shim to
hold that down — 17 tests over what each control saves and what it focuses. It
needs a JavaScript engine on the machine and skips when there isn't one.

**The match is derived on every read, never stored.** `GET /api/runs/<id>` runs
`pipeline.enrich()`, which runs the stages marked *pure* — read-only, no network,
same input same output. That is the only way the page can't show a match that
disagrees with the rows underneath it. The cart stage is not pure, so it runs
only when somebody presses the button.

## Keeping it alive through a menu change

Task 1 §6 is blunt about the risk: options are addressed by their exact display
name, so a rename breaks the cart silently. Three things guard that.

**One, the vocabulary is data.** `data/mapping.json` holds the synonyms, the
milk alternatives this store doesn't carry, the topping families too vague to
guess between, the drink aliases, the group-name → axis routing, and the
per-axis policy. No option string is hardcoded in Python. A second store with a
`Tajin Option` group is a config edit.

    BOBA_MAPPING=/path/to/mine.json     merged over the shipped file:
                                        objects update key by key, lists replace

**Two, it is checkable.** Half of that file names real menu strings, so:

```
$ python3 -m app.mapping
mapping: data/mapping.json  (version 1)
menu:    5th Ave, Bk [8625 5th Ave] — 149 items

    18  size words
    ...
every mapping target still exists on this store's menu.
```

Anything that has been renamed away is listed, and the exit code is non-zero.
`MappingHealthTests` runs the same check, so a snapshot refresh that strands
`"oreo" -> "OREO®"` fails the test suite instead of quietly failing to match.

**Three, the snapshot's age is visible.** The match block carries
`menu_captured`, the preview prints it, and past `matching.menu_stale_days` the
run picks up a note telling you to re-run `scripts/fetch_menu.py`.

## The sample order, end to end

`data/sample-group-order.csv`, 9 rows, against the real Bay Ridge menu:

```
5th Ave, Bk [8625 5th Ave] — 7 of 9 rows matched, $53.60 before tax

  row   5  Taro Slush x1  $7.40
            Large .7, Half S 50%, Boba
  row   6  Thai Tea Milk Cap x1  $8.30
            Large .5, Half S 50%, No Ice, OREO®, Pudding
  ...
  row  10  [needs_drink]  Wintermelon Lemonaid
  row  11  Oolong Milk Tea x1  $7.50
            Medium, Less S 70%, Pudding x2, Red Bean
  row  12  [skipped]  (no drink)
```

Note `Large .7` on row 5 and `Large .5` on row 6 — the same word in the
spreadsheet, two different strings on the wire. Row 10 is waiting on a person;
row 12 was unorderable before this stage ever saw it.

## One correction to Task 1 §5

> "Sugar and ice are `min: 0`. Omitting them is legal and means store default."

True of 131 and 97 items respectively — but **not all of them**. Four items
(`White Peach Oolong`, `Ceylon`, `Osmanthus Oolong`, `Coconut Creamy Latte` —
the same four that are the only `Cold`-size drinks) have `min: 1` on both. On
those, omitting the sugar or ice group is a rejected line, not a default.

Nothing here reads that from a rule: `required` comes off each group in the
snapshot, per item, so a store where a different four are required needs no
change. It is called out because the generalisation is in Task 1's write-up and
in `docs/task3-parsing.md`, and somebody will otherwise trust it.

## Judgement calls worth arguing with

* **`Wintermelon Lemonade` now resolves on its own**, where Task 3 offered it as
  a suggestion. Spacing is not a judgement call — there is exactly one item it
  can be — and the row records "read X as Y" either way. The sample file was
  changed to a real misspelling (`Lemonaid`) so it still exercises the
  did-you-mean path, which is now the only thing that path is for.
* **A drink that resolves to two different names is refused, not ranked.** If
  `squash()` collides on two genuinely different menu items, that's ambiguous
  and a person picks.
* **Prices are shown, and labelled as estimates.** They come from the snapshot,
  before tax and fees. The real number comes from `GET .../quote` in Task 5;
  showing nothing until then would be less useful than showing this with a
  caveat, and 111 of 114 shared items differ in price between stores, so the
  number is worth sanity-checking before anyone pays.
* **The matcher deletes one of `options.py`'s sheet-level notes.** "N drinks
  didn't match by name" is the same fact counted without the menu in hand; this
  stage recounts it properly. Two notes saying one thing reads as two problems.

## Caveats

* The menu is the **committed snapshot**, not a live fetch. Task 1 recommends
  re-fetching per run; that belongs with the cart stage, which needs a live
  `can_order` anyway. Until then the staleness note is the mitigation.
* Synonyms and aliases are hand-written from how people fill these sheets in.
  They will miss things. A miss is a flagged row, not a wrong order.
* `drinks.aliases` is the one place a human judgement is baked into config —
  `classic milk tea → KF Milk Tea` is a claim about this store, and a different
  store's file should say something else. Deliberately short for that reason.
* Prices assume one price tier. All 154 Bay Ridge items have exactly one
  (`Regular`), and the item's own `default_price_tier` is carried through rather
  than assumed, but nothing here has been tested against a multi-tier store.
