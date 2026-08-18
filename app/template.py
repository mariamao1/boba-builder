"""The order template, and the hints the page shows about what to type.

Both are generated from the menu snapshot Task 1 captured rather than typed by
hand, so the example rows are drinks the store actually sells and the suggested
sugar/ice wording matches the real option names. If the snapshot is missing the
page still works — it just falls back to generic examples.
"""

from __future__ import annotations

import io
import csv
import json
from collections import Counter
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
TARGET_STORE = "650c9c3cd73592bc0e0bd50a"  # 5th Ave, Bk — Task 1's target store

COLUMNS = ["Name", "Drink", "Size", "Sugar", "Ice", "Toppings", "Milk", "Notes"]

FALLBACK_ROWS = [
    ["Alice", "Taro Slush", "Large", "50%", "Less ice", "Boba", "", ""],
    ["Bob", "Thai Tea Milk Cap", "Medium", "100%", "Regular ice", "", "", "extra cold"],
    ["Chen", "Winter Melon Tea", "Large", "30%", "No ice", "Boba, Pudding", "Soy milk", ""],
]

_cache: dict | None = None


def _load_menu() -> dict | None:
    global _cache
    if _cache is not None:
        return _cache or None
    path = DATA_DIR / f"menu-{TARGET_STORE}.json"
    if not path.exists():
        candidates = sorted(DATA_DIR.glob("menu-*.json"))
        path = candidates[0] if candidates else None
    if path is None:
        _cache = {}
        return None
    try:
        _cache = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        _cache = {}
        return None
    return _cache


def _options(item: dict, axis: str) -> list[str]:
    for group in item.get("option_groups", []):
        if group.get("axis") == axis:
            return [option["name"] for option in group.get("options", [])
                    if not option.get("is_disabled")]
    return []


def _canonical_labels(item: dict, axis: str) -> list[str]:
    for group in item.get("option_groups", []):
        if group.get("axis") == axis:
            return list(group.get("canonical", {}).keys())
    return []


def menu_hints() -> dict:
    """What to tell the user they can type, drawn from the live snapshot."""
    menu = _load_menu()
    if not menu:
        return {
            "store": None,
            "item_count": 0,
            "drinks": [row[1] for row in FALLBACK_ROWS],
            "sizes": ["Medium", "Large", "Hot"],
            "sugar": ["0%", "30%", "50%", "70%", "100%"],
            "ice": ["No ice", "Less ice", "Regular ice", "More ice"],
            "toppings": ["Boba", "Pudding", "Aloe Vera", "Red Bean"],
            "milk": ["Soy milk"],
        }

    items = [item for item in menu.get("items", [])
             if item.get("available") and not item.get("sold_out")
             and "gift card" not in item.get("name", "").lower()]

    sizes: Counter = Counter()
    sugar: Counter = Counter()
    ice: Counter = Counter()
    toppings: Counter = Counter()
    milk: Counter = Counter()
    for item in items:
        sizes.update(_canonical_labels(item, "size"))
        sugar.update(_options(item, "sugar"))
        ice.update(_options(item, "ice"))
        toppings.update(_options(item, "toppings"))
        milk.update(_options(item, "milk"))

    drinks = sorted({item["name"] for item in items})

    return {
        "store": menu.get("restaurant_name") or (menu.get("store") or {}).get("name"),
        "restaurant_id": menu.get("restaurant_id"),
        "item_count": len(items),
        "captured": menu.get("source", {}).get("fetched_at") if isinstance(
            menu.get("source"), dict) else None,
        "drinks": drinks,
        "sizes": [name for name, _count in sizes.most_common()],
        # Full vocabularies, commonest first. The page trims what it shows; the
        # misplaced-value classifier in schema.py wants all of them.
        "sugar": [name for name, _count in sugar.most_common()],
        "ice": [name for name, _count in ice.most_common()],
        "toppings": [name for name, _count in toppings.most_common()],
        "milk": [name for name, _count in milk.most_common()],
    }


def _prefer(options: list[str], wanted: list[str]) -> str:
    """First option matching a preference, else blank.

    Blank rather than options[0] on purpose: 31 Bay Ridge items have no 100%
    sugar and 66 have no Regular Ice (Task 1, §5), and the template should show
    an empty cell — which means "shop default" — rather than an odd one like
    "Extra S 120%" just because it happened to be listed first.
    """
    for want in wanted:
        for option in options:
            if want.lower() in option.lower():
                return option
    return ""


# Per example row: (size, sugar, ice, topping) preferences. Deliberately ordinary
# choices — the template is read as an example of what to write, so it should not
# show anyone "Protein Add On" as the normal thing to put in the toppings column.
_EXAMPLE_TASTES = [
    (["Large"], ["50%"], ["Less"], ["Boba", "Pudding", "Jelly"]),
    (["Medium"], ["100%", "70%"], ["Regular", "Less"], []),
    (["Large", "Medium"], ["30%"], ["No Ice"], ["Pudding", "Boba", "Jelly"]),
]


def _example_rows() -> list[list[str]]:
    """Three rows using drinks that exist, with options those drinks really have."""
    menu = _load_menu()
    if not menu:
        return FALLBACK_ROWS

    wanted = ["Taro Slush", "Thai Tea Milk Cap", "Classic Milk Tea", "Winter Melon Tea"]
    by_name = {}
    for item in menu.get("items", []):
        if item.get("available") and not item.get("sold_out"):
            by_name.setdefault(item["name"], item)

    picks = [by_name[name] for name in wanted if name in by_name][:3]
    if len(picks) < 3:
        picks += [item for item in by_name.values()
                  if item.get("has_size") and item not in picks][: 3 - len(picks)]
    if not picks:
        return FALLBACK_ROWS

    people = ["Alice", "Bob", "Chen"]
    notes = ["", "extra cold", ""]
    rows = []
    for index, item in enumerate(picks):
        size_want, sugar_want, ice_want, topping_want = _EXAMPLE_TASTES[index]
        rows.append([
            people[index],
            item["name"],
            _prefer(_canonical_labels(item, "size"), size_want),
            _prefer(_options(item, "sugar"), sugar_want),
            _prefer(_options(item, "ice"), ice_want),
            _prefer(_options(item, "toppings"), topping_want) if topping_want else "",
            "",
            notes[index],
        ])
    return rows


def template_csv() -> str:
    """The downloadable template: header, three worked examples, blank rows."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(COLUMNS)
    for row in _example_rows():
        writer.writerow(row)
    for _ in range(12):
        writer.writerow([""] * len(COLUMNS))
    return buffer.getvalue()
