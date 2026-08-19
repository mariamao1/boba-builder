"""Rows -> exact menu items and exact modifier strings. Task 4.

    python3 -m app.matcher .runs/<id>.json

`app/options.py` got the sheet as far as the store's own words: `Large`, `50%`,
`Boba`. That is as far as anything can get without knowing which drink it is,
because Kung Fu Tea defines its option groups per item (Task 1 §5) — `Large` is
`Large .7` on a Taro Slush, `Large 1` on the next drink along, and the upcharge
is part of the string. This module walks the second arrow:

    "lg"  ->  "Large"  ->  "Large .7"  on this drink, in this group
              options.py   matcher.py

and produces, for each row, a line the cart writer can post verbatim.

WHAT IT DECIDES, AND WHAT IT REFUSES TO
---------------------------------------
It applies a drink name three ways, all of which are the *same name*: exactly as
written, the same name spelled differently ("Wintermelon Lemonade"), or an alias
from `data/mapping.json`. It will not pick between two drinks. A near miss comes
back as suggestions on the row and the preview asks a person, because every
other axis is a modifier costing pennies and the drink costs a whole drink.

For modifiers it is the opposite: it decides, and says what it decided.

* **not offered here** — "Large" on a drink sold in one size, `Espresso Shot` on
  a drink that doesn't take it. Reported with what that item *does* have, and
  dropped rather than guessed at — unless the group is required, where dropping
  it would only move the rejection to the store, so the item's own default goes
  in and the row says which and why.
* **already the default** — 31 Bay Ridge items have no `Regular Sugar 100%` and
  66 have no `Regular Ice`. Asking for those is not an impossible request, it is
  the recipe: send no modifier (Task 1 §5). Logged, not warned about.
* **required, and nobody said** — `Choose A Size` is `min 1` on 136 items and 9
  items must pick an included topping, so leaving them out is a rejected order,
  not a default. The item's own default is used, or the store's usual from the
  mapping file, and the row says so.

Nothing here is fatal, and nothing here is silent. A row that can't be matched
still reaches the preview with its reasons attached, and the preview can fix any
of it in place — see `importer.apply_row_edit`.
"""

from __future__ import annotations

import datetime as _dt
import json
import sys

from . import mapping, menu as menu_module
from .schema import Issue

#: Row-level outcomes.
READY = "ready"            # a complete line, ready to post
NEEDS_DRINK = "needs_drink"  # we know nothing to order; a person must choose
SKIPPED = "skipped"        # the import already called this row unorderable

#: Codes this module owns, so re-matching replaces its notes instead of stacking
#: them. Everything it writes starts with this prefix.
PREFIX = "match:"


def _join(names, conjunction: str = "and", limit: int = 4) -> str:
    names = list(names)
    if not names:
        return "nothing"
    if len(names) > limit:
        return f"{', '.join(names[:limit])} and {len(names) - limit} more"
    if len(names) == 1:
        return names[0]
    return f"{', '.join(names[:-1])} {conjunction} {names[-1]}"


class Chosen:
    """One modifier's outcome on one item."""

    __slots__ = ("group", "option", "axis", "asked", "quantity", "status",
                 "message", "choices")

    OK = "ok"                  # asked for, and this item has it
    DEFAULTED = "defaulted"    # nobody asked; the group is required so we picked
    IS_DEFAULT = "is_default"  # asked for, and it's the recipe — send nothing
    NOT_HERE = "not_here"      # this item doesn't offer it
    NO_GROUP = "no_group"      # this item has no such axis at all

    def __init__(self, axis: str, group=None, option: dict | None = None,
                 quantity: int = 1, status: str = OK, message: str = "",
                 choices: list[str] | None = None, asked: str = ""):
        self.axis = axis
        self.group = group
        self.option = option
        # What the row asked for, so the page can offer to replace exactly that
        # one — a row can ask for four toppings and only trip on the third.
        self.asked = asked
        self.quantity = quantity
        self.status = status
        self.message = message
        self.choices = choices or []

    def as_option(self) -> dict | None:
        """The line-item entry, keyed the way the cart API addresses it.

        Task 1 §6: options carry no ids. Both the group and the option are
        referenced by their exact display name, which is why the group travels
        with the option and not just the axis.
        """
        if not self.option or not self.group:
            return None
        return {
            "group": self.group.group_name,
            "axis": self.axis,
            "name": self.option["name"],
            "quantity": self.quantity,
            "price": round((self.option.get("price") or 0) * self.quantity, 2),
        }


#: How loudly to say each thing. Nothing the matcher finds is an error: the
#: import decided long ago which rows are unorderable, and a modifier this store
#: can't do is a drink somebody still gets.
_LEVEL = {Chosen.NOT_HERE: "warning", Chosen.NO_GROUP: "info",
          Chosen.IS_DEFAULT: "info", Chosen.DEFAULTED: "info"}

#: "<drink> <this> — it's <what it has>". One per axis because "doesn't come in
#: Large" and "doesn't do 0% sugar" are both the plain way to say it.
_NOT_HERE = {
    "size": "doesn't come in {label}",
    "sugar": "doesn't do {label} sugar",
    "ice": "doesn't do {label}",
    "milk": "doesn't do a milk alternative",
    "toppings": "doesn't take {label}",
}


class RowMatcher:
    """One row against one menu. Split out so the rules read in one place."""

    def __init__(self, store: menu_module.StoreMenu, config: mapping.Mapping,
                 columns: dict | None = None):
        self.store = store
        self.config = config
        # Which fields the sheet actually had a column for. A blank Size cell in
        # a sheet with a Size column is worth a word; a whole missing column was
        # already reported once at the top by the importer.
        self.columns = {key.lower() for key in (columns or {})}

    # -- the drink ------------------------------------------------------------

    def find_item(self, row: dict) -> tuple[menu_module.MenuItem | None, str, list[str]]:
        """(item, how it was found, what to offer instead).

        The already-resolved name first, then what the sheet said — so the same
        three tiers apply whether or not options.py got there first, and a row
        somebody corrected by hand looks up the name they picked.
        """
        canonical = (row.get("canonical") or {}).get("drink") or ""
        typed = row.get("drink") or ""
        chosen = any((issue.get("code") or "") == "edited:drink"
                     for issue in row.get("issues") or [])

        for text in (canonical, typed):
            if not text:
                continue
            found = self.store.find(text)
            if found:
                return found.item, "chosen" if chosen else found.how, []
            if found.how == menu_module.Match.AMBIGUOUS:
                return None, found.how, found.choices
        return None, menu_module.Match.NONE, self.store.suggestions(typed)

    # -- the modifiers --------------------------------------------------------

    def choose(self, item: menu_module.MenuItem, axis: str, label: str) -> Chosen:
        """A canonical label -> the exact option on this item, or a reason why not."""
        groups = item.groups_for(axis)
        required = [group for group in groups if group.required]

        if not groups:
            if label:
                return Chosen(axis, status=Chosen.NO_GROUP, asked=label,
                              message=f"{item.name} has no {_axis_word(axis)} choice, so "
                                      f"\"{label}\" wasn't sent")
            return Chosen(axis, status=Chosen.NO_GROUP)

        if label:
            found = item.literal(axis, label)
            if found:
                group, option = found
                return Chosen(axis, group, option)

            offered = item.labels(axis)
            phrase = _NOT_HERE.get(axis, "doesn't take {label}").format(label=label)
            fallback = self._fallback(required, axis)
            if fallback:
                # The group is required, so "send nothing" isn't on the table —
                # a line missing a required group is a rejected line. Something
                # gets ordered and the row says loudly what and why.
                group, option = fallback
                instead = group.label_for(option["name"])
                rest = [name for name in offered if name != instead]
                message = (f"{item.name} {phrase} — it only comes {instead}, so that's "
                           f"what's ordered") if not rest else (
                    f"{item.name} {phrase} — ordered {instead} for now; it also does "
                    f"{_join(rest, 'or')}")
                return Chosen(axis, group, option, status=Chosen.NOT_HERE,
                              choices=offered, asked=label, message=message)
            if self._is_the_recipe(axis, label):
                # Task 1 §5: no Regular Ice option means regular ice is what you
                # get. Sending nothing is the correct order, not a compromise.
                return Chosen(axis, status=Chosen.IS_DEFAULT,
                              message=f"{label.lower()} is how {item.name} comes — "
                                      f"no {_axis_word(axis)} sent")
            return Chosen(axis, status=Chosen.NOT_HERE, choices=offered, asked=label,
                          message=f"{item.name} {phrase} — it's {_join(offered, 'or')}")

        fallback = self._fallback(required, axis)
        if fallback:
            group, option = fallback
            asked = axis in self.columns
            return Chosen(axis, group, option, status=Chosen.DEFAULTED,
                          message=(f"no {_axis_word(axis)} on this row, so {item.name} "
                                   f"is ordered {group.label_for(option['name'])}")
                                  if asked else "")
        return Chosen(axis)

    def _fallback(self, required: list, axis: str):
        """(group, option) for the first required group that can fill itself in."""
        for group in required:
            option = group.default(self.config.required_fallback.get(axis))
            if option is not None:
                return group, option
        return None

    def _is_the_recipe(self, axis: str, label: str) -> bool:
        return label.lower() in self.config.assumed_defaults.get(axis, ())

    def choose_toppings(self, item: menu_module.MenuItem, row: dict) -> list[Chosen]:
        canonical = row.get("canonical") or {}
        wanted = list(canonical.get("toppings") or [])
        counts = canonical.get("topping_quantities") or {}
        offered = item.options("toppings")
        chosen: list[Chosen] = []

        for name in wanted:
            found = item.literal("toppings", name)
            if not found:
                chosen.append(Chosen("toppings", status=Chosen.NOT_HERE, choices=offered,
                                     asked=name,
                                     message=f"{item.name} doesn't take {name}"
                                             if offered else
                                             f"{item.name} doesn't take toppings at all"))
                continue
            group, option = found
            count = int(counts.get(name) or 1)
            if count > 1 and not group.allows_quantity:
                chosen.append(Chosen("toppings", group, option, 1,
                                     message=f"{item.name} only takes one {name}"))
                continue
            chosen.append(Chosen("toppings", group, option, count))

        # An "Included Topping" group is min 1: the drink is defined by it, so a
        # sheet that didn't mention it still has to say something.
        for group in item.groups_for("toppings"):
            if not group.required:
                continue
            if any(pick.group is group for pick in chosen):
                continue
            option = group.default()
            if option is None:
                continue
            chosen.append(Chosen("toppings", group, option, status=Chosen.DEFAULTED,
                                 message=f"{item.name} comes with {option['name']}"))
        return chosen

    # -- the row --------------------------------------------------------------

    def run(self, row: dict) -> tuple[dict, list[Issue]]:
        number = row.get("row_number")
        issues: list[Issue] = []

        def note(chosen: Chosen, field: str) -> None:
            level = _LEVEL.get(chosen.status)
            if level and chosen.message:
                issues.append(Issue(level, chosen.message, field, number,
                                    f"{PREFIX}{chosen.status}:{field}"))

        if not row.get("ok", True):
            return {"status": SKIPPED}, issues

        item, how, offer = self.find_item(row)
        if item is None:
            typed = (row.get("drink") or "").strip()
            gone = self.store.unorderable(typed)
            message = ""
            if how == menu_module.Match.AMBIGUOUS:
                message = f"\"{typed}\" is two different drinks here — {_join(offer, 'or')}"
            elif gone is not None:
                message = (f"{gone.name} is sold out today" if gone.sold_out
                           else f"{gone.name} isn't something this can order")
                offer = offer or self.store.suggestions(typed)
            elif not _already_said(row, "drink"):
                # options.py says "no drink called X — did you mean Y?" while
                # resolving the row. Saying it again in different words would
                # only make the preview look like two things went wrong.
                message = f"no drink called \"{typed}\" on this store's menu"
                if offer:
                    message += f" — did you mean {_join(offer[:2], 'or')}?"
            if message:
                issues.append(Issue("warning", message, "drink", number,
                                    f"{PREFIX}no-item"))
            return {"status": NEEDS_DRINK, "choices": {"drink": offer}}, issues

        canonical = row.get("canonical") or {}
        picks = [self.choose(item, "size", canonical.get("size") or ""),
                 self.choose(item, "sugar", canonical.get("sugar") or ""),
                 self.choose(item, "ice", canonical.get("ice") or ""),
                 self.choose(item, "milk", canonical.get("milk") or "")]
        for pick in picks:
            note(pick, pick.axis)
        toppings = self.choose_toppings(item, row)
        for pick in toppings:
            note(pick, "toppings")
        picks += toppings

        line_options = [entry for entry in
                        (pick.as_option() for pick in picks) if entry]
        quantity = int(row.get("quantity") or 1)
        unit = float(item.base_price or 0)
        extras = round(sum(entry["price"] for entry in line_options), 2)

        # Two different bad outcomes, and the page shows them differently: one
        # is a decision waiting to be made, the other is a request this drink
        # simply can't take, with nothing to choose between.
        unmapped = [{"axis": pick.axis, "asked": pick.asked, "why": pick.message}
                    for pick in picks if pick.status == Chosen.NOT_HERE]
        dropped = [{"axis": pick.axis, "asked": pick.asked, "why": pick.message}
                   for pick in picks if pick.status == Chosen.NO_GROUP and pick.asked]
        choices = {pick.axis: pick.choices for pick in picks if pick.choices}

        return {
            "status": READY,
            "matched_by": how,
            "item": item.as_dict(),
            "price_tier": item.price_tier,
            "quantity": quantity,
            "options": line_options,
            "unit_price": round(unit, 2),
            "extras_price": extras,
            "total": round((unit + extras) * quantity, 2),
            "unmapped": unmapped,
            "dropped": dropped,
            "choices": choices,
            "available": self.available(item),
        }, issues

    def available(self, item: menu_module.MenuItem) -> dict:
        """Everything this one drink can be ordered with, per axis.

        `choices` is only the axes that went wrong. This is the whole list, so
        the review screen can offer a dropdown of what is actually possible for
        this drink rather than the store's vocabulary as a whole — the two are
        very different (a slush has no ice level at all).
        """
        return {
            "size": item.labels("size"),
            "sugar": item.labels("sugar"),
            "ice": item.labels("ice"),
            "milk": item.labels("milk"),
            "toppings": item.options("toppings"),
        }


def _already_said(row: dict, field: str) -> bool:
    """Has app/options.py already flagged this field on this row?"""
    return any(str(issue.get("code") or "").startswith("option:")
               and issue.get("field") == field
               for issue in row.get("issues") or [])


def _axis_word(axis: str) -> str:
    return {"size": "size", "sugar": "sugar level", "ice": "ice level",
            "milk": "milk alternative", "toppings": "topping"}.get(axis, axis)


# --- the pipeline stage ------------------------------------------------------


def match(run: dict, store: menu_module.StoreMenu | None = None) -> dict:
    """Add a menu match to every row of a saved import. The Task 4 stage.

    Pure: it reads the run and the menu snapshot and returns a new run. Running
    it twice on the same input gives the same answer — its own notes are cleared
    first — so the preview can re-derive it on every read rather than storing a
    result that goes stale the moment somebody corrects a row.
    """
    # `is None`, not `or`: an empty menu is falsy and must stay the one we were
    # handed, so "there is no snapshot" is reported rather than papered over.
    store = menu_module.store_menu() if store is None else store
    config = store.config
    matcher = RowMatcher(store, config, run.get("column_map"))

    result = dict(run)
    rows: list[dict] = []
    # Our own notes go first, so re-matching replaces them rather than stacking
    # them up. options.py's "N drinks didn't match by name" goes too: it is the
    # same fact counted without the menu in hand, and this stage recounts it.
    issues = [issue for issue in run.get("issues") or []
              if not str(issue.get("code") or "").startswith(PREFIX)
              and issue.get("code") != "option:unmatched-drinks"]

    if not store:
        rows = [dict(row, match={"status": SKIPPED}) for row in run.get("rows") or []]
        issues.append(Issue("warning", "there's no menu snapshot for this store, so "
                                       "nothing could be matched to it — run "
                                       "scripts/fetch_menu.py", None, None,
                            f"{PREFIX}no-menu").as_dict())
        result["rows"] = rows
        result["issues"] = issues
        result["match"] = {"ready": 0, "needs_drink": 0, "skipped": len(rows)}
        return result

    ready = attention = 0
    drinks = 0
    subtotal = 0.0
    for row in run.get("rows") or []:
        row = dict(row)
        row["issues"] = [issue for issue in row.get("issues") or []
                         if not str(issue.get("code") or "").startswith(PREFIX)]
        found, row_issues = matcher.run(row)
        row["issues"] += [issue.as_dict() for issue in row_issues]
        row["match"] = found
        if found["status"] == READY:
            ready += 1
            drinks += found["quantity"]
            subtotal += found["total"]
            if found["unmapped"]:
                attention += 1
        elif found["status"] == NEEDS_DRINK:
            attention += 1
        rows.append(row)

    unmatched = sum(1 for row in rows if row["match"]["status"] == NEEDS_DRINK)
    if unmatched:
        issues.append(Issue(
            "warning",
            f"{unmatched} drink{'s' if unmatched != 1 else ''} still need"
            f"{'' if unmatched != 1 else 's'} picking from the menu before the cart "
            f"can be built", "drink", None, f"{PREFIX}unmatched").as_dict())

    captured = menu_module.captured_at()
    age = (_dt.date.today() - captured).days if captured else None
    if age is not None and age > config.menu_stale_days:
        # Task 1 §6: options are addressed by name, so a rename breaks the cart
        # quietly. An old snapshot is the one way this stage is wrong without
        # noticing.
        issues.append(Issue(
            "info", f"this menu was captured {age} days ago — re-run "
                    f"scripts/fetch_menu.py if the cart starts rejecting options",
            None, None, f"{PREFIX}stale-menu").as_dict())

    result["rows"] = rows
    result["issues"] = issues
    result["match"] = {
        "store": store.store,
        "restaurant_id": store.restaurant_id,
        "menu_items": len(store.drinks),
        "menu_captured": captured.isoformat() if captured else None,
        # What the whole store offers, for a row whose drink isn't picked yet.
        # Once it is, the row's own `match.available` is the narrower truth.
        "vocabulary": store.vocabulary(),
        "ready": ready,
        "needs_drink": unmatched,
        "skipped": sum(1 for row in rows if row["match"]["status"] == SKIPPED),
        "needs_attention": attention,
        "drinks": drinks,
        "subtotal": round(subtotal, 2),
    }
    return result


def choices_for(item_name: str, axis: str) -> list[str]:
    """What one drink can be ordered with, for the preview's fix-it controls."""
    store = menu_module.store_menu()
    found = store.find(item_name)
    if not found:
        return []
    return found.item.options(axis) if axis == "toppings" else found.item.labels(axis)


def _report(run: dict) -> str:
    lines = [f"{run['match'].get('store')} — {run['match']['ready']} of "
             f"{len(run.get('rows') or [])} rows matched, "
             f"${run['match']['subtotal']:.2f} before tax", ""]
    for row in run.get("rows") or []:
        found = row.get("match") or {}
        if found.get("status") != READY:
            lines.append(f"  row {row['row_number']:>3}  [{found.get('status')}]  "
                         f"{row.get('drink') or '(no drink)'}")
            continue
        modifiers = ", ".join(
            f"{entry['name']}{'' if entry['quantity'] == 1 else ' x%d' % entry['quantity']}"
            for entry in found["options"])
        lines.append(f"  row {row['row_number']:>3}  {found['item']['name']} "
                     f"x{found['quantity']}  ${found['total']:.2f}")
        if modifiers:
            lines.append(f"            {modifiers}")
    return "\n".join(lines)


if __name__ == "__main__":  # python3 -m app.matcher .runs/<id>.json
    if len(sys.argv) < 2:
        print(__doc__.strip().splitlines()[2], file=sys.stderr)
        sys.exit(2)
    with open(sys.argv[1], encoding="utf-8") as handle:
        print(_report(match(json.load(handle))))
